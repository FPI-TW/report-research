# tests/test_healthz_llm.py
"""`/healthz/llm`：LLM 帳號可用性（金鑰、端點、額度），只回答本機直連。

設計理由在 `app/services/llm_health.py` 的模組 docstring。這裡守的是判斷邏輯的每一個分支：
探測結果對應的狀態、額度閂鎖與解除條件（只有真實請求成功才解除）、連續兩次才算連不上、快取時效、
主答設定錯誤的 `misconfigured`（只看主答，審查 M15 的粒度）。

全程不連網：`llm_http._transport` 換成 `httpx.MockTransport`；金鑰只用 gitleaks allowlist 內的假值，
以 `mock.patch.dict` 限定在單一測試內（conftest 把 `OPENAI_API_KEY` 強制成空字串）。
"""
import asyncio
import dataclasses
import inspect
import os
import re
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.services import llm_health, llm_http  # noqa: E402
from web import server  # noqa: E402
from web.routers import health  # noqa: E402
from web.server import app  # noqa: E402

PROBE = Path(__file__).resolve().parents[1] / "scripts" / "check_web_health.sh"

FAKE_KEY = "fixed-test-secret-openai0"
BASE = "https://api.example.test"
ONLINE = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_BASE_URL": BASE, "LLM_PROVIDER": "openai"}
# 主答解析到白名單外的名稱（PR-M 起沒有其他 backend：每題都以設定錯誤失敗），金鑰照常有值。
MISCONF = {"OPENAI_API_KEY": FAKE_KEY, "OPENAI_BASE_URL": BASE, "LLM_PROVIDER": "openai",
           "ASK_ANSWER_MODEL": "claude-sonnet-5"}


MODELS = {"object": "list", "data": [{"id": "gpt-6-luna", "object": "model"}]}
QUOTA_BODY = {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}


def _local() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", client=("127.0.0.1", 51000))


class _Base(unittest.TestCase):
    def setUp(self):
        llm_health.reset()
        self._quota, self._ok = llm_http._quota_seen_at, llm_http._ok_seen_at
        llm_http._quota_seen_at = llm_http._ok_seen_at = 0.0
        self.requests: list[httpx.Request] = []
        self.handler = lambda req: httpx.Response(200, json=MODELS)

        def recording(request):
            self.requests.append(request)
            return self.handler(request)

        llm_http._transport = httpx.MockTransport(recording)
        llm_http._reset_clients()

    def tearDown(self):
        llm_health.reset()
        llm_http._quota_seen_at, llm_http._ok_seen_at = self._quota, self._ok
        llm_http._transport = None
        llm_http._reset_clients()

    def reply(self, status=200, body=None, **kw):
        self.handler = lambda req: httpx.Response(status, json=body, **kw)

    def get(self, env=ONLINE, client=None):
        with mock.patch.dict(os.environ, env):
            return (client or _local()).get("/healthz/llm")

    def expire(self):
        """快取到期（ok 600 秒／失敗 60 秒都一樣：直接把期限撥到過去）。"""
        llm_health._snap = llm_health._snap._replace(expires_at=0.0)

    def assertState(self, r, status, state):
        self.assertEqual((r.status_code, r.json()), (status, {"llm": state}), r.text)


