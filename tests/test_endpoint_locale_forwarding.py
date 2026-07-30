# tests/test_endpoint_locale_forwarding.py
"""`/api/ask` 與 `/api/report` 的 locale 轉發（F1），走 **HTTP 層**。

兩個端點都有 `locale` 欄位並轉發給服務層，但先前**零測試**。這條縫特別危險，
因為 locale 是 fail-open 的：轉發斷掉時不會有例外、不會 500，只會「英文輸出
無聲退回中文」——沒有任何訊號。

本專案已兩度在同一種縫上出事：
- M3：Pydantic 靜默丟棄未宣告欄位，而雙側測試各自繞過端點
- 2026-07-28：裝飾器套到輔助函式上，而測試直接呼叫 handler 物件

所以這裡一律經 TestClient 發真實請求，不呼叫 handler 函式物件。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from web import deps  # noqa: E402
from web.routers import report as report_routes  # noqa: E402
from web.server import app  # noqa: E402


def _authed() -> TestClient:
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


def _drain(client, path, body):
    """打 SSE 端點並讀完，回傳原始文字（我們只在意服務層收到什麼）。"""
    with client.stream("POST", path, json=body) as r:
        return r.status_code, "".join(r.iter_text())


class AskLocaleForwardingTests(unittest.TestCase):
    def _capture(self, body):
        seen = {}

        async def fake_answer(question, **kwargs):
            seen.update(kwargs)
            seen["question"] = question
            yield ("done", {"cited": [], "qa_id": None, "conversation_id": "c1"})

        with patch.object(deps, "answer_question", fake_answer):
            code, _ = _drain(_authed(), "/api/ask", body)
        return code, seen

    def test_locale_en_reaches_service(self):
        code, seen = self._capture({"question": "TSMC outlook", "locale": "en"})
        self.assertEqual(code, 200)
        self.assertEqual(seen.get("locale"), "en")

    def test_locale_absent_forwards_none(self):
        """未帶時轉發 None，由服務層 resolve_locale fail-open 到 zh-Hant。

        端點不該自己塞預設值——那會讓「預設」在兩處各有一份定義。
        """
        code, seen = self._capture({"question": "台積電"})
        self.assertEqual(code, 200)
        self.assertIn("locale", seen)
        self.assertIsNone(seen["locale"])

    def test_unknown_locale_still_forwarded_verbatim(self):
        """未知值原樣轉發，由服務層統一 fail-open;端點不做語言判斷。"""
        _, seen = self._capture({"question": "q", "locale": "fr"})
        self.assertEqual(seen.get("locale"), "fr")


class ReportLocaleForwardingTests(unittest.TestCase):
    def _capture(self, body):
        seen = {}

        async def fake_generate(question, **kwargs):
            seen.update(kwargs)
            yield ("done", {"report_id": "r1", "title": "T",
                            "download_url": "/api/report-doc/r1/pdf", "thinking_ms": 1})

        with patch.object(report_routes, "generate_report", fake_generate):
            code, _ = _drain(_authed(), "/api/report", body)
        return code, seen

    def test_locale_and_template_both_reach_service(self):
        code, seen = self._capture(
            {"question": "TSMC", "locale": "en", "template_id": "broker-modern"}
        )
        self.assertEqual(code, 200)
        self.assertEqual(seen.get("locale"), "en")
        self.assertEqual(seen.get("template_id"), "broker-modern")

    def test_absent_fields_forward_none(self):
        code, seen = self._capture({"question": "台積電"})
        self.assertEqual(code, 200)
        self.assertIsNone(seen.get("locale"))
        self.assertIsNone(seen.get("template_id"))


class RequestModelContractTests(unittest.TestCase):
    """Pydantic 會**靜默丟棄**未宣告的欄位——欄位不在模型上，請求照樣 200。

    M3 就是這樣讓一個欄位一路無聲失效到生產。
    """

    def test_ask_request_declares_locale(self):
        from web.routers.ask import AskRequest

        self.assertIn("locale", AskRequest.model_fields)

    def test_report_request_declares_locale_and_template(self):
        from web.routers.report import ReportRequest

        self.assertIn("locale", ReportRequest.model_fields)
        self.assertIn("template_id", ReportRequest.model_fields)


if __name__ == "__main__":
    unittest.main()
