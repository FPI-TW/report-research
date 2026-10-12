"""維運代理的 systemd unit（`deploy/systemd/report-mark-ops-agent*.service`）靜態守門。

環境、socket 目錄、catalog 三者要一致（以 dev socket 載入 prod catalog 的那種錯置，代理啟動時會拒絕，
這裡在 unit 檔層面先擋），而且是最小權限：專用使用者、不讀環境檔、不設 HOME、不接告警鏈。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from ops_agent import protocol

REPO_ROOT = Path(__file__).resolve().parents[1]


class DeployUnitTests(unittest.TestCase):
    """`deploy/systemd/report-mark-ops-agent*.service`：環境、socket 目錄、catalog 三者一致，且最小權限。"""

    UNITS = {
        "production": REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent.service",
        "staging": REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent-staging.service",
        "development": REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent-dev.service",
    }
    CATALOGS = {"production": "services.prod.toml", "staging": "services.staging.toml",
                "development": "services.dev.toml"}

    def _directives(self, path: Path) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "[")) or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out.setdefault(key.strip(), []).append(value.strip())
        return out

    def test_runtime_directory_matches_canonical_socket(self):
        for env, unit in self.UNITS.items():
            d = self._directives(unit)
            socket_dir = os.path.dirname(protocol.CANONICAL_SOCKETS[env])
            self.assertEqual(d["RuntimeDirectory"], [socket_dir.removeprefix("/run/")], unit.name)
            self.assertEqual(d["RuntimeDirectoryMode"], ["0750"], unit.name)

    def test_execstart_loads_the_matching_catalog_with_system_python(self):
        for env, unit in self.UNITS.items():
            d = self._directives(unit)
            for key in ("ExecStart", "ExecStartPre"):
                (cmd,) = d[key]
                self.assertTrue(cmd.startswith("/usr/bin/python3 "), cmd)
                self.assertIn(f"--catalog /opt/report-mark-ops/{self.CATALOGS[env]}", cmd)
                self.assertNotIn("--socket", cmd)
                self.assertNotIn(".venv", cmd)
                self.assertNotIn("uv ", cmd)
            self.assertTrue(d["ExecStartPre"][0].endswith("--check"))
            for other_env, other in self.CATALOGS.items():
                if other_env != env:
                    self.assertNotIn(other, unit.read_text(encoding="utf-8"))

    def test_dedicated_user_and_least_privilege(self):
        users = set()
        for unit in self.UNITS.values():
            d = self._directives(unit)
            (user,) = d["User"]
            users.add(user)
            self.assertNotIn(user, ("root", "kashionz"))
            self.assertEqual(d["Group"], [user])
            # staging 沒有任何容器（RDS、apt 的 nginx）：不給等同 root 的 docker 群組。
            groups = ["systemd-journal"] if unit == self.UNITS["staging"] else ["systemd-journal docker"]
            self.assertEqual(d["SupplementaryGroups"], groups, unit.name)
            self.assertEqual(d["NoNewPrivileges"], ["yes"])
            self.assertEqual(d["CapabilityBoundingSet"], [""])
            self.assertEqual(d["ProtectSystem"], ["strict"])
            self.assertEqual(d["RestrictAddressFamilies"], ["AF_UNIX"])
            # 不讀任何環境檔（祕密只給需要的 unit）、不設 HOME（tests/test_deploy_units.py 的 HOME 規則）
            self.assertNotIn("EnvironmentFile", d)
            self.assertFalse(any(v.startswith("HOME=") for v in d.get("Environment", [])))
            self.assertNotIn("OnFailure", d)
        self.assertEqual(len(users), 3, "dev、staging、prod 代理要用不同的使用者")

    def test_staging_catalog_has_no_containers(self):
        """staging 代理沒有 docker 群組：catalog 一出現 container 服務，status 就只會回 docker 的權限錯誤。"""
        from ops_agent.catalog import load_catalog

        catalog = load_catalog(REPO_ROOT / "deploy" / "ops" / "services.staging.toml", resolve_user=lambda _n: 4242)
        self.assertEqual([s.name for s in catalog.services if s.kind == "container"], [])

    def test_lock_files_are_visible_read_only_inside_the_sandbox(self):
        """catalog 的 flock_files／pid_files 所在目錄必須被 BindReadOnlyPaths 綁進來（家目錄其餘部分藏起來）。

        沒綁進來時代理看不到鎖檔，sync 的 run 一律回 lock_unavailable（fail-closed，但功能等於壞了）。
        """
        from ops_agent.catalog import load_catalog

        for env, unit in self.UNITS.items():
            d = self._directives(unit)
            catalog = load_catalog(REPO_ROOT / "deploy" / "ops" / self.CATALOGS[env], resolve_user=lambda _n: 4242)
            binds = [b.lstrip("-") for line in d.get("BindReadOnlyPaths", []) for b in line.split()]
            self.assertNotIn("BindPaths", d, "代理對鎖檔只需要讀")
            self.assertNotIn("ReadWritePaths", d)
            lock_dirs = {os.path.dirname(p) for s in catalog.services for p in (*s.flock_files, *s.pid_files)}
            for lock_dir in lock_dirs:
                self.assertIn(lock_dir, binds, f"{unit.name} 沒有唯讀綁進 {lock_dir}")
            if any(b.startswith("/home/") for b in binds):
                self.assertEqual(d["ProtectHome"], ["tmpfs"], unit.name)
            else:
                self.assertEqual(d["ProtectHome"], ["yes"], unit.name)
        prod = self._directives(self.UNITS["production"])
        self.assertEqual(prod["BindReadOnlyPaths"], ["-/home/kashionz/projects/report-mark/data"])

    def test_dev_smoke_unit_is_inert(self):
        """dev catalog 唯一能 run 的對象：只 sleep、沒網路、沒告警鏈、沒有 timer 與 [Install]。"""
        unit = REPO_ROOT / "deploy" / "systemd" / "report-mark-dev-smoke.service"
        d = self._directives(unit)
        self.assertEqual(d["Type"], ["oneshot"])
        self.assertEqual(d["ExecStart"], ["/usr/bin/sleep 20"])
        self.assertEqual(d["DynamicUser"], ["yes"])
        self.assertEqual(d["PrivateNetwork"], ["yes"])
        self.assertNotIn("OnFailure", d)
        self.assertNotIn("EnvironmentFile", d)
        self.assertNotIn("[Install]", unit.read_text(encoding="utf-8"))
        self.assertFalse((unit.parent / "report-mark-dev-smoke.timer").exists())


if __name__ == "__main__":
    unittest.main()