class AccessTests(_Base):
    def test_only_direct_loopback_gets_an_answer(self):
        """它在 auth 白名單裡，所以『對外等於不存在』要靠 handler 自己守住；被拒的請求不探端點。"""
        with mock.patch.dict(os.environ, ONLINE):
            outside = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1").get("/healthz/llm")
            proxied = _local().get("/healthz/llm", headers={"X-Forwarded-For": "203.0.113.9"})
            public_host = TestClient(
                app, base_url="http://research.example.com", client=("127.0.0.1", 51000)
            ).get("/healthz/llm")
        for r in (outside, proxied, public_host):
            self.assertEqual(r.status_code, 404)
            self.assertNotIn("llm", r.json())
        self.assertEqual(self.requests, [])

    def test_no_login_needed_locally(self):
        """白名單：本機探針沒有 session cookie，不能被導向 /login。"""
        r = self.get()
        self.assertState(r, 200, "ok")

    def test_allowlist_is_exact_not_a_healthz_prefix(self):
        """白名單是精確集合：`/healthz` 開頭的其他路徑照樣要登入（放寬成前綴等於把未來任何
        `/healthz/*` 端點都免認證對外開放，而只有列出的這幾支自己守了本機直連；/healthz/security 是 Admin v2 加的，
        /healthz/feed 是來源主機信標，/readyz 是容器部署的就緒探測）。"""
        self.assertEqual(server._AUTH_ALLOWLIST, {"/login", "/healthz", "/readyz", "/healthz/storage", "/healthz/llm",
                                                  "/healthz/security", "/healthz/feed"})
        for path in ("/healthz/llm/", "/healthz/llmx", "/healthz/other", "/healthzz", "/healthz/storage/x",
                     "/healthz/security/x", "/healthz/feed/x", "/readyz/", "/readyz/x"):
            with self.subTest(path=path):
                r = _local().get(path)
                self.assertEqual((r.status_code, r.headers.get("location")), (302, "/login"), path)

    def test_query_shape(self):
        self.get()
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual((req.method, str(req.url)), ("GET", f"{BASE}/models"))
        self.assertEqual(req.headers["authorization"], f"Bearer {FAKE_KEY}")


class NoKeyTests(_Base):
    def test_no_key_but_online_http_is_auth_failed(self):
        r = self.get({"LLM_PROVIDER": "openai"})
        self.assertState(r, 503, "auth_failed")
        self.assertEqual(self.requests, [])


class ProbeJudgementTests(_Base):
    def test_models_200_is_ok_whatever_the_body_says(self):
        """`GET /models` 只判斷端點與金鑰：200 就是 ok，本體不解析（兩條路徑都沒有餘額可查）。"""
        for handler in (
            lambda req: httpx.Response(200, json=MODELS),
            lambda req: httpx.Response(200, json={"object": "list", "data": []}),
            lambda req: httpx.Response(200, text="<html>not json</html>"),
        ):
            llm_health.reset()
            self.handler = handler
            self.assertState(self.get(), 200, "ok")

    def test_quota_answers_are_exhausted(self):
        for status, body in ((402, {"error": {"message": "Payment required"}}), (429, QUOTA_BODY)):
            with self.subTest(status=status):
                llm_health.reset()
                self.reply(status, body)
                self.assertState(self.get(), 503, "exhausted")

    def test_401_is_auth_failed(self):
        self.reply(401, {"error": {"message": "Incorrect API key"}})
        self.assertState(self.get(), 503, "auth_failed")

    def test_key_without_models_scope_is_ok(self):
        """限制權限的金鑰沒有 Models 讀取權限：伺服器認得金鑰才回缺哪個 scope，不是停擺（誤報會開 CRITICAL 事件）。"""
        self.reply(403, {"error": {"message": "You have insufficient permissions for this operation. "
                                              "Missing scopes: api.model.read."}})
        self.assertState(self.get(), 200, "ok")

    def test_other_403_is_auth_failed(self):
        self.reply(403, {"error": {"message": "Country, region, or territory not supported"}})
        self.assertState(self.get(), 503, "auth_failed")

    def test_unexpected_answers_are_indeterminate(self):
        for status, body in ((404, {"error": {"message": "Not Found"}}), (400, {"error": {"message": "bad"}}),
                             (418, {})):
            with self.subTest(status=status):
                llm_health.reset()
                self.reply(status, body)
                self.assertState(self.get(), 503, "indeterminate")

    def test_opencode_go_probes_the_go_endpoint_with_the_go_key(self):
        go_base = "https://go.example.test/v1"
        env = {"LLM_PROVIDER": "opencode_go", "OPENCODE_GO_API_KEY": FAKE_KEY, "OPENCODE_GO_BASE_URL": go_base,
               "OPENAI_API_KEY": ""}
        self.assertState(self.get(env), 200, "ok")
        req = self.requests[0]
        self.assertEqual(str(req.url), f"{go_base}/models")
        self.assertEqual(req.headers["authorization"], f"Bearer {FAKE_KEY}")
        self.assertEqual(req.headers["user-agent"], llm_http.GO_USER_AGENT)

    def test_no_low_state_and_no_balance_logic(self):
        """沒有餘額可查就沒有「快用完」：LOW、judge_balance、fetch_balance 都移除，FAILING 不含它。"""
        for name in ("LOW", "judge_balance"):
            self.assertFalse(hasattr(llm_health, name), name)
        for name in ("fetch_balance", "BalanceResult"):
            self.assertFalse(hasattr(llm_http, name), name)
        self.assertNotIn("low", llm_health.FAILING)
        self.assertEqual(llm_health.FAILING, frozenset({"exhausted", "auth_failed", "unreachable", "indeterminate",
                                                        "misconfigured"}))
        self.assertEqual(list(inspect.signature(llm_health.report).parameters), [])


