"""`deploy/polkit/10-report-mark-ops.rules`：維運代理的使用者只能對 catalog 的寫入類 unit 做 start／restart。

三件事：
1. 白名單（ALLOW）與三份 catalog 的寫入類 action、三個代理 unit 的 `User=` 逐項一致；dev 使用者只碰
   `report-mark-dev-*`，prod 與 staging 使用者不碰它們；staging 不比生產寬；PostgreSQL／nginx／cloudflared
   不可能出現在裡面。
2. 檔案只用 polkit JS 引擎（duktape，ES5.1）認得的語法、只有一條 addRule、沒有 addAdminRule／spawn。
3. 有 node 時，以假的 `polkit` 物件實際執行這個檔，逐一比對決策（YES／NO／NOT_HANDLED）。沒有 node 就 skip
   （CI 的 runner 有 node；本機沒有時前兩項仍然守住）。
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_ops_agent import _uid

from ops_agent.catalog import load_catalog
from ops_agent.protocol import FORBIDDEN_WRITE_TARGET

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES = REPO_ROOT / "deploy" / "polkit" / "10-report-mark-ops.rules"
CATALOGS = {
    "production": (REPO_ROOT / "deploy" / "ops" / "services.prod.toml",
                   REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent.service"),
    "staging": (REPO_ROOT / "deploy" / "ops" / "services.staging.toml",
                REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent-staging.service"),
    "development": (REPO_ROOT / "deploy" / "ops" / "services.dev.toml",
                    REPO_ROOT / "deploy" / "systemd" / "report-mark-ops-agent-dev.service"),
}
VERB_OF_ACTION = {"restart": "restart", "run": "start"}


def _allow() -> dict:
    text = RULES.read_text(encoding="utf-8")
    m = re.search(r"// BEGIN ALLOW\s*var ALLOW = (\{.*?\});\s*// END ALLOW", text, re.S)
    assert m, "找不到 BEGIN ALLOW／END ALLOW 標記"
    return json.loads(m.group(1))


def _unit_user(unit: Path) -> str:
    (user,) = re.findall(r"^User=(.+)$", unit.read_text(encoding="utf-8"), re.M)
    return user.strip()


def _expected() -> dict:
    out: dict = {}
    for toml, unit in CATALOGS.values():
        catalog = load_catalog(toml, resolve_user=_uid)
        verbs: dict[str, list[str]] = {}
        for svc in catalog.services:
            for action in svc.actions:
                if action in VERB_OF_ACTION:
                    verbs.setdefault(VERB_OF_ACTION[action], []).append(svc.unit)
        out[_unit_user(unit)] = verbs
    return out


class PolkitAllowlistTests(unittest.TestCase):
    def test_allowlist_matches_catalogs_and_unit_users(self):
        allow = _allow()
        expected = _expected()
        self.assertEqual(set(allow), set(expected))
        for user, verbs in expected.items():
            self.assertEqual({v: sorted(u) for v, u in allow[user].items()},
                             {v: sorted(u) for v, u in verbs.items()}, user)

    def test_only_start_and_restart(self):
        for user, verbs in _allow().items():
            self.assertLessEqual(set(verbs), {"start", "restart"}, user)

    def test_write_scope(self):
        """v1／v1.5 的六個 oneshot，加上 Admin v2 的 db-snapshot、analytics-rollup（只讀或冪等、零 LLM）。
        security-retention（會刪資料）與 security-health／security-incident（探針與 P5 狀態機）刻意不在裡面。"""
        allow = _allow()
        self.assertEqual(allow["report-mark-ops"]["restart"], ["report-mark-web.service"])
        self.assertEqual(sorted(allow["report-mark-ops"]["start"]), sorted([
            "report-mark-sync.service", "report-mark-backup.service", "report-mark-freshness.service",
            "report-mark-audit.service", "report-mark-r2-reconcile.service", "report-mark-upload.service",
            "report-mark-db-snapshot.service", "report-mark-analytics-rollup.service"]))
        for user, verbs in allow.items():
            for unit in ("report-mark-security-retention.service", "report-mark-security-health.service",
                         "report-mark-security-incident.service"):
                self.assertNotIn(unit, verbs.get("start", []), user)
        for user, verbs in allow.items():
            for unit in itertools.chain.from_iterable(verbs.values()):
                self.assertIsNone(FORBIDDEN_WRITE_TARGET.search(unit), unit)
                self.assertTrue(unit.endswith(".service") and "*" not in unit, unit)

    def test_dev_and_prod_users_are_separated(self):
        allow = _allow()
        for unit in itertools.chain.from_iterable(allow["report-mark-ops-dev"].values()):
            self.assertTrue(unit.startswith("report-mark-dev-"), unit)
        for user in ("report-mark-ops", "report-mark-ops-staging"):
            for unit in itertools.chain.from_iterable(allow[user].values()):
                self.assertFalse(unit.startswith("report-mark-dev-"), unit)

    def test_staging_is_never_wider_than_production(self):
        """staging：restart 只有 web；start 是生產白名單的子集，不含 sync（共用金鑰）與 backup（沒有 NAS）。"""
        allow = _allow()
        staging, prod = allow["report-mark-ops-staging"], allow["report-mark-ops"]
        self.assertEqual(staging["restart"], ["report-mark-web.service"])
        self.assertLessEqual(set(staging["start"]), set(prod["start"]))
        for unit in ("report-mark-sync.service", "report-mark-backup.service", "report-mark-r2-reconcile.service"):
            self.assertNotIn(unit, staging["start"])


class PolkitSyntaxTests(unittest.TestCase):
    def test_es5_only_and_single_rule(self):
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", RULES.read_text(encoding="utf-8"), flags=re.S)
        for token in (r"\blet\b", r"\bconst\b", "=>", "`", r"\bclass\b", r"\.includes\(", r"\.startsWith\("):
            self.assertIsNone(re.search(token, code), f"polkit 的 JS 引擎（ES5.1）不認得 {token}")
        self.assertEqual(code.count("polkit.addRule("), 1)
        for banned in ("addAdminRule", "polkit.spawn", "polkit.log", "Result.AUTH"):
            self.assertNotIn(banned, code)
        self.assertEqual(code.count("polkit.Result.YES"), 1)

    def test_installed_name_sorts_first(self):
        """polkit 依檔名排序、第一個給出結果的規則勝出：NO 要先於系統其他規則（49-、50-）。"""
        self.assertTrue(RULES.name.startswith("10-"))


_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const rulesPath = process.argv[process.argv.length - 1];
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const rules = [];
const Result = {YES: "yes", NO: "no", AUTH_ADMIN: "auth_admin", NOT_HANDLED: null};
const polkit = {Result, addRule: (fn) => rules.push(fn), addAdminRule: () => { throw new Error("addAdminRule"); },
                spawn: () => { throw new Error("spawn"); }, log: () => {}};
vm.runInNewContext(fs.readFileSync(rulesPath, "utf8"), {polkit});
const out = cases.map(([user, id, details]) => {
  const action = {id, lookup: (k) => details[k]};
  const subject = {user, local: true, active: true, isInGroup: () => false, isInNetGroup: () => false};
  for (const fn of rules) {
    const r = fn(action, subject);
    if (r !== undefined && r !== null) return r;
  }
  return null;
});
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "沒有 node：只做靜態檢查")
class PolkitDecisionTests(unittest.TestCase):
    def test_decisions(self):
        allow = _allow()
        units = sorted({u for verbs in allow.values() for us in verbs.values() for u in us} | {
            "report-mark-postgres.service", "postgresql.service", "docker.service", "nginx.service",
            "cloudflared.service", "report-mark-ops-agent.service", "report-mark-health.service",
            "report-mark-sync.timer", "ssh.service", "report-mark-dev-web.service"})
        verbs = ("start", "restart", "stop", "reload", "try-restart", "reload-or-restart", "kill", "", None)
        users = ("report-mark-ops", "report-mark-ops-staging", "report-mark-ops-dev", "kashionz", "root",
                 "report-mark-ops-x")
        systemd_actions = ("org.freedesktop.systemd1.manage-units", "org.freedesktop.systemd1.manage-unit-files",
                           "org.freedesktop.systemd1.reload-daemon", "org.freedesktop.systemd1.set-environment")
        cases, expected = [], []
        for user, action_id in itertools.product(users, (*systemd_actions, "org.freedesktop.login1.reboot")):
            for verb, unit in itertools.product(verbs, units):
                details = {"unit": unit, "verb": verb}
                cases.append([user, action_id, details])
                if user not in allow or not action_id.startswith("org.freedesktop.systemd1."):
                    expected.append(None)
                elif action_id == "org.freedesktop.systemd1.manage-units" and unit in allow[user].get(verb or "", []):
                    expected.append("yes")
                else:
                    expected.append("no")
        proc = subprocess.run(["node", "-e", _HARNESS, str(RULES)], input=json.dumps(cases), capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        mismatches = [(c, e, g) for c, e, g in zip(cases, expected, got) if e != g]
        self.assertEqual(mismatches[:5], [])
        yes = [c for c, g in zip(cases, got) if g == "yes"]
        self.assertEqual(len(yes), sum(len(us) for verbs in allow.values() for us in verbs.values()))
        self.assertTrue(all(c[2]["unit"] not in ("report-mark-postgres.service", "nginx.service") for c in yes))


if __name__ == "__main__":
    unittest.main()
