"""/api/ask 在 LLM 不可用時依 `LLMUnavailableError.kind` 給使用者不同的 detail（走 HTTP 層）。

內容審查要讓使用者知道「換個問法就好」；帳號層級（餘額、金鑰）與設定錯誤（模型名）要說「暫時
無法使用」，免得反覆重試，且不承諾「已通知管理者」（沒有告警接線）；設定錯誤不說「帳號異常」
（誤設 ASK_WEB_MODEL 也會觸發）。其餘（含 CLI 路徑，kind 未填）維持原本的「問答服務發生錯誤」。
"""

import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# web.auth 匯入時即讀取共用帳密（fail-closed），須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

import web.server as server  # noqa: E402
from app.services.llm import LLMUnavailableError  # noqa: E402
from web import deps  # noqa: E402


def _authed_client():
    c = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return c


def _error_events(body: str) -> list[dict]:
    out = []
    for frame in body.split("\n\n"):
        lines = frame.strip().splitlines()
        if lines and lines[0] == "event: error":
            out.append(json.loads(lines[1][len("data: "):]))
    return out


class AskLlmErrorDetailTests(unittest.TestCase):
    def _ask_with(self, exc: Exception) -> list[dict]:
        async def failing(question, **kwargs):
            yield ("sources", [])
            raise exc

        client = _authed_client()
        orig = deps.answer_question
        deps.answer_question = failing
        try:
            resp = client.post("/api/ask", json={"question": "台積電展望"})
        finally:
            deps.answer_question = orig
        self.assertEqual(resp.status_code, 200)
        return _error_events(resp.text)

    def test_content_filter_suggests_rephrasing(self):
        events = self._ask_with(LLMUnavailableError("HTTP 400 Content Exists Risk", kind="content_filter"))
        self.assertEqual(events, [{"detail": "此題觸發模型供應商的內容審查，可換個問法"}])

    def test_account_kinds_say_unavailable_without_promising_notification(self):
        for kind in ("quota", "auth"):
            with self.subTest(kind=kind):
                events = self._ask_with(LLMUnavailableError("x", kind=kind))
                self.assertEqual(
                    events, [{"detail": "問答服務暫時無法使用（模型服務帳號異常），請稍後再試或聯絡管理者"}]
                )

    def test_config_is_not_called_an_account_problem(self):
        events = self._ask_with(LLMUnavailableError("x", kind="config"))
        self.assertEqual(events, [{"detail": "問答服務暫時無法使用（模型設定有誤），請聯絡管理者"}])

    def test_no_detail_promises_notification(self):
        from web.routers import ask as ask_router

        for kind, detail in ask_router._LLM_ERROR_DETAILS.items():
            with self.subTest(kind=kind):
                self.assertNotIn("已通知", detail)

    def test_other_kinds_keep_generic_message(self):
        for exc in (
            LLMUnavailableError("529 Overloaded"),            # CLI：kind 未填
            LLMUnavailableError("x", kind="overloaded"),
            LLMUnavailableError("x", kind="timeout"),
            RuntimeError("程式錯誤"),
        ):
            with self.subTest(exc=repr(exc)):
                self.assertEqual(self._ask_with(exc), [{"detail": "問答服務發生錯誤"}])


if __name__ == "__main__":
    unittest.main()