class ResponseShapeTests(_Base):
    def test_response_has_only_the_llm_key(self):
        for status, body, want in ((200, MODELS, 200), (402, {"error": {"message": "secret detail"}}, 503)):
            llm_health.reset()
            self.reply(status, body)
            r = self.get()
            self.assertEqual(r.status_code, want)
            self.assertEqual(set(r.json()), {"llm"})
            self.assertNotIn("secret detail", r.text)

    def test_detail_goes_to_the_log_only(self):
        self.reply(401, {"error": {"message": "Incorrect API key provided"}})
        with self.assertLogs("app.services.llm_health", "WARNING") as cm:
            r = self.get()
        self.assertNotIn("Incorrect", r.text)
        self.assertTrue(any("auth_failed" in line and "Incorrect" in line for line in cm.output), cm.output)

    def test_healthz_itself_is_untouched(self):
        """`/healthz` 的回應只有 `status` 一個鍵，LLM 帳號壞掉也不能讓它變 503。"""
        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return None

        self.reply(402, {"error": {"message": "Insufficient Balance"}})
        self.assertEqual(self.get().status_code, 503)
        health._cache = (0.0, False)
        try:
            with mock.patch.object(health.deps, "SessionFactory", lambda: _FakeSession()), \
                    mock.patch.dict(os.environ, ONLINE):
                r = TestClient(app, base_url="http://127.0.0.1").get("/healthz")
        finally:
            health._cache = (0.0, False)
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))


