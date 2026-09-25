# tests/test_healthz_llm.py
"""`/healthz/llm`：DeepSeek 帳號可用性（餘額、金鑰、連線），只回答本機直連。

設計理由在 `app/services/llm_health.py` 的模組 docstring。這裡守的是判斷邏輯的每一個分支：
幣別只看 CNY 那一筆且不靠索引、門檻邊界、402 閂鎖與解除條件、連續兩次才算連不上、快取時效、
審查 M15 的 `_unused`、回應不含任何金額。

全程不連網：`llm_http._transport` 換成 `httpx.MockTransport`；金鑰只用 gitleaks allowlist 內的假值，
以 `mock.patch.dict` 限定在單一測試內（conftest 把 `DEEPSEEK_API_KEY` 強制成空字串）。
"""
import asyncio
import os
import re
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import httpx

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.services import llm_health, llm_http  # noqa: E402
from web import server  # noqa: E402
from web.routers import health  # noqa: E402
from web.server import app  # noqa: E402

PROBE = Path(__file__).resolve().parents[1] / "scripts" / "check_web_health.sh"

FAKE_KEY = "fixed-test-secret-deepseek0"
BASE = "https://api.example.test"
ONLINE = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": BASE, "LLM_PROVIDER": "deepseek"}
# 線上任務全走 Claude（claude_cli 表），但有金鑰：批次在用 DeepSeek 的情境
UNUSED = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": BASE, "LLM_PROVIDER": "claude_cli"}


def _balance(*infos, available=True):
    return {"is_available": available, "balance_infos": [
        {"currency": c, "total_balance": t, "granted_balance": "0.00", "topped_up_balance": t} for c, t in infos
    ]}


# 9/24 實測的兩種順序（checks.json 的 balance_before／balance_after）
PROBE_BEFORE = _balance(("USD", "0.00"), ("CNY", "166.04"))
PROBE_AFTER = _balance(("CNY", "166.04"), ("USD", "0.00"))


def _local() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", client=("127.0.0.1", 51000))


