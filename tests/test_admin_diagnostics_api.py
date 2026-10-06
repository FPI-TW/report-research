"""診斷（/api/admin/diagnostics，HTTP 層）與 `app/services/diagnostics.py` 的純函式。

不連 DB、不連維運代理、不打 R2／DeepSeek：DB 用假 session、代理用假 client、healthz 的兩份快取只被動讀。驗：
未登入 401、一般使用者 403、沒有 `ops.read` 的管理員 403 `missing_scope`；回應形狀與 schema 版本判讀；
**祕密不外洩**（環境變數、DB 密碼、狀態檔訊息裡夾帶的憑證，整份回應字串都找不到）；某段失敗（DB 掛、
代理連不上、某個段落拋例外）整頁仍 200、該段只記例外型別；TTL 快取；讀 `.git` 不跑 git；
版本判讀與 `scripts/schema_baseline.py` 的 `compare_versions` 逐例一致。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import db, diagnostics, llm_health, llm_http
from web import auth, deps, ops_client
from web.routers import admin_diagnostics
from web.routers import health as health_routes
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
PATH = "/api/admin/diagnostics"

# 假祕密一律 fixed-test-secret-…（gitleaks 的 allowlist 認得這個前綴）。
SECRETS = {
    "DEEPSEEK_API_KEY": "fixed-test-secret-deepseek6b1f0c",
    "R2_ACCESS_KEY_ID": "fixed-test-secret-r2access91ad",
    "R2_SECRET_ACCESS_KEY": "fixed-test-secret-r2secretc0ffee",
    "REPORT_MARK_EDGE_SECRET": "fixed-test-secret-edge77aa",
    "REPORT_MARK_ALERT_WEBHOOK": "https://hooks.example.invalid/fixed-test-secret-webhook5150",
    "LINE_CHANNEL_TOKEN": "fixed-test-secret-linetoken3e3e",
    "SOME_DB_PASSWORD": "fixed-test-secret-otherpassword11",
}
DB_PASSWORD = "fixed-test-secret-dbpass4242"
DB_URL = f"postgresql+asyncpg://diaguser:{DB_PASSWORD}@db.internal.example:6543/research_x"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


class _Result:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _Session:
    def __init__(self, revisions):
        self.revisions = revisions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT 1" in sql:
            return _Result(1)
        if "server_version" in sql:
            return _Result("16.4")
        if "to_regclass" in sql:
            return _Result(self.revisions is not None)
        if "alembic_version" in sql:
            return _Result(rows=[(r,) for r in self.revisions or []])
        raise AssertionError(f"診斷頁送了預期外的 SQL：{sql}")


class _SessionFactory:
    def __init__(self, revisions=("0007",), fail: BaseException | None = None):
        self.revisions = list(revisions) if revisions is not None else None
        self.fail = fail
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return _Session(self.revisions)


class _OpsClient:
    def __init__(self, fail: BaseException | None = None):
        self.timeout = 20.0
        self.fail = fail
        self.ops: list[str] = []

    async def request(self, op, service=None, params=None, *, actor=None):
        self.ops.append(op)
        if self.fail is not None:
            raise self.fail
        return {"items": [{"name": "web"}, {"name": "postgres"}]}


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        admin_diagnostics.reset_caches()
        self.addCleanup(admin_diagnostics.reset_caches)
        self.store = _NoOpsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sessions = _SessionFactory()
        self.ops = _OpsClient()
        for patcher in (
            mock.patch.object(deps, "SessionFactory", self.sessions),
            mock.patch.object(ops_client, "default_client", lambda: self.ops),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client


class AuthzTests(_Base):
    def test_gates(self):
        self.assertEqual(_client().get(PATH).status_code, 401)
        self.assertEqual(self.login("alice", USER_PW).get(PATH).status_code, 403)
        r = self.login("limited").get(PATH)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "missing_scope")
        self.assertEqual(self.sessions.calls, 0, "沒權限的請求不得碰 DB")


class ShapeTests(_Base):
    def test_full_response(self):
        r = self.login().get(PATH)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(
            set(d),
            {"generated_at", "cache_ttl_s", "versions", "schema_info", "runtime", "config", "models", "db_pool",
             "gates", "gates_error", "checks"},
        )
        self.assertEqual(d["cache_ttl_s"], 30)
        self.assertTrue(d["checks"]["db"]["ok"])
        self.assertEqual(d["checks"]["db"]["server_version"], "16.4")
        self.assertIsNotNone(d["checks"]["db"]["latency_ms"])
        self.assertEqual(d["checks"]["ops_agent"], {"ok": True, "latency_ms": mock.ANY, "services": 2, "error": None})
        self.assertEqual(self.ops.ops, ["list"])
        self.assertLessEqual(self.ops.timeout, diagnostics.OPS_TIMEOUT)
        # 程式的 head（這份 checkout 的 db/migrations/）
        _, heads = diagnostics.code_revisions()
        self.assertEqual(d["schema_info"]["code_heads"], list(heads))
        self.assertIn(d["schema_info"]["status"], ("ok", "behind"))
        self.assertEqual(d["versions"]["python"].count("."), 2)
        self.assertIn("fastapi", d["versions"]["packages"])
        self.assertEqual(d["config"]["db_target"], diagnostics.config_section()["db_target"])
        self.assertIn("ask_enable_web", d["config"]["flags"])
        self.assertTrue(any(g["name"] == "ask" for g in d["gates"]))
        self.assertGreater(d["runtime"]["pid"], 0)
        self.assertIn(d["models"]["warmup"], diagnostics.WARMUP_STATES)

    def test_schema_statuses(self):
        chain, heads = diagnostics.code_revisions()
        head = heads[0]
        cases = [
            ([head], "ok"),
            (None, "unversioned"),
            ([], "unversioned"),
            (["9999"], "ahead"),
            ([chain[0], head], "ambiguous"),
        ]
        if len(chain) > 1:
            cases.append(([chain[0]], "behind"))
        for revisions, expected in cases:
            with self.subTest(revisions=revisions):
                admin_diagnostics.reset_caches()
                self.sessions.revisions = revisions
                s = self.login().get(PATH).json()["schema_info"]
                self.assertEqual(s["status"], expected)
                if expected == "behind":
                    self.assertEqual(s["pending"], list(chain[1:]))

    def test_daily_check_summary(self):
        status = self.tmp / "schema_check.json"
        status.write_text(json.dumps({
            "format": 1, "checked_at": "2026-10-06T05:20:03+08:00", "duration_s": 12.3, "mode": "full",
            "target": "localhost:5436/research", "exit_code": 1, "alert": True, "problems": ["schema_drift"],
            "message": "3 項 schema drift（基準 revision 0007）",
            "version": {"status": "ok", "expected_head": "0007", "db_revision": "0007", "pending": []},
            "drift": {"status": "drift", "drift_count": 3, "categories": {}},
        }), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SCHEMA_CHECK_STATUS_FILE": str(status)}):
            dc = self.login().get(PATH).json()["schema_info"]["daily_check"]
        self.assertTrue(dc["available"])
        self.assertEqual((dc["exit_code"], dc["alert"], dc["problems"]), (1, True, ["schema_drift"]))
        self.assertEqual((dc["version_status"], dc["drift_status"], dc["drift_count"]), ("ok", "drift", 3))
        self.assertIsNotNone(dc["age_hours"])

    def test_daily_check_missing_or_broken(self):
        self.assertEqual(
            self.login().get(PATH).json()["schema_info"]["daily_check"]["unavailable_reason"], "missing")
        broken = self.tmp / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        admin_diagnostics.reset_caches()
        with mock.patch.dict(os.environ, {"SCHEMA_CHECK_STATUS_FILE": str(broken)}):
            dc = self.login().get(PATH).json()["schema_info"]["daily_check"]
        self.assertEqual((dc["available"], dc["unavailable_reason"]), (False, "invalid"))

    def test_cached_30_seconds(self):
        client = self.login()
        first = client.get(PATH).json()
        self.sessions.revisions = None
        second = client.get(PATH).json()
        self.assertEqual(first, second)
        self.assertEqual(self.sessions.calls, 1)
        admin_diagnostics.reset_caches()
        self.assertEqual(client.get(PATH).json()["schema_info"]["status"], "unversioned")


class SecretLeakTests(_Base):
    """整份回應的序列化字串裡，祕密的值一個都不能出現。"""

    def test_no_secret_in_response(self):
        status = self.tmp / "schema_check.json"
        # 狀態檔訊息夾帶連線字串與密碼（例如某版腳本把例外 repr 寫進去）：scrub 這一層要擋下來。
        status.write_text(json.dumps({
            "checked_at": "2026-10-06T05:20:03+08:00", "mode": "full", "exit_code": 2, "alert": True,
            "problems": ["check_error"],
            "message": f"讀不到 DB：connect {DB_URL} failed；key={SECRETS['DEEPSEEK_API_KEY']}",
            "version": {"status": "error"}, "drift": {"status": "error"},
        }), encoding="utf-8")
        env = {**SECRETS, "SCHEMA_CHECK_STATUS_FILE": str(status), "REPORT_MARK_DB_URL": DB_URL}
        with mock.patch.dict(os.environ, env), mock.patch.object(db, "DATABASE_URL", DB_URL):
            r = self.login().get(PATH)
        self.assertEqual(r.status_code, 200)
        body = r.text
        forbidden = [*SECRETS.values(), DB_PASSWORD, DB_URL, os.environ["REPORT_MARK_SESSION_SECRET"], "diaguser:"]
        for value in forbidden:
            with self.subTest(value=value[:24]):
                self.assertNotIn(value, body)
        d = r.json()
        self.assertEqual(d["config"]["db_target"], "db.internal.example:6543/research_x")
        self.assertIn(diagnostics.REDACTED, d["schema_info"]["daily_check"]["message"])
        present = d["config"]["secrets_present"]
        self.assertTrue(all(present[k] for k in ("deepseek_api_key", "r2_credentials", "edge_secret",
                                                 "alert_webhook", "session_secret")))

    def test_config_is_whitelist(self):
        """config 段的每個鍵都在白名單裡；名稱像祕密的鍵，值只能是布林。"""
        allowed = {
            "db_target", "object_storage_mode", "llm_provider", "models", "extractor", "log_level",
            "ops_agent_environment", "ops_agent_socket", "flags", "secrets_present", "limits", "error",
        }
        cfg = self.login().get(PATH).json()["config"]
        self.assertEqual(set(cfg), allowed)

        def walk(node, path=""):
            for key, value in node.items():
                if isinstance(value, dict):
                    walk(value, f"{path}{key}.")
                elif diagnostics.SENSITIVE_NAME.search(key) or path.startswith("secrets_present"):
                    self.assertIsInstance(value, bool, f"{path}{key} 名稱像祕密卻不是布林值")

        walk(cfg)


class FailureTests(_Base):
    def test_db_and_agent_down_still_200(self):
        self.sessions.fail = ConnectionRefusedError(f"connect to {DB_URL} refused")
        self.ops.fail = ops_client.OpsAgentUnavailable("維運代理未啟動（找不到 /run/x.sock）")
        r = self.login().get(PATH)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["checks"]["db"], {"ok": False, "latency_ms": None, "server_version": None,
                                             "error": "ConnectionRefusedError"})
        self.assertEqual(d["checks"]["ops_agent"]["error"], "OpsAgentUnavailable")
        self.assertEqual((d["schema_info"]["status"], d["schema_info"]["error"]), ("error", "ConnectionRefusedError"))
        self.assertNotIn("refused", r.text, "只記例外型別，不記訊息")
        self.assertNotIn("/run/x.sock", r.text)
        self.assertIsNone(d["runtime"]["error"])

    def test_db_timeout(self):
        class _Hang(_Session):
            async def execute(self, stmt, params=None):
                await asyncio.sleep(30)

        self.sessions = lambda: _Hang(None)
        with mock.patch.object(deps, "SessionFactory", self.sessions), \
                mock.patch.object(diagnostics, "DB_TIMEOUT", 0.05):
            d = self.login().get(PATH).json()
        self.assertEqual(d["checks"]["db"]["error"], "TimeoutError")

    def test_section_exception_is_isolated(self):
        for name, section in (("runtime_section", "runtime"), ("config_section", "config"),
                              ("versions_section", "versions"), ("models_section", "models")):
            with self.subTest(section=section):
                admin_diagnostics.reset_caches()
                with mock.patch.object(diagnostics, name, side_effect=RuntimeError("boom /secret/path")):
                    r = self.login().get(PATH)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()[section]["error"], "RuntimeError")
                self.assertNotIn("/secret/path", r.text)

    def test_cached_health_snapshots_failing(self):
        with mock.patch.object(health_routes, "storage_snapshot", side_effect=OSError("x")), \
                mock.patch.object(llm_health, "cached_snapshot", side_effect=ValueError("y")):
            d = self.login().get(PATH).json()
        self.assertEqual((d["checks"]["storage"]["state"], d["checks"]["storage"]["error"]), ("unknown", "OSError"))
        self.assertEqual((d["checks"]["llm"]["state"], d["checks"]["llm"]["error"]), ("unknown", "ValueError"))


class PassiveHealthTests(unittest.TestCase):
    """R2 與 DeepSeek 只讀 healthz 上一次的結論：診斷頁不得觸發探測（計費的 R2 list、餘額查詢）。"""

    def test_llm_snapshot_does_not_fetch(self):
        llm_health.reset()
        self.addCleanup(llm_health.reset)
        with mock.patch.object(llm_http, "fetch_balance", side_effect=AssertionError("不得查餘額")):
            snap = llm_health.cached_snapshot()
        self.assertEqual(snap["state"], llm_health.UNKNOWN)
        self.assertIsNone(snap["last_check_age_s"])
        self.assertNotIn("detail", snap, "detail 含金額，只進日誌")

    def test_storage_snapshot_does_not_probe(self):
        class _Storage:
            enabled = True

            def ping(self):
                raise AssertionError("不得探測 R2")

        with mock.patch.object(health_routes, "get_object_storage", lambda: _Storage()), \
                mock.patch.object(health_routes, "_storage", health_routes._STORAGE_INITIAL):
            self.assertEqual(health_routes.storage_snapshot()["state"], "unknown")
            ok = health_routes._StorageState("ok", 0, health_routes.time.monotonic() + 290)
            with mock.patch.object(health_routes, "_storage", ok):
                snap = health_routes.storage_snapshot()
        self.assertEqual((snap["state"], snap["last_probe_ok"]), ("ok", True))
        self.assertAlmostEqual(snap["last_probe_age_s"], 10, delta=2)


class ScrubTests(unittest.TestCase):
    def test_secret_values_and_scrub(self):
        env = {"DEEPSEEK_API_KEY": "fixed-test-secret-a1", "ASK_MAX_TOKENS": "8192", "LOG_LEVEL": "INFO",
               "R2_ENDPOINT_URL": "https://acct.r2.example.invalid", "EMPTY_TOKEN": ""}
        values = diagnostics.secret_values(env, db_url="postgresql://u:fixed-test-secret-pw@h:1/d")
        self.assertIn("fixed-test-secret-a1", values)
        self.assertIn("fixed-test-secret-pw", values)
        self.assertIn("https://acct.r2.example.invalid", values)
        self.assertNotIn("8192", values, "太短的值不拿來比對")
        out = diagnostics.scrub(
            {"a": ["x fixed-test-secret-a1 y", {"k": "redis://user:fixed-test-secret-urlpw@h:6379/0"}], "n": 3},
            values,
        )
        self.assertEqual(out["a"][0], f"x {diagnostics.REDACTED} y")
        self.assertEqual(out["a"][1]["k"], f"redis://{diagnostics.REDACTED}@h:6379/0")
        self.assertEqual(out["n"], 3)


class GitReadTests(unittest.TestCase):
    SHA = "0123456789abcdef0123456789abcdef01234567"
    SHA2 = "fedcba9876543210fedcba9876543210fedcba98"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _git(self, base: Path, head: str):
        (base / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (base / "HEAD").write_text(head + "\n")

    def test_loose_ref(self):
        git = self.root / ".git"
        self._git(git, "ref: refs/heads/main")
        (git / "refs" / "heads" / "main").write_text(self.SHA + "\n")
        self.assertEqual(diagnostics.read_git_head(self.root),
                         {"available": True, "reason": None, "commit": self.SHA, "branch": "main"})

    def test_packed_ref_and_detached(self):
        git = self.root / ".git"
        self._git(git, "ref: refs/heads/feat/x")
        (git / "packed-refs").write_text(f"# pack-refs\n{self.SHA2} refs/heads/other\n{self.SHA} refs/heads/feat/x\n")
        self.assertEqual(diagnostics.read_git_head(self.root)["commit"], self.SHA)
        (git / "HEAD").write_text(self.SHA2 + "\n")
        self.assertEqual(diagnostics.read_git_head(self.root)["branch"], None)
        self.assertEqual(diagnostics.read_git_head(self.root)["commit"], self.SHA2)

    def test_worktree_pointer(self):
        common = self.root / "main.git"
        wt = common / "worktrees" / "lane"
        self._git(common, "ref: refs/heads/main")
        self._git(wt, "ref: refs/heads/lane")
        (wt / "commondir").write_text("../..\n")
        (common / "refs" / "heads" / "lane").write_text(self.SHA + "\n")
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text(f"gitdir: {wt}\n")
        got = diagnostics.read_git_head(checkout)
        self.assertEqual((got["commit"], got["branch"]), (self.SHA, "lane"))

    def test_missing_or_unresolved(self):
        self.assertEqual(diagnostics.read_git_head(self.root)["reason"], "no_git_dir")
        self._git(self.root / ".git", "ref: refs/heads/nowhere")
        got = diagnostics.read_git_head(self.root)
        self.assertEqual((got["available"], got["reason"]), (False, "unresolved_head"))

    def test_does_not_spawn_git(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("不得跑 git")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("不得跑 git")):
            diagnostics.git_section()


class FrontendBuildTests(unittest.TestCase):
    def test_entry_assets_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index.html"
            self.assertEqual(diagnostics.frontend_section(index)["available"], False)
            index.write_text(
                '<script type="module" crossorigin src="/app/assets/index-AbC.js"></script>'
                '<link rel="modulepreload" crossorigin href="/app/assets/react-Zz.js">'
                '<link rel="stylesheet" crossorigin href="/app/assets/index-Qq.css">',
                encoding="utf-8",
            )
            got = diagnostics.frontend_section(index)
        self.assertTrue(got["available"])
        self.assertEqual(got["entry_assets"], ["index-AbC.js", "index-Qq.css"])
        self.assertIsNotNone(got["built_at"])


class CompareParityTests(unittest.TestCase):
    """web 不 import scripts.*，版本判讀在 diagnostics 另寫一份；這裡逐例比對兩邊結論相同。"""

    def test_same_as_schema_baseline(self):
        from scripts import schema_baseline as sb

        chain, heads = ["0001", "0002", "0003"], ["0003"]
        cases = [None, [], ["0003"], ["0002"], ["0001"], ["0099"], ["0001", "0002"]]
        for revs in cases:
            for hs in (heads, ["0003", "0004"]):
                with self.subTest(revs=revs, heads=hs):
                    expected = sb.compare_versions(revs, chain=chain, heads=hs)
                    status, pending = diagnostics.compare_revisions(revs, chain=chain, heads=hs)
                    self.assertEqual((status, pending), (expected.status, expected.pending))


if __name__ == "__main__":
    unittest.main()