class CacheAndStreakTests(_Base):
    def test_ok_is_cached_for_600_seconds(self):
        c = _local()
        bodies = [self.get(client=c).json() for _ in range(4)]
        self.assertEqual(bodies, [{"llm": "ok"}] * 4)
        self.assertEqual(len(self.requests), 1)
        remaining = llm_health._snap.expires_at - time.monotonic()
        self.assertGreater(remaining, 590)
        self.assertLessEqual(remaining, 600)

    def test_failure_states_are_cached_for_60_seconds(self):
        for status, body in ((402, {}), (401, {}), (404, {}), (429, QUOTA_BODY)):
            with self.subTest(status=status, body=body):
                llm_health.reset()
                self.reply(status, body)
                self.get()
                remaining = llm_health._snap.expires_at - time.monotonic()
                self.assertGreater(remaining, 50)
                self.assertLessEqual(remaining, 60)

    def test_refetches_after_expiry(self):
        self.get()
        self.reply(401, {})
        self.expire()
        self.assertState(self.get(), 503, "auth_failed")
        self.assertEqual(len(self.requests), 2)

    def _network_down(self):
        def boom(req):
            raise httpx.ConnectError("connection refused", request=req)
        self.handler = boom

    def test_single_network_failure_is_not_unreachable_two_in_a_row_is(self):
        self._network_down()
        first = self.get()
        self.expire()
        second = self.get()
        self.assertState(first, 200, "unknown")
        self.assertState(second, 503, "unreachable")

    def test_server_errors_and_rate_limits_count_as_unreachable_too(self):
        """429 限流與 5xx 同類：單次不算（不是額度用完）。"""
        for status in (500, 503, 429):
            with self.subTest(status=status):
                llm_health.reset()
                self.reply(status, {"error": {"message": "busy"}})
                first = self.get()
                self.expire()
                second = self.get()
                self.assertState(first, 200, "unknown")
                self.assertState(second, 503, "unreachable")

    def test_single_failure_keeps_previous_state_but_retries_within_60s(self):
        """ok 之後一次連不上：仍回 ok，但下一次查詢在 60 秒內（不是沿用 600 秒）。"""
        self.get()
        self._network_down()
        self.expire()
        self.assertState(self.get(), 200, "ok")
        self.assertEqual(llm_health._snap.consecutive_failures, 1)
        self.assertLessEqual(llm_health._snap.expires_at - time.monotonic(), 60)
        self.expire()
        self.assertState(self.get(), 503, "unreachable")

    def test_recovery_clears_the_streak(self):
        self._network_down()
        self.get()
        self.reply(body=MODELS)
        self.expire()
        self.assertState(self.get(), 200, "ok")
        self.assertEqual(llm_health._snap.consecutive_failures, 0)
        self._network_down()
        self.expire()
        self.assertState(self.get(), 200, "ok")  # 恢復後重新起算：又只是第一次

    def test_non_transient_error_resets_the_streak(self):
        self._network_down()
        self.get()
        self.reply(401, {})
        self.expire()
        self.get()
        self.assertEqual(llm_health._snap.consecutive_failures, 0)

    def test_timeout_counts_as_unreachable(self):
        async def slow(req):
            await asyncio.sleep(5)
            return httpx.Response(200, json=MODELS)

        self.handler = slow
        with mock.patch.object(llm_health, "FETCH_TIMEOUT", 0.1), mock.patch.object(llm_health, "WAIT", 1.0):
            first = self.get()
            self.expire()
            second = self.get()
        self.assertState(first, 200, "unknown")
        self.assertState(second, 503, "unreachable")

    def test_waits_at_most_wait_seconds_then_returns_previous(self):
        """查詢比等待上限慢時回上一次的結論，不讓探針的 curl（5 秒）逾時。"""
        self.get()  # ok

        async def slow(req):
            await asyncio.sleep(1.0)
            return httpx.Response(402, json={})

        self.handler = slow
        self.expire()
        with mock.patch.object(llm_health, "WAIT", 0.1):  # 查詢本身（FETCH_TIMEOUT 3.5 秒）比它慢
            t0 = time.monotonic()
            r = self.get()
            elapsed = time.monotonic() - t0
        self.assertState(r, 200, "ok")
        self.assertLess(elapsed, 0.9)


