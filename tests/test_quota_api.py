"""每人配額的 HTTP 層：`/api/ask` 與 `/api/admin/export/*.csv` 的計數與 429 契約、`/api/admin/quota*`、`/api/me/quota`、
LLM 用量頁的線上來源。服務層換成假物件（`deps.quota`），不連 DB。

驗：
- 429 `quota_exceeded`＋`Retry-After`（正式阻擋時），body 帶 kind／limit；影子模式照常 200。
- `/api/ask`：`queue_full()` 被拒不扣次數、驗證失敗（400／404）不扣次數、`/api/ask/stop` 不扣次數；重新生成與編輯重問
  都算；計數在開始串流之前。
- 匯出：超額且正式阻擋時 429、不寫匯出稽核；影子模式照常匯出。
- 管理端點：401／403（一般使用者）／`missing_scope`／寫入要 `elevation_required`；`unlimited` 只有 super admin
  （服務層規則的錯誤碼對應）；回應形狀。
- `/api/me/quota`：401、任何登入使用者可看自己的；服務失敗 503。
- `/api/admin/llm-usage` 附 `online` 與 `combined`，線上讀取失敗不影響批次那段。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import quota as real_quota
from web import auth, deps
from web.routers import ask as ask_routes
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
UUID_A = "13c3af97-b458-4108-836d-654a89e76fb7"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class FakeQuota:
    """`deps.quota` 的替身：記錄 charge 呼叫，依 `decision` 回結論；錯誤類別沿用真的模組。"""

    Decision = real_quota.Decision
    QuotaError = real_quota.QuotaError
    QuotaTargetNotFound = real_quota.QuotaTargetNotFound
    QuotaPermissionDenied = real_quota.QuotaPermissionDenied
    InvalidQuotaInput = real_quota.InvalidQuotaInput
    exceeded_message = staticmethod(real_quota.exceeded_message)

    def __init__(self):
        self.charges: list[tuple[str | None, str]] = []
        self.blocked = False
        self.shadow_over = False
        self.set_calls: list[dict] = []
        self.set_error: Exception | None = None
        self.me_error: Exception | None = None

    async def charge(self, user, kind, **_kw):
        self.charges.append((user.id, kind))
        if self.blocked:
            return real_quota.Decision(kind=kind, allowed=False, over=True, enforced=True, limit=7, retry_after=1234)
        if self.shadow_over:
            return real_quota.Decision(kind=kind, allowed=True, over=True, enforced=False, limit=7)
        return real_quota.Decision(kind=kind, allowed=True, counted=True, limit=7, count=1)

    async def me_usage(self, user):
        if self.me_error:
            raise self.me_error
        kinds = ("ask", "export", "upload") if user.is_admin else ("ask",)
        return {"day": "2026-10-07", "timezone": "Asia/Taipei", "resets_in_seconds": 100, "enforced": False,
                "items": [{"kind": k, "used": 3, "over": 0, "limit": 100, "remaining": 97} for k in kinds]}

    def _item(self, kind="ask", mode="limit", limit=5):
        return {"kind": kind, "used": 1, "over": 2, "limit": limit, "default_limit": 100, "mode": mode,
                "remaining": None if limit is None else limit - 1, "reason": "r", "updated_at": None}

    async def admin_overview(self):
        return {
            "day": "2026-10-07", "timezone": "Asia/Taipei", "resets_in_seconds": 100,
            "defaults": {"ask": 100, "export": 20, "upload": 30},
            "enforcement": {"env_ceiling": False, "flag_enabled": False, "flag_scoped": False,
                            "flag_source": "default", "effective": False, "mode": "shadow"},
            "over_today": {"ask": 2, "export": 0},
            "users": [{"user_id": UUID_A, "username": "alice", "role": "user", "enabled": True, "is_super": False,
                       "items": [self._item(k) for k in ("ask", "export", "upload")],
                       "llm": {"calls": 3, "failures": 0, "prompt_tokens": 10, "completion_tokens": 5}}],
        }

    async def usage_stats(self, days):
        self.stats_days = days
        return {"since_day": "2026-09-24", "until_day": "2026-10-07", "days": days, "timezone": "Asia/Taipei",
                "kinds": [{"kind": k, "default_limit": 100, "user_days": 4, "users": 3, "p50": 5, "p95": 40,
                           "max": 120, "over_user_days": 1, "over_events": 20} for k in ("ask", "export", "upload")]}

    async def set_override(self, user_id, kind, *, mode, daily_limit=None, reason=None, actor_id):
        self.set_calls.append({"user_id": user_id, "kind": kind, "mode": mode, "daily_limit": daily_limit,
                               "reason": reason, "actor_id": actor_id})
        if self.set_error:
            raise self.set_error
        return {"changed": True, "item": self._item(kind, mode, daily_limit if mode == "limit" else None)}


class _Store(FakeAccounts):
    """`limited` 是管理員但沒有 accounts.manage。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"accounts.manage"})
        return user


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.addCleanup(auth._FAILS.clear)
        self.store = _Store()
        self.root_id = self.store.add_user("root", ADMIN_PW, "admin", is_super=True)
        self.alice_id = self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        ctx = install(self.store)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.q = FakeQuota()
        orig = deps.quota
        deps.quota = self.q
        self.addCleanup(setattr, deps, "quota", orig)

    def login(self, username="root", password=ADMIN_PW, *, elevate=False):
        client = _client()
        self.assertEqual(client.post("/login", data={"username": username, "password": password}).status_code, 303)
        if elevate:
            self.assertEqual(client.post("/api/admin/elevate", json={"password": password}).status_code, 200)
        return client


