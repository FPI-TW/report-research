# tests/test_report_doc_locale_persist.py
"""report_doc 持久化 locale/template_id，供換皮重出與 PDF 重建沿用（A 項）。

不存的話這兩個值只活在**原始請求**裡，重出時退回預設 → 英文研報變成
「英文內文 + 中文封面/頁首/免責 + 預設版型」。生產實測對照（2026-07-28）：

    沿用存下的 locale → 英文品牌 True／英文免責 True／中文洩漏 False
    不帶 locale       → 英文品牌 False／英文免責 False／**中文品牌與免責皆洩漏**

免責聲明是可轉寄 PDF 上最不該漂移的東西，而 M10c 才剛把它收斂到單一來源。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report as rpt  # noqa: E402


class _FakeSession:
    def __init__(self):
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        return None

    async def commit(self):
        return None


class PersistTests(unittest.IsolatedAsyncioTestCase):
    async def test_locale_and_template_written(self):
        s = _FakeSession()
        with patch.object(rpt, "SessionFactory", lambda: s):
            await rpt.persist_report_doc(
                "rid", None, None, "q", "T", "md", "/p", [], 1,
                locale="en", template_id="broker-modern",
            )
        sql, params = s.executed[-1]
        self.assertIn("locale", sql)
        self.assertIn("template_id", sql)
        self.assertEqual(params["locale"], "en")
        self.assertEqual(params["tpl"], "broker-modern")

    async def test_defaults_none_for_legacy_callers(self):
        """位置參數契約不變；未帶則寫 NULL（歷史列相容）。"""
        s = _FakeSession()
        with patch.object(rpt, "SessionFactory", lambda: s):
            await rpt.persist_report_doc("rid", None, None, "q", "T", "md", "/p", [], 1)
        params = s.executed[-1][1]
        self.assertIsNone(params["locale"])
        self.assertIsNone(params["tpl"])


class ThreadingTests(unittest.TestCase):
    """兩條生成路徑都必須把 locale/template_id 傳進 persist。"""

    def setUp(self):
        self.src = (REPO_ROOT / "app" / "services" / "report.py").read_text(
            encoding="utf-8"
        )

    def test_both_persist_call_sites_pass_them(self):
        # 逐節路徑與單次路徑各一
        self.assertEqual(
            self.src.count("locale=locale, template_id=template_id,"), 2
        )

    def test_fetch_returns_them(self):
        self.assertIn('"locale": row[6], "template_id": row[7]', self.src)


class RerenderAndRebuildTests(unittest.TestCase):
    """換皮與重建必須沿用存下來的值，而不是退回預設。"""

    def setUp(self):
        self.src = (REPO_ROOT / "web" / "routers" / "report.py").read_text(
            encoding="utf-8"
        )

    def test_rerender_uses_stored_locale(self):
        self.assertIn("doc_locale = doc.get(\"locale\")", self.src)
        self.assertIn("locale=doc_locale", self.src)

    def test_rerender_keeps_original_template_when_unspecified(self):
        """未指定 template_id ＝維持原模板，不是「換成預設模板」。"""
        self.assertIn('target_template = req.template_id or doc.get("template_id")', self.src)

    def test_rebuild_uses_stored_values(self):
        """docs/qa_pdf_report_deployment.md 以「PDF 可重建」為由主張 REPORTS_DIR
        不需備份——重建出不一樣的東西，那個主張就不成立。"""
        self.assertIn(
            'template_id=doc.get("template_id"), locale=doc.get("locale")', self.src
        )

    def test_no_bare_render_without_locale_in_router(self):
        """契約防護：router 內每個 render_report_pdf 呼叫都要帶 locale。"""
        import re

        # 只認 asyncio.to_thread(render_report_pdf, ...) 的實際呼叫。
        # 用 r"render_report_pdf,.*?\)" 會連 import 清單一起匹配（自造偽陽性）。
        calls = re.findall(
            r"to_thread\(\s*\n?\s*render_report_pdf,.*?\n        \)", self.src, re.DOTALL
        )
        self.assertTrue(calls, "未找到 render_report_pdf 呼叫")
        for c in calls:
            self.assertIn("locale=", c, f"缺 locale 的呼叫:\n{c[:200]}")


if __name__ == "__main__":
    unittest.main()