class QuotaLatchTests(_Base):
    """本行程的真實請求碰到額度用完 → 立刻 exhausted，直到之後有一次**真實請求成功**（`last_ok_at`）。
    `/models` 回 200 說明不了額度回來了沒，所以探測不解除閂鎖。"""

    def test_real_quota_flips_cached_ok_immediately(self):
        c = _local()
        self.assertState(self.get(client=c), 200, "ok")
        llm_http._quota_seen_at = time.monotonic()
        self.assertState(self.get(client=c), 503, "exhausted")
        self.assertEqual(len(self.requests), 1, "閂鎖本身不需要等探測")

    def test_successful_probe_does_not_clear_the_latch(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        self.assertState(self.get(), 503, "exhausted")
        # ok 的快取還有將近 600 秒，但閂鎖期間以 60 秒計：撥過 60 秒就重探，/models 200 仍不解除
        llm_health._snap = llm_health._snap._replace(checked_at=time.monotonic() - 61)
        self.assertState(self.get(), 503, "exhausted")
        self.assertEqual(len(self.requests), 2)

    def test_successful_real_request_clears_the_latch(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        self.assertState(self.get(), 503, "exhausted")
        llm_http._ok_seen_at = time.monotonic() + 1
        self.assertState(self.get(), 200, "ok")

    def test_ok_before_the_quota_does_not_clear(self):
        llm_http._ok_seen_at = time.monotonic()
        llm_http._quota_seen_at = llm_http._ok_seen_at + 1
        self.assertState(self.get(), 503, "exhausted")

    def test_latch_not_reprobed_before_fail_ttl(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        llm_health._snap = llm_health._snap._replace(checked_at=time.monotonic() - 30)
        self.assertState(self.get(), 503, "exhausted")
        self.assertEqual(len(self.requests), 1)

    def test_failed_probe_does_not_clear(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()

        def boom(req):
            raise httpx.ConnectError("down", request=req)

        self.handler = boom
        llm_health._snap = llm_health._snap._replace(checked_at=0.0, expires_at=0.0)
        self.assertState(self.get(), 503, "exhausted")

    def test_non_success_probe_keeps_exhausted_not_indeterminate(self):
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(404, {"error": {"message": "Not Found"}})
        self.assertState(self.get(), 503, "exhausted")

    def test_auth_failure_takes_precedence_over_the_latch(self):
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(401, {})
        self.assertState(self.get(), 503, "auth_failed")

    def test_latch_applies_before_first_probe_completes(self):
        async def slow(req):
            await asyncio.sleep(1.0)
            return httpx.Response(200, json=MODELS)

        llm_http._quota_seen_at = time.monotonic()
        self.handler = slow
        with mock.patch.object(llm_health, "WAIT", 0.05):
            self.assertState(self.get(), 503, "exhausted")


class MisconfiguredTests(_Base):
    """主答（`ASK_ANSWER`）解析到白名單外的名稱：每題都以設定錯誤失敗＝問答停擺，回 503 `misconfigured`
    （探針 reason `llm_misconfigured`、退出碼 8），不探端點。"""

    def test_non_whitelisted_main_answer_is_503_misconfigured_without_query(self):
        for model in ("claude-sonnet-5", "sonnet", "deepseek-flash", "off", "GPT-6-Luna"):
            with self.subTest(model=model):
                llm_health.reset()
                self.assertState(self.get({**MISCONF, "ASK_ANSWER_MODEL": model}), 503, "misconfigured")
        self.assertEqual(self.requests, [], "設定錯誤時帳號狀態回答不了問答能不能用，不查")

    def test_misconfigured_wins_over_every_account_state(self):
        """帳號壞掉、額度閂鎖、沒有金鑰都一樣：先說設定錯誤（修好設定後下一次查詢自然說出帳號狀態）。"""
        cases = [
            ("402", lambda: self.reply(402, {}), MISCONF),
            ("401", lambda: self.reply(401, {}), MISCONF),
            ("latch", lambda: setattr(llm_http, "_quota_seen_at", time.monotonic() + 1000), MISCONF),
            ("no_key", lambda: None, {"ASK_ANSWER_MODEL": "claude-sonnet-5"}),
        ]
        for name, arrange, env in cases:
            with self.subTest(case=name):
                llm_health.reset()
                arrange()
                self.assertState(self.get(env), 503, "misconfigured")

    def test_fixing_the_config_reveals_the_account_state(self):
        self.reply(402, {})
        self.assertState(self.get(MISCONF), 503, "misconfigured")
        self.assertState(self.get({**MISCONF, "ASK_ANSWER_MODEL": "gpt-6-luna"}), 503, "exhausted")
        self.assertState(self.get({**MISCONF, "ASK_ANSWER_MODEL": ""}), 503, "exhausted")

    def test_whitelisted_main_answer_is_not_misconfigured(self):
        for model in ("gpt-6-luna", ""):
            with self.subTest(model=model):
                llm_health.reset()
                self.assertState(self.get({**ONLINE, "ASK_ANSWER_MODEL": model}), 200, "ok")

    def test_fail_open_online_tasks_do_not_count(self):
        """審查低2：路由、改寫、規劃、追問、忠實度、網搜都 fail-open（網搜沒有後端），它們解析到白名單外名稱
        時問答照樣答得出來——不能因此開「問答停擺」的事件。逐一只讓一個設錯，也試全部一起。"""
        knobs = ("ASK_INTENT_MODEL", "ASK_CONDENSE_MODEL", "QA_PLANNER_MODEL", "ASK_FOLLOWUP_MODEL",
                 "FAITHFULNESS_MODEL", "ASK_WEB_MODEL")
        for chosen in [(k,) for k in knobs] + [knobs]:
            with self.subTest(knobs=chosen):
                llm_health.reset()
                env = {**ONLINE, **{k: "claude-sonnet-5" for k in chosen}}
                self.assertState(self.get(env), 200, "ok")

    def test_retired_provider_values_do_not_hide_an_outage(self):
        """退役值（`deepseek`、`claude_*`）解析成 openai——主答照樣走 OpenAI，帳號停擺要 503 `exhausted`，
        不是 misconfigured（主答名稱本身沒錯）。"""
        for value in ("deepseek", "claude_cli", "claude_only"):
            with self.subTest(value=value):
                llm_health.reset()
                self.reply(402, {})
                self.assertState(self.get({**ONLINE, "LLM_PROVIDER": value}), 503, "exhausted")

    def test_misconfigured_is_logged_with_the_value(self):
        with self.assertLogs("app.services.llm_health", "WARNING") as cm:
            self.get(MISCONF)
        line = "\n".join(cm.output)
        self.assertIn("misconfigured", line)
        self.assertIn("claude-sonnet-5", line)

    def test_probe_reason_is_accepted_and_exit_8(self):
        """探針的 reason 詞彙封閉規則（`llm_` + 小寫字母與底線、最長 32 字）要接受它。"""
        self.assertRegex(llm_health.MISCONFIGURED, r"^[a-z_]{1,32}$")
        self.assertIn(llm_health.MISCONFIGURED, llm_health.FAILING)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """真正的並行（同一個 event loop 上同時進行的請求與探測），不是撥時鐘模擬。"""

    def setUp(self):
        llm_health.reset()
        self._quota, self._ok = llm_http._quota_seen_at, llm_http._ok_seen_at
        llm_http._quota_seen_at = llm_http._ok_seen_at = 0.0
        self.requests: list[httpx.Request] = []
        self.delay = 0.3
        self.status, self.body = 200, MODELS

        async def slow(req):
            self.requests.append(req)
            await asyncio.sleep(self.delay)
            return httpx.Response(self.status, json=self.body)

        llm_http._transport = httpx.MockTransport(slow)
        llm_http._reset_clients()
        env = mock.patch.dict(os.environ, ONLINE)
        env.start()
        self.addCleanup(env.stop)

    async def asyncTearDown(self):
        await llm_http.aclose()

    def tearDown(self):
        llm_health.reset()
        llm_http._quota_seen_at, llm_http._ok_seen_at = self._quota, self._ok
        llm_http._transport = None
        llm_http._reset_clients()

    @staticmethod
    def _report():
        return llm_health.report()

    async def test_quota_arriving_while_a_probe_is_in_flight_latches(self):
        """探測進行中碰到額度用完：探測回 ok 也不算數。"""
        async def hit_quota():
            await asyncio.sleep(0.1)
            llm_http._quota_seen_at = time.monotonic()

        (state, status), _ = await asyncio.gather(self._report(), hit_quota())
        self.assertEqual((state, status), ("exhausted", 503))
        self.assertEqual(await self._report(), ("exhausted", 503), "快取期間閂鎖仍在")
        self.assertEqual(len(self.requests), 1)

    async def test_concurrent_requests_share_one_probe(self):
        results = await asyncio.gather(self._report(), self._report(), self._report())
        self.assertEqual(results, [("ok", 200)] * 3)
        self.assertEqual(len(self.requests), 1, "同時進來的請求要共用同一個探測 task")

    async def test_slow_probe_is_not_cancelled_by_the_wait_limit(self):
        """等不到（WAIT）就先回上一次的結論，但探測不能被取消（shield）：它在背景跑完後要更新狀態。"""
        self.status, self.body = 402, {"error": {"message": "Payment required"}}
        with mock.patch.object(llm_health, "WAIT", 0.05):
            self.assertEqual(await self._report(), ("unknown", 200))
            await asyncio.sleep(self.delay + 0.2)
            self.assertEqual(llm_health._snap.state, "exhausted", "背景探測沒有完成（被 wait_for 取消了？）")
            self.assertEqual(await self._report(), ("exhausted", 503))
        self.assertEqual(len(self.requests), 1)


class DeadlineOrderTests(unittest.TestCase):
    def test_fetch_timeout_below_wait_below_probe_curl_timeout(self):
        """FETCH_TIMEOUT < WAIT < 探針的 HEALTH_TIMEOUT：端點一定在探針的 curl 放棄前回答（否則 curl 逾時
        被當成「判不出來」、停擺被靜默），而探測逾時在同一個請求裡就有結論。"""
        m = re.search(r'^HEALTH_TIMEOUT="\$\{HEALTH_TIMEOUT:-(\d+)\}"', PROBE.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m)
        self.assertLess(llm_health.FETCH_TIMEOUT, llm_health.WAIT)
        self.assertLess(llm_health.WAIT, int(m.group(1)))


class QuotaHookTests(unittest.IsolatedAsyncioTestCase):
    """`llm_http` 在線上與批次兩條路徑碰到額度用完（402、429 insufficient_quota）都要記下時刻；其他錯誤不記。"""

    def setUp(self):
        self._quota, self._ok = llm_http._quota_seen_at, llm_http._ok_seen_at
        llm_http._quota_seen_at = llm_http._ok_seen_at = 0.0

    def tearDown(self):
        llm_http._quota_seen_at, llm_http._ok_seen_at = self._quota, self._ok
        llm_http._transport = None
        llm_http._reset_clients()

    def _install(self, status, body=None):
        llm_http._transport = httpx.MockTransport(
            lambda req: httpx.Response(status, json=body or {"error": {"message": "x"}})
        )
        llm_http._reset_clients()

    async def _stream_once(self):
        with mock.patch.dict(os.environ, ONLINE):
            async for item in llm_http.astream_chat("gpt-6-luna", "hi", max_tokens=8, first_token_timeout=5):
                last = item
        return last

    async def test_online_402_is_recorded(self):
        self._install(402)
        before = time.monotonic()
        out = await self._stream_once()
        self.assertEqual(out.kind, llm_http.QUOTA)
        self.assertGreaterEqual(llm_http.last_quota_at(), before)

    async def test_online_429_insufficient_quota_is_recorded(self):
        self._install(429, QUOTA_BODY)
        out = await self._stream_once()
        self.assertEqual(out.kind, llm_http.QUOTA)
        self.assertGreater(llm_http.last_quota_at(), 0.0)

    async def test_online_other_errors_are_not(self):
        for status in (401, 400, 429, 500):
            self._install(status)
            await self._stream_once()
        self.assertEqual(llm_http.last_quota_at(), 0.0)

    async def test_online_success_records_last_ok(self):
        body = (b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n")
        llm_http._transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
        llm_http._reset_clients()
        before = time.monotonic()
        out = await self._stream_once()
        self.assertIsNone(out.kind)
        self.assertGreaterEqual(llm_http.last_ok_at(), before)

    async def test_batch_402_is_recorded(self):
        self._install(402)
        with mock.patch.dict(os.environ, ONLINE):
            res = await asyncio.to_thread(
                llm_http.complete_chat, "gpt-6-luna", "hi", max_tokens=8, timeout=5, sleep=lambda s: None
            )
        self.assertEqual(res.kind, llm_http.QUOTA)
        self.assertGreater(llm_http.last_quota_at(), 0.0)

    async def test_probe_402_does_not_arm_the_latch(self):
        """探測自己的 402 直接判 exhausted，不經閂鎖；探測成功也不算真實請求成功。"""
        self._install(402)
        with mock.patch.dict(os.environ, ONLINE):
            res = await llm_http.probe_endpoint()
        self.assertEqual(res.kind, llm_http.QUOTA)
        self.assertEqual(llm_http.last_quota_at(), 0.0)
        self._install(200, MODELS)
        with mock.patch.dict(os.environ, ONLINE):
            res = await llm_http.probe_endpoint()
        self.assertIsNone(res.kind)
        self.assertEqual(llm_http.last_ok_at(), 0.0)


class ProbeEndpointTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        llm_http._transport = None
        llm_http._reset_clients()

    async def _probe(self, handler, env=ONLINE, timeout=4.0):
        llm_http._transport = httpx.MockTransport(handler)
        llm_http._reset_clients()
        with mock.patch.dict(os.environ, env):
            return await llm_http.probe_endpoint(timeout)

    async def test_ok(self):
        res = await self._probe(lambda req: httpx.Response(200, json=MODELS))
        self.assertIsInstance(res, llm_http.ProbeResult)
        self.assertEqual((res.kind, res.status), (None, 200))

    async def test_error_kinds(self):
        def connect(req):
            raise httpx.ConnectError("refused", request=req)

        def read_timeout(req):
            raise httpx.ReadTimeout("slow", request=req)

        cases = [
            (lambda req: httpx.Response(401, json={}), llm_http.AUTH),
            (lambda req: httpx.Response(402, json={}), llm_http.QUOTA),
            (lambda req: httpx.Response(429, json=QUOTA_BODY), llm_http.QUOTA),
            (lambda req: httpx.Response(429, json={"error": {"message": "Rate limit reached"}}), llm_http.OVERLOADED),
            (lambda req: httpx.Response(500, json={}), llm_http.OVERLOADED),
            (lambda req: httpx.Response(404, json={}), llm_http.CONFIG),
            (connect, llm_http.NETWORK),
            (read_timeout, llm_http.TIMEOUT),
        ]
        for handler, kind in cases:
            with self.subTest(kind=kind):
                res = await self._probe(handler)
                self.assertEqual(res.kind, kind)

    async def test_overall_timeout(self):
        async def slow(req):
            await asyncio.sleep(5)
            return httpx.Response(200, json=MODELS)

        t0 = time.monotonic()
        res = await self._probe(slow, timeout=0.2)
        self.assertEqual(res.kind, llm_http.TIMEOUT)
        self.assertLess(time.monotonic() - t0, 2)

    async def test_missing_key_is_auth_without_request(self):
        seen = []
        res = await self._probe(lambda req: seen.append(req) or httpx.Response(200, json={}),
                                env={"OPENAI_API_KEY": ""})
        self.assertEqual(res.kind, llm_http.AUTH)
        self.assertEqual(seen, [])


class KnobTests(unittest.TestCase):
    def test_budget_knobs_are_gone(self):
        """沒有餘額判定就沒有預算旋鈕：Settings 不留空殼。"""
        fields = {f.name for f in dataclasses.fields(config.Settings)}
        self.assertFalse({"llm_budget_currency", "llm_balance_floor"} & fields)
        self.assertFalse(hasattr(config, "_budget_currency"))


if __name__ == "__main__":
    unittest.main()
