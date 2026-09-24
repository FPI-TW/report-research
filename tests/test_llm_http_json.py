"""app/services/llm_http.py 的 `complete_json`：judge 用的非串流 JSON 模式（DeepSeek 遷移 PR-18）。

全程不連網：`httpx.MockTransport` 注入（`llm_http._transport`），金鑰只用 gitleaks allowlist 內的假值，
以 `mock.patch.dict` 限定在單一測試內（conftest 把 `DEEPSEEK_API_KEY` 強制成空字串）。

釘住的契約：
- 請求：`stream=false`、`response_format=json_object`、`temperature=0`、thinking 兩個開關都關、`user_id`、
  呼叫端給的 `max_tokens`，沒有 `stream_options`。
- 回應：`finish_reason` 必須是 stop 且 content 是 JSON 物件／陣列；記下 `model` 與 `system_fingerprint`。
- 重試最多 `JSON_MAX_ATTEMPTS`（2）個請求，只給截斷（2 倍 max_tokens）、空 content、暫時性錯誤；
  審查、帳號、400、不合法 JSON、逾時一律 1 個請求就停。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_http as lh  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
ENV = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 0,
         "prompt_cache_miss_tokens": 100}


def completion(content='{"ok": true}', finish="stop", *, usage=USAGE, fp="fp_test_1", model="deepseek-v4.1-flash",
               reasoning=None) -> dict:
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    obj = {"id": "x", "object": "chat.completion", "model": model,
           "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
    if fp is not None:
        obj["system_fingerprint"] = fp
    return obj


def ok(obj: dict, *, prefix: bytes = b"") -> httpx.Response:
    return httpx.Response(200, content=prefix + json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()
        self.sleeps: list[float] = []

    def tearDown(self):
        self._env.stop()
        lh._transport = None
        lh._reset_clients()

    def install(self, *responses):
        """依序回應；最後一個重複使用。元素可以是 Response、例外或 callable(request)。"""
        self.requests: list[httpx.Request] = []
        seq = list(responses)

        async def handler(request: httpx.Request):
            self.requests.append(request)
            item = seq[min(len(self.requests) - 1, len(seq) - 1)]
            if isinstance(item, BaseException):
                raise item
            if callable(item) and not isinstance(item, httpx.Response):
                return await item(request)
            return item

        lh._transport = httpx.MockTransport(handler)
        lh._reset_clients()

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)

    async def call(self, **kw) -> lh.JsonResult:
        async def fake_sleep(s):
            self.sleeps.append(s)

        kw.setdefault("max_tokens", 2048)
        kw.setdefault("timeout", 5.0)
        kw.setdefault("sleep", fake_sleep)
        return await lh.complete_json("deepseek-flash", "主張：…", system="只輸出 JSON", task="t", **kw)


class RequestShapeTests(_Base):
    async def test_body_is_non_streaming_json_mode_with_thinking_off(self):
        self.install(ok(completion()))
        res = await self.call(user_id="web-faithfulness", max_tokens=2048)
        self.assertIsNone(res.kind)
        body = self.body()
        self.assertIs(body["stream"], False)
        self.assertNotIn("stream_options", body)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["user_id"], "web-faithfulness")
        self.assertEqual(body["max_tokens"], 2048)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "只輸出 JSON"})
        self.assertEqual(self.requests[0].headers["accept"], "application/json")
        self.assertEqual(self.requests[0].headers["authorization"], f"Bearer {FAKE_KEY}")

    def test_streaming_body_is_unchanged_by_the_new_options(self):
        """新參數預設不送：串流路徑（問答、批次）的請求一個位元組都不變。"""
        body = lh.build_body("deepseek-flash", "p", max_tokens=16)
        self.assertNotIn("temperature", body)
        self.assertNotIn("response_format", body)


class SuccessTests(_Base):
    async def test_parses_content_and_records_model_and_fingerprint(self):
        self.install(ok(completion('{"verdicts": [{"idx": 0, "supported": true}]}')))
        res = await self.call()
        self.assertEqual(res.data, {"verdicts": [{"idx": 0, "supported": True}]})
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.outcome.model_resp, "deepseek-v4.1-flash")
        self.assertEqual(res.outcome.system_fingerprint, "fp_test_1")
        self.assertEqual(res.usage["prompt_tokens"], 100)
        self.assertIsNone(res.error)

    async def test_keepalive_blank_lines_before_body(self):
        """伺服器排隊時在本體前送空行（官方文件）。"""
        self.install(ok(completion('["a"]'), prefix=b"\n\n\n"))
        res = await self.call()
        self.assertEqual(res.data, ["a"])

    async def test_log_line_carries_fingerprint_not_key_or_prompt(self):
        self.install(ok(completion()))
        with self.assertLogs("app.services.llm_http", "INFO") as logs:
            await self.call()
        text = "\n".join(logs.output)
        self.assertIn("llm_call task=t", text)
        self.assertIn("fp=fp_test_1", text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn("主張：", text)


class RetryTests(_Base):
    async def test_length_retries_once_with_double_max_tokens(self):
        self.install(ok(completion('{"statements": ["截', "length")), ok(completion('{"statements": []}')))
        res = await self.call(max_tokens=1024)
        self.assertIsNone(res.kind)
        self.assertEqual(res.attempts, 2)
        self.assertEqual([self.body(i)["max_tokens"] for i in range(2)], [1024, 2048])
        self.assertEqual(res.max_tokens, 2048)
        self.assertEqual(res.usage["prompt_tokens"], 200, "兩次嘗試都計費，usage 要加總")

    async def test_length_twice_is_truncated_without_parsing_half_json(self):
        self.install(ok(completion('{"a": [', "length")))
        res = await self.call(max_tokens=1024)
        self.assertEqual(res.kind, lh.TRUNCATED)
        self.assertEqual(res.attempts, 2)
        self.assertIsNone(res.data)
        self.assertIn("max_tokens=2048", res.error)

    async def test_empty_content_retries_once(self):
        self.install(ok(completion("")), ok(completion('{"ok": 1}')))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts, res.data), (None, 2, {"ok": 1}))

    async def test_empty_twice_is_empty(self):
        self.install(ok(completion("  ", reasoning="想")))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts), (lh.EMPTY, 2))
        self.assertIn("reasoning", res.detail)

    async def test_transient_status_retries_after_retry_after(self):
        self.install(httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"message": "rate"}}),
                     ok(completion()))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts), (None, 2))
        self.assertEqual(self.sleeps, [3.0])

    async def test_retry_after_is_capped(self):
        self.install(httpx.Response(503, headers={"Retry-After": "600"}), ok(completion()))
        await self.call(timeout=60)
        self.assertEqual(self.sleeps, [lh._JSON_RETRY_AFTER_CAP])

    async def test_network_error_retries(self):
        self.install(httpx.ConnectError("refused"), ok(completion()))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts), (None, 2))

    async def test_error_object_in_200_body_is_transient(self):
        self.install(ok({"error": {"message": "server busy"}}), ok(completion()))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts), (None, 2))

    async def test_overloaded_twice_gives_up(self):
        self.install(httpx.Response(503))
        res = await self.call()
        self.assertEqual((res.kind, res.attempts), (lh.OVERLOADED, 2))

    async def test_no_wait_past_the_deadline(self):
        self.install(httpx.Response(429, headers={"Retry-After": "9"}), ok(completion()))
        res = await self.call(timeout=5)
        self.assertEqual((res.kind, res.attempts), (lh.OVERLOADED, 1))
        self.assertEqual(self.sleeps, [])

    async def test_max_attempts_one_disables_retry(self):
        """judge 的單層預算由呼叫端傳入（judge_schema.http_attempts_allowed）。"""
        self.install(ok(completion("{", "length")))
        res = await self.call(max_attempts=1)
        self.assertEqual((res.kind, res.attempts, len(self.requests)), (lh.TRUNCATED, 1, 1))

    async def test_never_more_than_json_max_attempts(self):
        self.install(httpx.Response(503))
        res = await self.call(max_attempts=99)
        self.assertEqual(len(self.requests), res.attempts)
        self.assertLessEqual(res.attempts, 99)
        # 預設值本身：
        self.install(httpx.Response(503))
        res = await self.call()
        self.assertEqual(len(self.requests), lh.JSON_MAX_ATTEMPTS)


class NoRetryTests(_Base):
    async def _once(self, response, kind):
        self.install(response)
        res = await self.call()
        self.assertEqual(res.kind, kind)
        self.assertEqual(len(self.requests), 1, "重打同一個 prompt 只是再付一次錢")
        self.assertIsNone(res.data)
        return res

    async def test_finish_content_filter(self):
        await self._once(ok(completion("", "content_filter")), lh.CONTENT_FILTER)

    async def test_content_exists_risk_400(self):
        await self._once(httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}), lh.CONTENT_FILTER)

    async def test_quota_402_marks_quota_seen(self):
        before = lh.last_quota_at()
        await self._once(httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}), lh.QUOTA)
        self.assertGreater(lh.last_quota_at(), before)

    async def test_auth_401(self):
        res = await self._once(httpx.Response(401, json={"error": {"message": "bad key sk-abcdef123"}}), lh.AUTH)
        self.assertNotIn("sk-abcdef123", res.error)

    async def test_bad_request_400(self):
        await self._once(httpx.Response(400, json={"error": {"message": "invalid"}}), lh.BAD_REQUEST)

    async def test_invalid_json_content(self):
        res = await self._once(ok(completion("這不是 JSON")), lh.INVALID_JSON)
        self.assertTrue(res.error.startswith("API[invalid_json]"))

    async def test_scalar_json_content(self):
        await self._once(ok(completion("42")), lh.INVALID_JSON)

    async def test_unknown_finish_reason(self):
        await self._once(ok(completion('{"a": 1}', "aborted")), lh.OTHER)

    async def test_body_not_json(self):
        await self._once(httpx.Response(200, content=b"<html>"), lh.OTHER)

    async def test_missing_key_sends_nothing(self):
        self.install(ok(completion()))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            res = await self.call()
        self.assertEqual(res.kind, lh.AUTH)
        self.assertEqual(self.requests, [])

    async def test_total_deadline_covers_a_silent_server(self):
        async def slow(_request):
            await asyncio.sleep(5)
            return ok(completion())

        self.install(slow)
        res = await self.call(timeout=0.05)
        self.assertEqual((res.kind, len(self.requests)), (lh.TIMEOUT, 1))


class TransportEdgeTests(_Base):
    """審查低1、低3：httpx 例外的歸類。任何例外都不能漏出 complete_json（契約：失敗都轉成結果）。"""

    async def test_read_timeout_is_timeout_and_not_retried(self):
        """非串流時伺服器整份生成完才回本體：read 逾時多半是「還在生成、會計費」，重送＝再付一次。"""
        self.install(httpx.ReadTimeout("silent"), ok(completion()))
        res = await self.call(timeout=60)
        self.assertEqual((res.kind, len(self.requests)), (lh.TIMEOUT, 1))
        self.assertIn("非串流", res.detail)

    async def test_connect_timeout_is_still_network_and_retried(self):
        """連線逾時沒送到伺服器、不計費：仍照暫時性錯誤重試。"""
        self.install(httpx.ConnectTimeout("syn"), ok(completion()))
        res = await self.call(timeout=60)
        self.assertEqual((res.kind, res.attempts), (None, 2))

    async def test_decoding_error_becomes_other_without_raising(self):
        """Content-Encoding 解不開（DecodingError 不是 TransportError）：先前會漏出 complete_json。"""
        # stream= 而不是 content=：後者在建構 Response 時就解碼，例外會在測試本身拋出
        broken = httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip"))
        self.install(broken)
        res = await self.call()
        self.assertEqual((res.kind, len(self.requests)), (lh.OTHER, 1))
        self.assertIn("DecodingError", res.detail)

    async def test_other_http_error_raised_by_transport_is_other(self):
        self.install(httpx.TooManyRedirects("loop"))
        res = await self.call()
        self.assertEqual((res.kind, len(self.requests)), (lh.OTHER, 1))


class TruncationRetryTimeTests(_Base):
    """審查低3：截斷重試前檢查剩餘期限，不夠就記 truncated、不送第二個請求。"""

    async def test_no_retry_when_remaining_time_is_short(self):
        async def slow_truncated(_request):
            await asyncio.sleep(0.3)
            return ok(completion('{"a": [', "length"))

        self.install(slow_truncated, ok(completion()))
        # 第一次耗時 ~0.3 秒，剩 ~0.2 秒 < 1.5×0.3：不重送
        res = await self.call(timeout=0.5, max_tokens=1024)
        self.assertEqual((res.kind, len(self.requests)), (lh.TRUNCATED, 1))
        self.assertEqual(res.max_tokens, 1024, "沒重送就不該報 2 倍上限")
        self.assertIn("剩餘期限不足", res.detail)

    async def test_retry_when_enough_time_remains(self):
        async def slowish_truncated(_request):
            await asyncio.sleep(0.05)
            return ok(completion('{"a": [', "length"))

        self.install(slowish_truncated, ok(completion()))
        res = await self.call(timeout=5, max_tokens=1024)
        self.assertEqual((res.kind, len(self.requests)), (None, 2))
        self.assertEqual(self.body(1)["max_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
