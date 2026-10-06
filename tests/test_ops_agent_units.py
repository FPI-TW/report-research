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
        "development": REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent-dev.service",
    }
    CATALOGS = {"production": "services.prod.toml", "development": "services.dev.toml"}

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
            other = self.CATALOGS["development" if env == "production" else "production"]
            self.assertNotIn(other, unit.read_text(encoding="utf-8"))

    def test_dedicated_user_and_least_privilege(self):
        users = set()
        for unit in self.UNITS.values():
            d = self._directives(unit)
            (user,) = d["User"]
            users.add(user)
            self.assertNotIn(user, ("root", "kashionz"))
            self.assertEqual(d["Group"], [user])
            self.assertEqual(d["SupplementaryGroups"], ["systemd-journal docker"])
            self.assertEqual(d["NoNewPrivileges"], ["yes"])
            self.assertEqual(d["CapabilityBoundingSet"], [""])
            self.assertEqual(d["ProtectSystem"], ["strict"])
            self.assertEqual(d["RestrictAddressFamilies"], ["AF_UNIX"])
            # 不讀任何環境檔（祕密只給需要的 unit）、不設 HOME（tests/test_deploy_units.py 的 HOME 規則）
            self.assertNotIn("EnvironmentFile", d)
            self.assertFalse(any(v.startswith("HOME=") for v in d.get("Environment", [])))
            self.assertNotIn("OnFailure", d)
        self.assertEqual(len(users), 2, "dev 與 prod 代理要用不同的使用者")


if __name__ == "__main__":
    unittest.main()
