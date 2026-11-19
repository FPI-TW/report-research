# tests/test_ops_agent_template.py
"""維運代理的框架範本（`deploy/ops-agent/`）彼此與程式對得上。

守的是安裝說明（docs/CONTAINER_DEPLOYMENT.md「維運代理（選配）」）照做就會動的幾件事：
- catalog 範本以系統的 python3 `--check` 得過；environment／socket 是 production 的固定路徑；只收 web 容器的 UID；
  只列容器、全部唯讀（寫入類只給 systemd unit，範本不列）。
- 容器名與框架 compose 的服務一一對得上（`<專案名>-<服務>-1`，專案名預設 report-mark）。
- unit 讀的 catalog 路徑、執行身分、socket 目錄與 web 端的固定路徑一致；目錄在重啟時保留（web 容器 bind mount 它）。
- polkit 範本的 ALLOW 是合法 JSON、使用者與 unit 的 User= 一致，而且與 catalog 的寫入類 action 一致（範本是空的）。
- compose override 把 socket 目錄掛進 web、以 OPS_AGENT_GID 加入 socket 群組。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import config as app_config  # noqa: E402
from ops_agent import protocol  # noqa: E402
from ops_agent.catalog import load_catalog  # noqa: E402

TEMPLATES = REPO_ROOT / "deploy" / "ops-agent"
CATALOG = TEMPLATES / "services.example.toml"
UNIT = TEMPLATES / "report-mark-ops-agent.service"
RULES = TEMPLATES / "10-report-mark-ops.rules"
OVERRIDE = TEMPLATES / "docker-compose.ops-agent.yml"
COMPOSE = REPO_ROOT / "deploy" / "compose" / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "deploy" / "container" / "Dockerfile"
INSTALLED_CATALOG = "/opt/report-mark-ops/services.toml"


def _unit_values(key: str) -> list[str]:
    return re.findall(rf"^{key}=(.*)$", UNIT.read_text(encoding="utf-8"), re.M)


def _allow() -> dict:
    m = re.search(r"// BEGIN ALLOW\s*var ALLOW = (\{.*?\});\s*// END ALLOW", RULES.read_text(encoding="utf-8"), re.S)
    assert m, "找不到 BEGIN ALLOW／END ALLOW 標記"
    return json.loads(m.group(1))


class CatalogTemplateTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(CATALOG)

    def test_production_binding_and_web_container_uid(self):
        self.assertEqual(self.catalog.environment, "production")
        self.assertEqual(self.catalog.socket_path, protocol.CANONICAL_SOCKETS["production"])
        uid = re.search(r"^ARG APP_UID=(\d+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
        self.assertEqual(self.catalog.agent.allowed_uids, frozenset({int(uid.group(1))}))

    def test_containers_only_and_read_only(self):
        self.assertTrue(self.catalog.services)
        for svc in self.catalog.services:
            with self.subTest(service=svc.name):
                self.assertEqual(svc.kind, "container")
                self.assertFalse(set(svc.actions) & set(protocol.WRITE_ACTIONS))

    def test_container_names_follow_the_framework_compose(self):
        compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        self.assertIn("report-mark", compose["name"])
        for svc in self.catalog.services:
            with self.subTest(service=svc.name):
                self.assertEqual(svc.container, f"report-mark-{svc.name}-1")
                self.assertIn(svc.name, compose["services"])

    def test_check_runs_with_system_python(self):
        """系統的 python3（有的話）以隔離模式 `-I` 照樣能 --check 範本——代理不靠應用程式的虛擬環境。"""
        python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
        proc = subprocess.run(
            [python, "-I", "-c",
             f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); from ops_agent.server import main; "
             f"sys.exit(main(['--catalog', {str(CATALOG)!r}, '--check']))"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("environment=production", proc.stdout)


class UnitTemplateTests(unittest.TestCase):
    def test_reads_the_installed_catalog_and_runs_as_the_agent_user(self):
        for key in ("ExecStartPre", "ExecStart"):
            with self.subTest(key=key):
                (line,) = _unit_values(key)
                self.assertIn(f"--catalog {INSTALLED_CATALOG}", line)
                self.assertTrue(line.startswith("/usr/bin/python3 -B -E -s -m ops_agent"))
        self.assertIn("--check", _unit_values("ExecStartPre")[0])
        self.assertEqual(_unit_values("User"), ["report-mark-ops"])
        self.assertEqual(_unit_values("WorkingDirectory"), ["/opt/report-mark-ops"])

    def test_socket_directory_matches_the_web_side(self):
        socket_dir = os.path.dirname(protocol.CANONICAL_SOCKETS["production"])
        self.assertEqual(f"/run/{_unit_values('RuntimeDirectory')[0]}", socket_dir)
        self.assertEqual(app_config._OPS_SOCKETS["production"], protocol.CANONICAL_SOCKETS["production"])
        self.assertEqual(_unit_values("RuntimeDirectoryPreserve"), ["yes"])
        self.assertEqual(_unit_values("RuntimeDirectoryMode"), ["0750"])

    def test_hardening_and_no_host_specific_paths(self):
        text = UNIT.read_text(encoding="utf-8")
        hardening = ("NoNewPrivileges=yes", "CapabilityBoundingSet=", "ProtectSystem=strict",
                     "RestrictAddressFamilies=AF_UNIX")
        for line in hardening:
            self.assertIn(f"\n{line}\n", text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("OnFailure=", text)


class PolkitTemplateTests(unittest.TestCase):
    def test_allow_matches_the_unit_user_and_the_catalog(self):
        allow = _allow()
        self.assertEqual(set(allow), set(_unit_values("User")))
        catalog = load_catalog(CATALOG)
        wanted: dict[str, set[str]] = {}
        for svc in catalog.services:
            for action in set(svc.actions) & set(protocol.WRITE_ACTIONS):
                wanted.setdefault("restart" if action == "restart" else "start", set()).add(svc.unit)
        for user, verbs in allow.items():
            with self.subTest(user=user):
                self.assertEqual({v: set(units) for v, units in verbs.items()}, wanted)


class ComposeOverrideTests(unittest.TestCase):
    def test_web_gets_the_socket_directory_and_group(self):
        doc = yaml.safe_load(OVERRIDE.read_text(encoding="utf-8"))
        self.assertEqual(set(doc["services"]), {"web"})
        web = doc["services"]["web"]
        socket_dir = os.path.dirname(protocol.CANONICAL_SOCKETS["production"])
        self.assertEqual(web["volumes"], [f"{socket_dir}:{socket_dir}"])
        self.assertEqual(len(web["group_add"]), 1)
        self.assertTrue(web["group_add"][0].startswith("${OPS_AGENT_GID:?"))
        self.assertEqual(web["environment"]["OPS_AGENT_ENVIRONMENT"], "production")


if __name__ == "__main__":
    unittest.main()
