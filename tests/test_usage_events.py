"""用量收集（app/services/usage_events.py、web/usage_middleware.py）與 llm_http 的 observer hook（不連 DB）。

守的是隱私規則與上限，不只是「有在算」：
- middleware 只計回 200 的四類請求；搜尋字串（`q`）永遠不出現在任何寫入參數裡。
- 只有「主題×日」（沒有 user_id）與「人×日×類別」（沒有主題）兩種形狀；配額三類不由這裡寫。
- 累加器有上限，超過的新鍵丟棄並計數；flush 失敗把那一批放回去。
- llm_http 交給 observer 的只有 metadata（沒有 prompt、回答），observer 例外不影響呼叫。
SQL 本身對真的 PostgreSQL 的行為在 tests/test_usage_events_db.py。
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
import uuid
from datetime import date
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from app import config, request_context
from app.services import llm_http, usage_events
from web import usage_middleware
from web.server import app as _server_app  # noqa: F401 — 收集期載入，conftest 才會裝上假帳號庫

HASH = "a" * 64
RID = "11111111-2222-3333-4444-555555555555"
U1 = "aaaaaaaa-0000-0000-0000-000000000001"
U2 = "aaaaaaaa-0000-0000-0000-000000000002"
SECRET = "機密查詢字串-XYZZY"


class ClassifyTests(unittest.TestCase):
    def test_counted_requests(self):
        c = usage_middleware.classify
        self.assertEqual(c("GET", f"/api/reading/{HASH}", b""), ("reading", HASH))
        self.assertEqual(c("GET", f"/api/report/{RID}/file", b""), ("report_file", RID))
        self.assertEqual(c("GET", "/api/search", b"q=abc&market=TW"), ("search", "TW"))
        self.assertEqual(c("GET", "/api/search", b"q=abc"), ("search", ""))
        self.assertEqual(c("POST", "/api/ask", b""), ("ask", ""))

    def test_not_counted(self):
        c = usage_middleware.classify
        for method, path in (("GET", f"/api/reading/{HASH}/text"), ("GET", f"/api/reading/{HASH}/similar"),
                             ("GET", f"/api/report/{RID}/full"), ("GET", "/api/ask"), ("POST", "/api/search"),
                             ("POST", "/api/ask/stop"), ("GET", "/api/reports"), ("GET", "/api/reading/short")):
            with self.subTest(path=path):
                self.assertIsNone(c(method, path, b""))

    def test_market_filter_only_accepts_market_codes(self):
        f = usage_middleware.market_filter
        self.assertEqual(f(b"market=US"), "US")
        self.assertEqual(f(b"market=" + SECRET.encode()), "")  # 不是市場代碼就不記
        self.assertEqual(f(b"q=TW"), "")  # 只看鍵名 market
        self.assertEqual(f(b"q=" + SECRET.encode() + b"&market=HK"), "HK")
        self.assertEqual(f(b"market=%E5%85%A8%E9%83%A8"), "")  # 「全部」


def _mini_app(status: int = 200):
    app = FastAPI()

    @app.get("/api/search")
    async def search(q: str = ""):
        return JSONResponse({"ok": True}, status_code=status)

    @app.get("/api/reading/{h}")
    async def reading(h: str):
        return JSONResponse({}, status_code=status)

    @app.post("/api/ask")
    async def ask():
        return PlainTextResponse("data: x\n\n", status_code=status)

    app.add_middleware(usage_middleware.UsageMiddleware)
    return app


class MiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.acc = usage_events.reset()

    def test_only_200_is_counted(self):
        for status in (403, 404, 429, 500):
            TestClient(_mini_app(status)).get("/api/search", params={"q": "x"})
        self.assertEqual(self.acc.size(), 0)

    def test_search_string_never_reaches_any_write(self):
        # middleware 註冊在使用者 contextvar 之外時也要能拿到身分：這裡用真的 server 的註冊順序驗。
        client = TestClient(_inner_app())
        for user in (U1, U2, U1):
            r = client.get("/api/search", params={"q": SECRET, "market": "TW"}, headers={"x-test-user": user})
            self.assertEqual(r.status_code, 200)
        client.get("/api/search", params={"q": SECRET}, headers={"x-test-user": U1})
        batch = self.acc.drain()
        params = usage_events.batch_params(batch)
        blob = repr(params)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("XYZZY", blob)
        daily = {(k, s): (h, u) for _d, k, s, h, u in batch.daily}
        self.assertEqual(daily[("search", "")], (4, 2))  # 總量：4 次、2 人
        self.assertEqual(daily[("search", "TW")], (3, 2))
        counters = {(uid, k): n for uid, _d, k, n in batch.counters}
        self.assertEqual(counters, {(U1, "search"): 3, (U2, "search"): 1})
        # 寫進 DB 的參數：usage_daily 沒有 user_id、usage_counter 沒有主題
        daily_params = next(p for sql, p in params if "usage_daily" in sql)
        self.assertNotIn(U1, repr(daily_params))
        counter_params = next(p for sql, p in params if "usage_counter" in sql)
        self.assertNotIn("TW", repr(counter_params))

    def test_flush_writes_contain_no_search_string(self):
        client = TestClient(_inner_app())
        client.get("/api/search", params={"q": SECRET, "market": "TW"}, headers={"x-test-user": U1})
        captured = []
        asyncio.run(usage_events.flush(self.acc, session_factory=_capturing_factory(captured)))
        self.assertTrue(captured)
        self.assertNotIn(SECRET, repr(captured))

    def test_ask_counts_only_the_total_and_never_the_quota_counter(self):
        TestClient(_inner_app()).post("/api/ask", headers={"x-test-user": U1})
        batch = self.acc.drain()
        self.assertEqual([(k, s, h) for _d, k, s, h, _u in batch.daily], [("ask", "", 1)])
        self.assertEqual(batch.counters, [])  # ask／export／upload 由配額服務寫
        self.assertTrue({"ask", "export", "upload"}.isdisjoint(usage_events.COUNTER_KINDS))

    def test_anonymous_request_counts_without_personal_row(self):
        TestClient(_inner_app()).get(f"/api/reading/{HASH}")
        batch = self.acc.drain()
        self.assertEqual({(k, s, u) for _d, k, s, _h, u in batch.daily}, {("reading", "", 0), ("reading", HASH, 0)})
        self.assertEqual(batch.counters, [])


def _inner_app():
    """與 web/server.py 同一個註冊順序：UsageMiddleware 先加（內層），設 contextvar 的那層後加（外層）。"""
    app = FastAPI()

    @app.get("/api/search")
    async def search(q: str = "", market: str = ""):
        return {"ok": True}

    @app.get("/api/reading/{h}")
    async def reading(h: str):
        return {}

    @app.post("/api/ask")
    async def ask():
        return PlainTextResponse("data: x\n\n")

    app.add_middleware(usage_middleware.UsageMiddleware)

    @app.middleware("http")
    async def as_user(request, call_next):
        token = request_context.set_user_id(request.headers.get("x-test-user"))
        try:
            return await call_next(request)
        finally:
            request_context.reset_user_id(token)

    return app


def _capturing_factory(captured: list, *, fail: bool = False):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt, params=None):
            if fail:
                raise RuntimeError("DB 掛了")
            captured.append((str(stmt), params))

        async def commit(self):
            pass

    return lambda: _Session()


class AccumulatorTests(unittest.TestCase):
    def test_distinct_users_survive_flushes_within_the_day(self):
        acc = usage_events.UsageAccumulator()
        acc.record_hit("reading", HASH, U1)
        acc.record_hit("reading", HASH, U2)
        first = {(k, s): (h, u) for _d, k, s, h, u in acc.drain().daily}
        self.assertEqual(first[("reading", HASH)], (2, 2))
        acc.record_hit("reading", HASH, U1)  # 同一人再看一次：hits 增量 1，users 仍是 2（GREATEST 合併）
        second = {(k, s): (h, u) for _d, k, s, h, u in acc.drain().daily}
        self.assertEqual(second[("reading", HASH)], (1, 2))
        self.assertEqual(acc.drain().daily, [])  # 沒有新的請求就不寫

    def test_previous_days_are_released_after_drain(self):
        acc = usage_events.UsageAccumulator()
        acc.record_hit("reading", HASH, U1, day=date(2020, 1, 1))
        acc.drain()
        self.assertEqual(acc.size(), 0)

    def test_cap_drops_new_keys_and_counts_them(self):
        acc = usage_events.UsageAccumulator(max_keys=3)
        for i in range(10):
            acc.record_hit("reading", f"{i:064x}", None)
        self.assertLessEqual(acc.size(), 3)
        self.assertGreater(acc.dropped, 0)
        acc.record_hit("reading", "", None)  # 已存在的鍵照常累加
        batch = acc.drain()
        self.assertEqual(batch.dropped, acc.dropped)
        totals = {(k, s): h for _d, k, s, h, _u in batch.daily}
        self.assertEqual(totals[("reading", "")], 11)

    def test_flush_logs_dropped_counts(self):
        acc = usage_events.UsageAccumulator(max_keys=1)
        acc.record_hit("reading", HASH, None)
        with self.assertLogs("app.services.usage_events", "WARNING") as logs:
            asyncio.run(usage_events.flush(acc, session_factory=_capturing_factory([])))
        self.assertIn("丟棄", "\n".join(logs.output))

    def test_failed_flush_restores_the_batch(self):
        acc = usage_events.UsageAccumulator()
        acc.record_hit("search", "TW", U1)
        with self.assertLogs("app.services.usage_events", "WARNING"):
            ok = asyncio.run(usage_events.flush(acc, session_factory=_capturing_factory([], fail=True)))
        self.assertFalse(ok)
        captured = []
        self.assertTrue(asyncio.run(usage_events.flush(acc, session_factory=_capturing_factory(captured))))
        self.assertEqual(len(captured), 2)  # usage_daily ＋ usage_counter，那一批沒有遺失

    def test_unknown_kind_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            usage_events.UsageAccumulator().record_hit("downloads", None, None)

    def test_invalid_user_id_is_not_recorded_as_a_person(self):
        acc = usage_events.UsageAccumulator()
        acc.record_hit("search", "", "not-a-uuid")
        batch = acc.drain()
        self.assertEqual(batch.counters, [])
        self.assertEqual(batch.daily[0][4], 0)


class LlmObserverTests(unittest.TestCase):
    def setUp(self):
        self.acc = usage_events.reset()

    def _chat(self, kind=None, usage=None):
        return llm_http.ChatOutcome(kind=kind, usage=usage, total_ms=1200, model_resp="deepseek-v4-flash")

    def test_metadata_has_no_prompt_or_answer(self):
        seen = []
        llm_http.add_observer(seen.append)
        try:
            llm_http._log_call("ask_answer", "deepseek-flash", self._chat(usage={
                "prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 20, "completion_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 0}}))
        finally:
            llm_http.remove_observer(seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(set(seen[0]), {"task", "model", "model_resp", "kind", "finish_reason", "attempts",
                                        "ttft_ms", "total_ms", "tokens"})
        self.assertEqual(seen[0]["tokens"], {"hit": 10, "miss": 20, "completion": 30, "reasoning": 0})

    def test_observer_errors_do_not_break_the_call(self):
        def boom(_meta):
            raise RuntimeError("observer 壞了")

        llm_http.add_observer(boom)
        try:
            with self.assertLogs("app.services.llm_http", "WARNING"):
                llm_http._observer_warned = False
                llm_http._log_call("ask_answer", "deepseek-flash", self._chat())
        finally:
            llm_http.remove_observer(boom)

    def test_usage_is_attributed_to_the_request_user(self):
        token = request_context.set_user_id(U1)
        try:
            usage_events.observe_llm_call(llm_http.call_metadata(
                "faithfulness", "deepseek-flash", self._chat(usage={"completion_tokens": 5})))
            usage_events.observe_llm_call({**llm_http.call_metadata(
                "faithfulness", "deepseek-flash", self._chat(kind="timeout")), "prompt": SECRET})
        finally:
            request_context.reset_user_id(token)
        usage_events.observe_llm_call(llm_http.call_metadata("ask_answer", "deepseek-flash", self._chat()))
        batch = self.acc.drain()
        rows = {(uid, task): cell for _d, uid, task, _m, cell in batch.llm}
        mine = rows[(U1, "faithfulness")]
        self.assertEqual((mine.calls, mine.failures, mine.completion, mine.without_tokens), (2, 1, 5, 1))
        self.assertIn((None, "ask_answer"), rows)  # 沒有請求身分＝NULL
        self.assertNotIn(SECRET, repr(usage_events.batch_params(batch)))


class LifespanWiringTests(unittest.TestCase):
    def tearDown(self):
        llm_http.remove_observer(usage_events.observe_llm_call)

    def test_disabled_by_conftest(self):
        from web import server

        self.assertFalse(config.get_settings().usage_events_enabled)
        self.assertIsNone(server._start_usage_collection())
        self.assertNotIn(usage_events.observe_llm_call, llm_http._OBSERVERS)

    def test_enabled_registers_observer_and_starts_flusher(self):
        from web import server

        started = []

        class _Flusher:
            def __init__(self, interval):
                self.interval = interval

            def start(self):
                started.append(self.interval)

        on = dataclasses.replace(config.get_settings(), usage_events_enabled=True)
        with mock.patch.object(config, "_SETTINGS", on), mock.patch.object(usage_events, "UsageFlusher", _Flusher):
            flusher = server._start_usage_collection()
        self.assertIsNotNone(flusher)
        self.assertEqual(started, [60.0])
        self.assertIn(usage_events.observe_llm_call, llm_http._OBSERVERS)

    def test_request_context_user_id_roundtrip(self):
        self.assertIsNone(request_context.current_user_id())
        uid = str(uuid.uuid4())
        token = request_context.set_user_id(uid)
        try:
            self.assertEqual(request_context.current_user_id(), uid)
        finally:
            request_context.reset_user_id(token)
        self.assertIsNone(request_context.current_user_id())


class ServerUsageWiringTests(unittest.TestCase):
    """真的 app：已登入的閱讀頁 200 計到那個人；未登入（401）不計。"""

    def setUp(self):
        self.acc = usage_events.reset()

    def test_logged_in_request_is_attributed(self):
        """把 classify 換成「這個請求要計」，打最便宜的 200 端點：驗 middleware 拿得到 require_login 設的身分。

        （不打真的 /api/search：它會載入嵌入模型。路徑分類本身由 ClassifyTests 驗。）
        """
        from fake_accounts import _default_user_id, session_cookies

        from web.server import app

        client = TestClient(app, base_url="http://127.0.0.1")
        client.cookies.update(session_cookies())
        with mock.patch.object(usage_middleware, "classify", return_value=("search", "TW")):
            ok = client.get("/api/me")
        self.assertEqual(ok.status_code, 200)
        counters = {(uid, k) for uid, _d, k, _n in self.acc.drain().counters}
        self.assertEqual(counters, {(_default_user_id(), "search")})

    def test_unauthenticated_is_not_counted(self):
        from web.server import app

        with mock.patch.object(usage_middleware, "classify", return_value=("search", "")):
            r = TestClient(app, base_url="http://127.0.0.1").get("/api/me")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.acc.size(), 0)


if __name__ == "__main__":
    unittest.main()