class AskQuotaTests(_Base):
    def setUp(self):
        super().setUp()
        self.answers: list[dict] = []

        async def fake_aq(question, **kwargs):
            self.answers.append(kwargs)
            yield ("done", {"qa_id": "x", "conversation_id": "c"})

        orig = deps.answer_question
        deps.answer_question = fake_aq
        self.addCleanup(setattr, deps, "answer_question", orig)

    def test_counts_once_per_ask_including_regenerate_and_edit(self):
        client = self.login("alice", USER_PW)
        for extra in ({}, {"regenerate_of": UUID_A}, {"edit_of": UUID_A}):
            self.assertEqual(client.post("/api/ask", json={"question": "台積電", **extra}).status_code, 200)
        self.assertEqual(self.q.charges, [(self.alice_id, "ask")] * 3)
        self.assertEqual(len(self.answers), 3)

    def test_enforced_429_contract(self):
        self.q.blocked = True
        r = self.login("alice", USER_PW).post("/api/ask", json={"question": "台積電"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["retry-after"], "1234")
        body = r.json()
        self.assertEqual((body["code"], body["kind"], body["limit"]), ("quota_exceeded", "ask", 7))
        self.assertIn("上限 7 次", body["detail"])
        self.assertIn("request_id", body)
        self.assertEqual(self.answers, [], "被擋時不得開始回答（也不呼叫 LLM）")

    def test_shadow_over_is_allowed(self):
        self.q.shadow_over = True
        r = self.login("alice", USER_PW).post("/api/ask", json={"question": "台積電"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("event: done", r.text)
        self.assertEqual(len(self.answers), 1)

    def test_queue_full_does_not_charge(self):
        orig = ask_routes._ASK_GATE.queue_full
        ask_routes._ASK_GATE.queue_full = lambda: True
        self.addCleanup(setattr, ask_routes._ASK_GATE, "queue_full", orig)
        r = self.login("alice", USER_PW).post("/api/ask", json={"question": "台積電"})
        self.assertEqual((r.status_code, r.headers["retry-after"]), (429, "30"))
        self.assertNotEqual(r.json()["code"], "quota_exceeded")
        self.assertEqual(self.q.charges, [])

    def test_rejected_requests_do_not_charge(self):
        client = self.login("alice", USER_PW)
        self.assertEqual(client.post("/api/ask", json={"question": "  "}).status_code, 400)
        self.assertEqual(client.post("/api/ask", json={"question": "x", "edit_of": "bad"}).status_code, 400)

        async def foreign(*_a, **_k):
            return True

        orig = deps.qa_is_foreign
        deps.qa_is_foreign = foreign
        self.addCleanup(setattr, deps, "qa_is_foreign", orig)
        self.assertEqual(client.post("/api/ask", json={"question": "x", "regenerate_of": UUID_A}).status_code, 404)
        self.assertEqual(_client().post("/api/ask", json={"question": "x"}).status_code, 401)
        self.assertEqual(self.q.charges, [])

    def test_stop_does_not_charge(self):
        async def fake_stop(*_a, **_k):
            return "qa-1"

        orig = deps.log_stopped_qa
        deps.log_stopped_qa = fake_stop
        self.addCleanup(setattr, deps, "log_stopped_qa", orig)
        r = self.login("alice", USER_PW).post("/api/ask/stop", json={"question": "台積電", "partial_answer": "部分"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.q.charges, [])


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class ExportQuotaTests(_Base):
    def exports(self):
        return [e for e in self.store.audit if e.action == "data.export"]

    def test_each_export_charges(self):
        client = self.login()
        self.assertEqual(client.get("/api/admin/export/users.csv").status_code, 200)
        self.assertEqual(client.get("/api/admin/export/audit.csv").status_code, 200)
        self.assertEqual(self.q.charges, [(self.root_id, "export")] * 2)

    def test_enforced_429_without_export_audit(self):
        self.q.blocked = True
        r = self.login().get("/api/admin/export/users.csv")
        self.assertEqual((r.status_code, r.headers["retry-after"], r.json()["code"]), (429, "1234", "quota_exceeded"))
        self.assertEqual(r.json()["kind"], "export")
        self.assertEqual(self.exports(), [])

    def test_shadow_over_still_exports(self):
        self.q.shadow_over = True
        r = self.login().get("/api/admin/export/users.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.exports()), 1)

    def test_forbidden_export_does_not_charge(self):
        self.assertEqual(self.login("alice", USER_PW).get("/api/admin/export/users.csv").status_code, 403)
        self.assertEqual(self.login("limited").get("/api/admin/export/users.csv").status_code, 403)
        self.assertEqual(self.q.charges, [])


class AdminQuotaTests(_Base):
    PUT = "/api/admin/quota/users/{uid}/ask"

    def test_gates(self):
        put = self.PUT.format(uid=UUID_A)
        for method, path in (("get", "/api/admin/quota"), ("get", "/api/admin/quota/stats"), ("put", put)):
            with self.subTest(path=path):
                kw = {"json": {"mode": "default"}} if method == "put" else {}
                self.assertEqual(getattr(_client(), method)(path, **kw).status_code, 401)
                self.assertEqual(getattr(self.login("alice", USER_PW), method)(path, **kw).status_code, 403)
                r = getattr(self.login("limited"), method)(path, **kw)
                self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.q.set_calls, [])

    def test_write_requires_elevation(self):
        r = self.login().put(self.PUT.format(uid=UUID_A), json={"mode": "limit", "daily_limit": 5})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self.assertEqual(self.q.set_calls, [])

    def test_overview_shape(self):
        r = self.login().get("/api/admin/quota")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["enforcement"]["mode"], "shadow")
        self.assertEqual([i["kind"] for i in body["users"][0]["items"]], ["ask", "export", "upload"])
        self.assertEqual(body["over_today"], {"ask": 2, "export": 0})

    def test_stats_days_bounds(self):
        client = self.login()
        self.assertEqual(client.get("/api/admin/quota/stats").json()["days"], 14)
        self.assertEqual(client.get("/api/admin/quota/stats", params={"days": 30}).json()["days"], 30)
        self.assertEqual(client.get("/api/admin/quota/stats", params={"days": 91}).status_code, 422)
        self.assertEqual(client.get("/api/admin/quota/stats", params={"days": 0}).status_code, 422)

    def test_set_limit(self):
        r = self.login(elevate=True).put(self.PUT.format(uid=UUID_A),
                                         json={"mode": "limit", "daily_limit": 5, "reason": "試用"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["item"]["limit"], 5)
        self.assertEqual(self.q.set_calls, [{"user_id": UUID_A, "kind": "ask", "mode": "limit", "daily_limit": 5,
                                             "reason": "試用", "actor_id": self.root_id}])

    def test_limit_mode_needs_value_and_other_modes_drop_it(self):
        client = self.login(elevate=True)
        r = client.put(self.PUT.format(uid=UUID_A), json={"mode": "limit"})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_input"))
        client.put(self.PUT.format(uid=UUID_A), json={"mode": "default", "daily_limit": 9})
        self.assertIsNone(self.q.set_calls[-1]["daily_limit"])
        self.assertEqual(client.put(self.PUT.format(uid=UUID_A), json={"mode": "x"}).status_code, 422)
        self.assertEqual(client.put("/api/admin/quota/users/" + UUID_A + "/search", json={"mode": "default"})
                         .status_code, 422)
        self.assertEqual(client.put(self.PUT.format(uid=UUID_A), json={"mode": "limit", "daily_limit": -1})
                         .status_code, 422)

    def test_service_errors_map_to_codes(self):
        client = self.login(elevate=True)
        cases = ((real_quota.QuotaTargetNotFound("帳號不存在"), 404, "not_found"),
                 (real_quota.QuotaPermissionDenied("只有 super admin 能設定「不限」"), 403, "super_required"),
                 (real_quota.InvalidQuotaInput("理由太長"), 400, "invalid_input"))
        for exc, status, code in cases:
            with self.subTest(code=code):
                self.q.set_error = exc
                r = client.put(self.PUT.format(uid=UUID_A), json={"mode": "unlimited"})
                self.assertEqual((r.status_code, r.json()["code"], r.json()["detail"]), (status, code, str(exc)))


class MeQuotaTests(_Base):
    def test_requires_login(self):
        self.assertEqual(_client().get("/api/me/quota").status_code, 401)

    def test_user_sees_own_ask_only(self):
        r = self.login("alice", USER_PW).get("/api/me/quota")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["kind"] for i in r.json()["items"]], ["ask"])

    def test_admin_sees_three_kinds(self):
        r = self.login().get("/api/me/quota")
        self.assertEqual([i["kind"] for i in r.json()["items"]], ["ask", "export", "upload"])

    def test_service_failure_is_503(self):
        self.q.me_error = RuntimeError("DB 掛了")
        r = self.login("alice", USER_PW).get("/api/me/quota")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "quota_unavailable"))


