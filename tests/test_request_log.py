"""請求關聯 id（app/request_context.py）、logging filter 與 web/request_log.py 的 middleware。

釘住的行為：
- 每個回應都帶 X-Request-Id，連被 auth 擋下的 401 也有（middleware 必須在最外層）。
- 上游給的 id 只在形狀安全時沿用——它會被原樣寫進日誌行，任意字串等於讓外部偽造日誌。
- 日誌行帶 rid，且 create_task／to_thread 出去的工作沿用發起者的 id。
- /api/* 每個請求一行耗時；高頻輪詢路徑正常時不記、出錯時照記。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import request_context  # noqa: E402
from app.logging_setup import RequestIdFilter, logging_config  # noqa: E402
from web import request_log  # noqa: E402
from web.server import app  # noqa: E402


class InboundIdTests(unittest.TestCase):
    def test_safe_inbound_id_is_kept(self):
        self.assertEqual(request_context.accept_inbound("cf-8a1b2c3d4e5f.TPE"), "cf-8a1b2c3d4e5f.TPE")

    def test_unsafe_inbound_id_is_replaced(self):
        for bad in (None, "", "short", "a" * 65, "abc def ghi", "abcdefgh\nERROR forged line", "abcd/efgh"):
            got = request_context.accept_inbound(bad)
            self.assertNotEqual(got, bad)
            self.assertRegex(got, r"^[0-9a-f]{16}$")

    def test_default_outside_any_request(self):
        self.assertEqual(request_context.current_request_id(), request_context.NO_REQUEST)


class FilterAndFormatTests(unittest.TestCase):
    def test_handler_carries_the_filter_the_format_depends_on(self):
        """format 用了 %(request_id)s；handler 沒掛 filter 的話每一行都會在格式化時 KeyError。"""
        cfg = logging_config("INFO")
        self.assertIn("%(request_id)s", cfg["formatters"]["standard"]["format"])
        self.assertEqual(cfg["handlers"]["stderr"]["filters"], ["request_id"])
        self.assertIs(cfg["filters"]["request_id"]["()"], RequestIdFilter)

    def test_filter_stamps_current_id(self):
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", (), None)
        token = request_context.set_request_id("abc12345")
        try:
            self.assertTrue(RequestIdFilter().filter(record))
        finally:
            request_context.reset_request_id(token)
        self.assertEqual(record.request_id, "abc12345")


class PropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tasks_and_threads_inherit_the_callers_id(self):
        """忠實度抽查是 create_task、嵌入與 rerank 是 to_thread：它們的日誌要歸得回發起的請求。"""
        token = request_context.set_request_id("parent-req-1")
        try:
            async def _in_task():
                return request_context.current_request_id()

            from_task = await asyncio.create_task(_in_task())
            from_thread = await asyncio.to_thread(request_context.current_request_id)
        finally:
            request_context.reset_request_id(token)
        self.assertEqual((from_task, from_thread), ("parent-req-1", "parent-req-1"))
        self.assertEqual(request_context.current_request_id(), request_context.NO_REQUEST)


class MiddlewareHttpTests(unittest.TestCase):
    def test_every_response_has_an_id_even_when_auth_rejects(self):
        r = TestClient(app).get("/api/stats")
        self.assertEqual(r.status_code, 401)
        self.assertRegex(r.headers["x-request-id"], r"^[0-9a-f]{16}$")

    def test_safe_inbound_id_is_echoed_and_unsafe_is_not(self):
        c = TestClient(app)
        self.assertEqual(
            c.get("/login", headers={"X-Request-Id": "edge-0123456789"}).headers["x-request-id"],
            "edge-0123456789",
        )
        forged = c.get("/login", headers={"X-Request-Id": "has space in it"}).headers["x-request-id"]
        self.assertRegex(forged, r"^[0-9a-f]{16}$")

    def test_api_request_is_logged_with_status_and_elapsed(self):
        with self.assertLogs("web.request_log", level="INFO") as cm:
            TestClient(app).get("/api/stats?secret=do-not-log")
        line = "\n".join(cm.output)
        self.assertIn("http GET /api/stats status=401 elapsed_ms=", line)
        # query string 不進這一行（/api/search 的 q 是使用者查詢，由該 router 自己決定）。
        self.assertNotIn("do-not-log", line)

    def test_id_does_not_leak_past_the_request(self):
        TestClient(app).get("/login")
        self.assertEqual(request_context.current_request_id(), request_context.NO_REQUEST)


class QuietPathTests(unittest.TestCase):
    def test_what_gets_logged(self):
        f = request_log._should_log
        self.assertTrue(f("/api/search", 200, 5.0))
        self.assertFalse(f("/app/assets/index.js", 200, 5.0))
        self.assertFalse(f("/login", 200, 5.0))
        # 高頻輪詢：正常時安靜，變慢或出錯時要看得到。
        self.assertFalse(f("/api/progress", 200, 50.0))
        self.assertFalse(f("/healthz", 200, 5.0))
        self.assertTrue(f("/api/progress", 200, request_log.QUIET_SLOW_MS))
        self.assertTrue(f("/healthz", 503, 5.0))
        self.assertTrue(f("/login", 500, 5.0))


if __name__ == "__main__":
    unittest.main()