class _Base(unittest.TestCase):
    def setUp(self):
        llm_health.reset()
        self._quota = llm_http._quota_seen_at
        llm_http._quota_seen_at = 0.0
        self.requests: list[httpx.Request] = []
        self.handler = lambda req: httpx.Response(200, json=PROBE_BEFORE)

        def recording(request):
            self.requests.append(request)
            return self.handler(request)

        llm_http._transport = httpx.MockTransport(recording)
        llm_http._reset_clients()

    def tearDown(self):
        llm_health.reset()
        llm_http._quota_seen_at = self._quota
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
        """它在 auth 白名單裡，所以『對外等於不存在』要靠 handler 自己守住；被拒的請求不查餘額。"""
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
        `/healthz/*` 端點都免認證對外開放，而只有這三支自己守了本機直連）。"""
        self.assertEqual(server._AUTH_ALLOWLIST, {"/login", "/healthz", "/healthz/storage", "/healthz/llm"})
        for path in ("/healthz/llm/", "/healthz/llmx", "/healthz/other", "/healthzz", "/healthz/storage/x"):
            with self.subTest(path=path):
                r = _local().get(path)
                self.assertEqual((r.status_code, r.headers.get("location")), (302, "/login"), path)

    def test_query_shape(self):
        self.get()
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual((req.method, str(req.url)), ("GET", f"{BASE}/user/balance"))
        self.assertEqual(req.headers["authorization"], f"Bearer {FAKE_KEY}")


class DisabledTests(_Base):
    def test_no_key_and_no_online_http_is_disabled_without_query(self):
        r = self.get({"LLM_PROVIDER": "claude_cli"})
        self.assertState(r, 200, "disabled")
        self.assertEqual(self.requests, [])

    def test_no_key_but_online_http_is_auth_failed(self):
        r = self.get({"LLM_PROVIDER": "deepseek"})
        self.assertState(r, 503, "auth_failed")
        self.assertEqual(self.requests, [])


class BalanceJudgementTests(_Base):
    def test_ok_in_both_observed_orders(self):
        """balance_infos 的順序不固定（9/24 實測會對調）：依 currency 取值，不靠索引。"""
        for body in (PROBE_BEFORE, PROBE_AFTER):
            with self.subTest(order=[i["currency"] for i in body["balance_infos"]]):
                llm_health.reset()
                self.reply(body=body)
                self.assertState(self.get(), 200, "ok")

    def test_low_below_floor_in_both_orders(self):
        for infos in ((("USD", "0.00"), ("CNY", "69.99")), (("CNY", "69.99"), ("USD", "0.00"))):
            with self.subTest(infos=infos):
                llm_health.reset()
                self.reply(body=_balance(*infos))
                self.assertState(self.get(), 503, "low")

    def test_floor_is_inclusive_ok(self):
        """門檻本身不算低（< 門檻才是 low）。"""
        self.reply(body=_balance(("CNY", "70.00"), ("USD", "0")))
        self.assertState(self.get(), 200, "ok")

    def test_exhausted_at_or_below_zero(self):
        for amount in ("0.00", "0", "-1.50"):
            with self.subTest(amount=amount):
                llm_health.reset()
                self.reply(body=_balance(("USD", "0.00"), ("CNY", amount)))
                self.assertState(self.get(), 503, "exhausted")

    def test_small_positive_is_low_not_exhausted(self):
        self.reply(body=_balance(("CNY", "0.01")))
        self.assertState(self.get(), 503, "low")

    def test_currency_code_is_trimmed_and_case_folded(self):
        """幣別代碼先去空白、轉大寫再比：小寫或帶空白的 CNY 仍是那一筆，不是「缺 CNY」。"""
        for cur in ("cny", " CNY ", "Cny"):
            with self.subTest(cur=cur):
                llm_health.reset()
                self.reply(body=_balance((cur, "166.04"), ("usd", "0.00")))
                self.assertState(self.get(), 200, "ok")
        llm_health.reset()
        self.reply(body=_balance(("cny", "10.00"), (" usd", "0")))
        self.assertState(self.get(), 503, "low")

    def test_json_numbers_are_accepted_as_amounts(self):
        """官方回字串，但 JSON 數字（int／float）同樣是金額；布林與 null 不是。"""
        for amount, want in ((166.04, (200, "ok")), (100, (200, "ok")), (30, (503, "low")), (0, (503, "exhausted")),
                             (True, (503, "indeterminate")), (None, (503, "indeterminate"))):
            with self.subTest(amount=amount):
                llm_health.reset()
                self.reply(body=_balance(("CNY", amount), ("USD", 0)))
                self.assertState(self.get(), *want)

    def test_unavailable_account_is_exhausted_even_with_money(self):
        self.reply(body=_balance(("CNY", "166.04"), ("USD", "0.00"), available=False))
        self.assertState(self.get(), 503, "exhausted")

    def test_indeterminate_cases(self):
        cases = {
            "缺 CNY": _balance(("USD", "0.00")),
            "空清單": _balance(),
            "非零 USD（幣別不符）": _balance(("CNY", "166.04"), ("USD", "5.00")),
            "非零 USD 在前": _balance(("USD", "5.00"), ("CNY", "166.04")),
            "負的 USD（欠款）": _balance(("CNY", "166.04"), ("USD", "-1.00")),
            "CNY 用罄但 USD 非零": _balance(("CNY", "0.00"), ("USD", "5.00")),
            "CNY 為負但 USD 非零": _balance(("USD", "5.00"), ("CNY", "-3.00")),
            "金額不是數字": _balance(("CNY", "abc"), ("USD", "0.00")),
            "金額是 NaN": _balance(("CNY", "NaN")),
            "USD 金額不是數字": _balance(("CNY", "166.04"), ("USD", "n/a")),
            "CNY 重複": _balance(("CNY", "166.04"), ("CNY", "0.00")),
            "is_available 缺": {"balance_infos": PROBE_BEFORE["balance_infos"]},
            "is_available 是字串": {**PROBE_BEFORE, "is_available": "true"},
            "balance_infos 缺": {"is_available": True},
            "項目不是物件": {"is_available": True, "balance_infos": ["CNY"]},
        }
        for name, body in cases.items():
            with self.subTest(name):
                llm_health.reset()
                self.reply(body=body)
                self.assertState(self.get(), 503, "indeterminate")

    def test_non_json_or_unexpected_status_is_indeterminate(self):
        for handler in (
            lambda req: httpx.Response(200, text="<html>maintenance</html>"),
            lambda req: httpx.Response(200, json=["not", "an", "object"]),
            lambda req: httpx.Response(404, json={"error": {"message": "Not Found"}}),
        ):
            llm_health.reset()
            self.handler = handler
            self.assertState(self.get(), 503, "indeterminate")

    def test_http_402_is_exhausted_and_401_is_auth_failed(self):
        self.reply(402, {"error": {"message": "Insufficient Balance"}})
        self.assertState(self.get(), 503, "exhausted")
        llm_health.reset()
        self.reply(401, {"error": {"message": "Authentication Fails"}})
        self.assertState(self.get(), 503, "auth_failed")

    def test_currency_and_floor_come_from_settings(self):
        """幣別與門檻是旋鈕（app/config.py）；換成 USD 時只看 USD 那一筆，非零 CNY 反而是幣別不符。"""
        s = config.get_settings()
        usd = replace(s, llm_budget_currency="USD", llm_balance_floor=10.0)
        with mock.patch.object(health, "get_settings", lambda: usd):
            self.reply(body=_balance(("CNY", "0.00"), ("USD", "12.00")))
            self.assertState(self.get(), 200, "ok")
            llm_health.reset()
            self.reply(body=_balance(("CNY", "0.00"), ("USD", "9.99")))
            self.assertState(self.get(), 503, "low")
            llm_health.reset()
            self.reply(body=_balance(("CNY", "5.00"), ("USD", "12.00")))
            self.assertState(self.get(), 503, "indeterminate")
        high = replace(s, llm_balance_floor=200.0)
        with mock.patch.object(health, "get_settings", lambda: high):
            llm_health.reset()
            self.reply(body=PROBE_BEFORE)
            self.assertState(self.get(), 503, "low")


class NoAmountLeakTests(_Base):
    def test_response_has_only_the_llm_key_and_no_amounts(self):
        for body, status in ((PROBE_BEFORE, 200), (_balance(("CNY", "12.34")), 503)):
            llm_health.reset()
            self.reply(body=body)
            r = self.get()
            self.assertEqual(r.status_code, status)
            self.assertEqual(set(r.json()), {"llm"})
            for token in ("166.04", "12.34", "CNY", "USD", "balance"):
                self.assertNotIn(token, r.text)

    def test_amount_goes_to_the_log_only(self):
        self.reply(body=_balance(("CNY", "12.34")))
        with self.assertLogs("app.services.llm_health", "WARNING") as cm:
            r = self.get()
        self.assertNotIn("12.34", r.text)
        self.assertTrue(any("low" in line and "12.34" in line for line in cm.output), cm.output)

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
        for status, body in ((200, _balance(("CNY", "1.00"))), (402, {}), (401, {}),
                             (200, _balance(("USD", "0.00")))):
            with self.subTest(status=status, body=body):
                llm_health.reset()
                self.reply(status, body)
                self.get()
                remaining = llm_health._snap.expires_at - time.monotonic()
                self.assertGreater(remaining, 50)
                self.assertLessEqual(remaining, 60)

    def test_refetches_after_expiry(self):
        self.get()
        self.reply(body=_balance(("CNY", "10.00")))
        self.expire()
        self.assertState(self.get(), 503, "low")
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

    def test_server_errors_count_as_unreachable_too(self):
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
        self.reply(body=PROBE_AFTER)
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
            return httpx.Response(200, json=PROBE_BEFORE)

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
    """本行程的真實請求收到 402 → 立刻 exhausted，直到一次之後開始的成功餘額查詢。"""

    def test_real_402_flips_cached_ok_immediately(self):
        c = _local()
        self.assertState(self.get(client=c), 200, "ok")
        llm_http._quota_seen_at = time.monotonic()
        self.assertState(self.get(client=c), 503, "exhausted")
        self.assertEqual(len(self.requests), 1, "閂鎖本身不需要等查詢")

    def test_latch_requeries_within_fail_ttl_and_clears_on_success(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        self.assertState(self.get(), 503, "exhausted")
        # ok 的快取還有將近 600 秒，但閂鎖期間以 60 秒計：撥過 60 秒就重查
        llm_health._snap = llm_health._snap._replace(checked_at=time.monotonic() - 61)
        self.assertState(self.get(), 200, "ok")
        self.assertEqual(len(self.requests), 2)
        self.assertState(self.get(), 200, "ok")

    def test_latch_not_requeried_before_fail_ttl(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        llm_health._snap = llm_health._snap._replace(checked_at=time.monotonic() - 30)
        self.assertState(self.get(), 503, "exhausted")
        self.assertEqual(len(self.requests), 1)

    def test_query_started_before_the_402_does_not_clear(self):
        """402 發生在查詢開始之後（這裡用未來時刻模擬）：那次查詢說 ok 也不算數。"""
        llm_http._quota_seen_at = time.monotonic() + 1000
        self.assertState(self.get(), 503, "exhausted")
        self.assertEqual(len(self.requests), 1, "閂鎖期間照樣查詢")

    def test_failed_query_does_not_clear(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()

        def boom(req):
            raise httpx.ConnectError("down", request=req)

        self.handler = boom
        llm_health._snap = llm_health._snap._replace(checked_at=0.0, expires_at=0.0)
        self.assertState(self.get(), 503, "exhausted")

    def test_exhausted_verdict_does_not_clear(self):
        self.get()
        llm_http._quota_seen_at = time.monotonic()
        self.reply(body=_balance(("CNY", "0.00")))
        self.expire()
        self.assertState(self.get(), 503, "exhausted")
        # 儲值後的查詢才解除
        self.reply(body=PROBE_BEFORE)
        self.expire()
        self.assertState(self.get(), 200, "ok")

    def test_exhausted_verdict_keeps_the_latch_armed(self):
        """exhausted 的查詢不算「解除」：之後一次非成功的查詢（404）仍回 exhausted，而不是 indeterminate。"""
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(body=_balance(("CNY", "0.00")))
        self.assertState(self.get(), 503, "exhausted")
        self.reply(404, {"error": {"message": "Not Found"}})
        self.expire()
        self.assertState(self.get(), 503, "exhausted")
        self.assertEqual(len(self.requests), 2)

    def test_non_success_query_does_not_clear(self):
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(404, {"error": {"message": "Not Found"}})
        self.assertState(self.get(), 503, "exhausted")

    def test_low_verdict_clears_the_latch_but_is_still_503(self):
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(body=_balance(("CNY", "30.00")))
        self.assertState(self.get(), 503, "low")

    def test_auth_failure_takes_precedence_over_the_latch(self):
        llm_http._quota_seen_at = time.monotonic() - 1
        self.reply(401, {})
        self.assertState(self.get(), 503, "auth_failed")

    def test_latch_applies_before_first_query_completes(self):
        async def slow(req):
            await asyncio.sleep(1.0)
            return httpx.Response(200, json=PROBE_BEFORE)

        llm_http._quota_seen_at = time.monotonic()
        self.handler = slow
        with mock.patch.object(llm_health, "WAIT", 0.05):
            self.assertState(self.get(), 503, "exhausted")


class UnusedTests(_Base):
    """審查 M15：線上任務沒有用到 DeepSeek 時，帳號問題回 200＋`_unused`，不開事件。"""

    def test_failing_states_are_200_with_unused_suffix(self):
        cases = [
            ((402, {}), "exhausted_unused"),
            ((200, _balance(("CNY", "10.00"))), "low_unused"),
            ((401, {}), "auth_failed_unused"),
            ((200, _balance(("USD", "0.00"))), "indeterminate_unused"),
        ]
        for (status, body), state in cases:
            with self.subTest(state=state):
                llm_health.reset()
                self.reply(status, body)
                self.assertState(self.get(UNUSED), 200, state)
        self.assertTrue(self.requests, "有金鑰就照常查詢（批次用得到）")

    def test_unreachable_unused(self):
        def boom(req):
            raise httpx.ConnectError("down", request=req)

        self.handler = boom
        self.get(UNUSED)
        self.expire()
        self.assertState(self.get(UNUSED), 200, "unreachable_unused")

    def test_healthy_states_have_no_suffix(self):
        self.assertState(self.get(UNUSED), 200, "ok")

    def test_latch_is_also_unused(self):
        llm_http._quota_seen_at = time.monotonic() + 1000
        self.assertState(self.get(UNUSED), 200, "exhausted_unused")

    def test_main_answer_on_deepseek_counts(self):
        """其他線上任務都在 Claude、只有主答走 DeepSeek：問答會停擺，要 503。"""
        env = {**UNUSED, "ASK_ANSWER_MODEL": "deepseek-flash"}
        self.reply(402, {})
        self.assertState(self.get(env), 503, "exhausted")

    def test_fail_open_online_tasks_do_not_count(self):
        """審查低2：路由、改寫、規劃、追問、忠實度都 fail-open，它們走 DeepSeek 而帳號壞掉時問答照樣答得
        出來——不能因此開「問答停擺」的事件。逐一只讓一個走 DeepSeek，也試全部一起。"""
        knobs = ("ASK_INTENT_MODEL", "ASK_CONDENSE_MODEL", "QA_PLANNER_MODEL", "ASK_FOLLOWUP_MODEL",
                 "FAITHFULNESS_MODEL")
        for chosen in [(k,) for k in knobs] + [knobs]:
            with self.subTest(knobs=chosen):
                llm_health.reset()
                self.reply(402, {})
                env = {**UNUSED, **{k: "deepseek-flash" for k in chosen}}
                self.assertState(self.get(env), 200, "exhausted_unused")

    def test_deepseek_provider_with_main_answer_pinned_to_claude_is_unused(self):
        """`LLM_PROVIDER=deepseek` 把其餘線上任務都帶到 DeepSeek，但主答釘在 Claude：仍不算用到。"""
        env = {**ONLINE, "ASK_ANSWER_MODEL": "claude-sonnet-5"}
        self.reply(401, {})
        self.assertState(self.get(env), 200, "auth_failed_unused")

    def test_missing_key_matters_only_for_the_main_answer(self):
        """沒有金鑰：主答走 DeepSeek 是 auth_failed（503），只有 fail-open 任務走 DeepSeek 是 disabled（200）。"""
        side = {"LLM_PROVIDER": "claude_cli", "ASK_INTENT_MODEL": "deepseek-flash",
                "FAITHFULNESS_MODEL": "deepseek-flash"}
        self.assertState(self.get(side), 200, "disabled")
        self.assertState(self.get({"LLM_PROVIDER": "claude_cli", "ASK_ANSWER_MODEL": "deepseek-flash"}),
                         503, "auth_failed")
        self.assertEqual(self.requests, [])

    def test_unused_state_is_logged(self):
        self.reply(402, {})
        with self.assertLogs("app.services.llm_health", "WARNING") as cm:
            self.get(UNUSED)
        self.assertTrue(any("exhausted_unused" in line for line in cm.output), cm.output)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """真正的並行（同一個 event loop 上同時進行的請求與查詢），不是撥時鐘模擬。"""

    def setUp(self):
        llm_health.reset()
        self._quota = llm_http._quota_seen_at
        llm_http._quota_seen_at = 0.0
        self.requests: list[httpx.Request] = []
        self.delay = 0.3
        self.status, self.body = 200, PROBE_BEFORE

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
        llm_http._quota_seen_at = self._quota
        llm_http._transport = None
        llm_http._reset_clients()

    @staticmethod
    def _report():
        return llm_health.report(currency="CNY", floor=70.0)

    async def test_402_arriving_while_a_query_is_in_flight_keeps_the_latch(self):
        """查詢開始之後、結束之前收到 402：那次查詢回 ok 也不得解除閂鎖（以查詢「開始」為界）。"""
        async def hit_402():
            await asyncio.sleep(0.1)
            llm_http._quota_seen_at = time.monotonic()

        (state, status), _ = await asyncio.gather(self._report(), hit_402())
        self.assertEqual((state, status), ("exhausted", 503))
        self.assertLess(llm_health._snap.cleared_at, llm_http.last_quota_at())
        self.assertEqual(await self._report(), ("exhausted", 503), "快取期間閂鎖仍在")
        self.assertEqual(len(self.requests), 1)

    async def test_concurrent_requests_share_one_query(self):
        results = await asyncio.gather(self._report(), self._report(), self._report())
        self.assertEqual(results, [("ok", 200)] * 3)
        self.assertEqual(len(self.requests), 1, "同時進來的請求要共用同一個查詢 task")

    async def test_slow_query_is_not_cancelled_by_the_wait_limit(self):
        """等不到（WAIT）就先回上一次的結論，但查詢不能被取消（shield）：它在背景跑完後要更新狀態。"""
        self.status, self.body = 402, {"error": {"message": "Insufficient Balance"}}
        with mock.patch.object(llm_health, "WAIT", 0.05):
            self.assertEqual(await self._report(), ("unknown", 200))
            await asyncio.sleep(self.delay + 0.2)
            self.assertEqual(llm_health._snap.state, "exhausted", "背景查詢沒有完成（被 wait_for 取消了？）")
            self.assertEqual(await self._report(), ("exhausted", 503))
        self.assertEqual(len(self.requests), 1)


class DeadlineOrderTests(unittest.TestCase):
    def test_fetch_timeout_below_wait_below_probe_curl_timeout(self):
        """FETCH_TIMEOUT < WAIT < 探針的 HEALTH_TIMEOUT：端點一定在探針的 curl 放棄前回答（否則 curl 逾時
        被當成「判不出來」、停擺被靜默），而查詢逾時在同一個請求裡就有結論。"""
        m = re.search(r'^HEALTH_TIMEOUT="\$\{HEALTH_TIMEOUT:-(\d+)\}"', PROBE.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m)
        self.assertLess(llm_health.FETCH_TIMEOUT, llm_health.WAIT)
        self.assertLess(llm_health.WAIT, int(m.group(1)))


class QuotaHookTests(unittest.IsolatedAsyncioTestCase):
    """`llm_http` 在線上與批次兩條路徑收到 402 都要記下時刻；其他錯誤不記。"""

    def setUp(self):
        self._quota = llm_http._quota_seen_at
        llm_http._quota_seen_at = 0.0

    def tearDown(self):
        llm_http._quota_seen_at = self._quota
        llm_http._transport = None
        llm_http._reset_clients()

    def _install(self, status):
        llm_http._transport = httpx.MockTransport(
            lambda req: httpx.Response(status, json={"error": {"message": "x"}})
        )
        llm_http._reset_clients()

    async def _stream_once(self):
        with mock.patch.dict(os.environ, ONLINE):
            async for item in llm_http.astream_chat("deepseek-flash", "hi", max_tokens=8, first_token_timeout=5):
                last = item
        return last

    async def test_online_402_is_recorded(self):
        self._install(402)
        before = time.monotonic()
        out = await self._stream_once()
        self.assertEqual(out.kind, llm_http.QUOTA)
        self.assertGreaterEqual(llm_http.last_quota_at(), before)

    async def test_online_other_errors_are_not(self):
        for status in (401, 400, 500):
            self._install(status)
            await self._stream_once()
        self.assertEqual(llm_http.last_quota_at(), 0.0)

    async def test_batch_402_is_recorded(self):
        self._install(402)
        with mock.patch.dict(os.environ, ONLINE):
            res = await asyncio.to_thread(
                llm_http.complete_chat, "deepseek-flash", "hi", max_tokens=8, timeout=5, sleep=lambda s: None
            )
        self.assertEqual(res.kind, llm_http.QUOTA)
        self.assertGreater(llm_http.last_quota_at(), 0.0)

    async def test_balance_query_402_does_not_arm_the_latch(self):
        """餘額查詢自己的 402 直接判 exhausted，不經閂鎖（否則之後的成功查詢解除時序會錯）。"""
        self._install(402)
        with mock.patch.dict(os.environ, ONLINE):
            res = await llm_http.fetch_balance()
        self.assertEqual(res.kind, llm_http.QUOTA)
        self.assertEqual(llm_http.last_quota_at(), 0.0)


class FetchBalanceTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        llm_http._transport = None
        llm_http._reset_clients()

    async def _fetch(self, handler, env=ONLINE, timeout=4.0):
        llm_http._transport = httpx.MockTransport(handler)
        llm_http._reset_clients()
        with mock.patch.dict(os.environ, env):
            return await llm_http.fetch_balance(timeout)

    async def test_ok_returns_body_untouched(self):
        res = await self._fetch(lambda req: httpx.Response(200, json=PROBE_BEFORE))
        self.assertEqual((res.kind, res.status, res.body), (None, 200, PROBE_BEFORE))

    async def test_error_kinds(self):
        def connect(req):
            raise httpx.ConnectError("refused", request=req)

        def read_timeout(req):
            raise httpx.ReadTimeout("slow", request=req)

        cases = [
            (lambda req: httpx.Response(401, json={}), llm_http.AUTH),
            (lambda req: httpx.Response(402, json={}), llm_http.QUOTA),
            (lambda req: httpx.Response(500, json={}), llm_http.OVERLOADED),
            (lambda req: httpx.Response(404, json={}), llm_http.CONFIG),
            (lambda req: httpx.Response(200, text="nope"), llm_http.OTHER),
            (connect, llm_http.NETWORK),
            (read_timeout, llm_http.TIMEOUT),
        ]
        for handler, kind in cases:
            with self.subTest(kind=kind):
                res = await self._fetch(handler)
                self.assertEqual(res.kind, kind)

    async def test_overall_timeout(self):
        async def slow(req):
            await asyncio.sleep(5)
            return httpx.Response(200, json=PROBE_BEFORE)

        t0 = time.monotonic()
        res = await self._fetch(slow, timeout=0.2)
        self.assertEqual(res.kind, llm_http.TIMEOUT)
        self.assertLess(time.monotonic() - t0, 2)

    async def test_missing_key_is_auth_without_request(self):
        seen = []
        res = await self._fetch(lambda req: seen.append(req) or httpx.Response(200, json={}),
                                env={"DEEPSEEK_API_KEY": ""})
        self.assertEqual(res.kind, llm_http.AUTH)
        self.assertEqual(seen, [])


class KnobTests(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {"LLM_BUDGET_CURRENCY": "", "LLM_BALANCE_FLOOR": ""}):
            s = config._load()
        self.assertEqual((s.llm_budget_currency, s.llm_balance_floor), ("CNY", 70.0))

    def test_currency_is_normalized_and_validated(self):
        for raw, want in ((" usd ", "USD"), ("CNY", "CNY"), ("RMB1", "CNY"), ("人民幣", "CNY"), ("US", "CNY")):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"LLM_BUDGET_CURRENCY": raw}):
                self.assertEqual(config._budget_currency(), want)

    def test_floor_rejects_garbage(self):
        for raw, want in (("100", 100.0), ("abc", 70.0), ("0", 70.0), ("-5", 70.0), ("nan", 70.0)):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"LLM_BALANCE_FLOOR": raw}):
                self.assertEqual(config._load().llm_balance_floor, want)

    def test_conftest_neutralizes_budget_knobs(self):
        """部署目錄 .env 的門檻不得滲進測試（否則上面的 ok／low 邊界會依機器而變）。"""
        self.assertEqual(os.environ.get("LLM_BUDGET_CURRENCY"), "")
        self.assertEqual(os.environ.get("LLM_BALANCE_FLOOR"), "")


if __name__ == "__main__":
    unittest.main()