class LlmUsageOnlineTests(_Base):
    def setUp(self):
        super().setUp()
        self.online_calls = []

        async def fake_online(since, until, *, session_factory=None):
            self.online_calls.append((since, until))
            return self.online

        self.online = {
            "available": True, "error": None, "since_day": "2026-10-01", "until_day": "2026-10-07",
            "totals": _totals(4), "by_day": [{"day": "2026-10-06", **_totals(4)}],
            "by_task": [{"task": "ask_answer", **_totals(4)}], "by_model": [{"model": "deepseek-flash", **_totals(4)}],
            "rows": [], "rows_truncated": False, "attributed_users": 2, "unattributed_calls": 1,
            "cost_available": False,
        }
        orig = deps.llm_usage.summarize_online
        deps.llm_usage.summarize_online = fake_online
        self.addCleanup(setattr, deps.llm_usage, "summarize_online", orig)

    def test_online_and_combined(self):
        r = self.login().get("/api/admin/llm-usage", params={"since": "2026-10-01T00:00:00Z"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["online"]["totals"]["calls"], 4)
        self.assertEqual(body["online"]["attributed_users"], 2)
        self.assertEqual(body["combined"]["totals"]["calls"], body["totals"]["calls"] + 4)
        self.assertNotIn("user_id", r.text, "LLM 用量頁不出個人維度")

    def test_online_unavailable_keeps_batch(self):
        self.online = {**self.online, "available": False, "error": "線上用量暫時讀不到（RuntimeError）",
                       "totals": _totals(0), "by_day": [], "by_task": [], "by_model": []}
        r = self.login().get("/api/admin/llm-usage", params={"since": "2026-09-01T00:00:00Z"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["online"]["available"])
        self.assertIn("source", r.json())


def _totals(calls):
    return {"calls": calls, "failures": 0, "prompt_hit_tokens": 1, "prompt_miss_tokens": 2, "completion_tokens": 3,
            "reasoning_tokens": 0, "calls_without_tokens": 0, "total_ms": 10, "cost": None}


class LlmUsageCombineTests(unittest.TestCase):
    def test_combine_and_online_days(self):
        from app.services import llm_usage

        batch = {"totals": _totals(2), "by_day": [{"day": "2026-10-06", **_totals(2)}], "cost_available": False}
        online = {"totals": _totals(3), "by_day": [{"day": "2026-10-06", **_totals(1)},
                                                   {"day": "2026-10-07", **_totals(2)}], "cost_available": False}
        out = llm_usage.combine(batch, online)
        self.assertEqual(out["totals"]["calls"], 5)
        self.assertEqual([(d["day"], d["calls"]) for d in out["by_day"]], [("2026-10-06", 3), ("2026-10-07", 2)])
        self.assertIsNone(out["totals"]["cost"])
        d0, d1 = llm_usage.online_days(datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc),
                                       datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc))
        self.assertEqual((str(d0), str(d1)), ("2026-10-02", "2026-10-07"), "until 是開區間：台北 10/8 00:00 不算 10/8")
