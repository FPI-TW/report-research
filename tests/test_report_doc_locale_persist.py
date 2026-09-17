# tests/test_report_doc_locale_persist.py
"""report_doc 持久化 locale/template_id，供換皮重出與 PDF 重建沿用（A 項）。

不存的話這兩個值只活在**原始請求**裡，重出時退回預設 → 英文研報變成
「英文內文 + 中文封面/頁首/免責 + 預設版型」。生產實測對照（2026-07-28）：

    沿用存下的 locale → 英文品牌 True／英文免責 True／中文洩漏 False
    不帶 locale       → 英文品牌 False／英文免責 False／**中文品牌與免責皆洩漏**

免責聲明是可轉寄 PDF 上最不該漂移的東西，而 M10c 才剛把它收斂到單一來源。
"""
import ast
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
                locale="en", template_id="broker-modern", renderer="weasyprint",
            )
        sql, params = s.executed[-1]
        self.assertIn("locale", sql)
        self.assertIn("template_id", sql)
        self.assertIn("renderer", sql)
        self.assertEqual(params["locale"], "en")
        self.assertEqual(params["tpl"], "broker-modern")
        self.assertEqual(params["renderer"], "weasyprint")

    async def test_defaults_none_for_legacy_callers(self):
        """位置參數契約不變；未帶則寫 NULL（歷史列相容）。"""
        s = _FakeSession()
        with patch.object(rpt, "SessionFactory", lambda: s):
            await rpt.persist_report_doc("rid", None, None, "q", "T", "md", "/p", [], 1)
        params = s.executed[-1][1]
        self.assertIsNone(params["locale"])
        self.assertIsNone(params["tpl"])


def _function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    return next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _doc_get(node: ast.AST, field: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "doc"
        and node.func.attr == "get"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == field
    )


def _render_calls(function: ast.AsyncFunctionDef) -> list[ast.Call]:
    return [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to_thread"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "render_report_pdf"
    ]


class ThreadingTests(unittest.IsolatedAsyncioTestCase):
    """兩條生成路徑都必須把 locale/template_id 傳進 persist。"""

    def setUp(self):
        self.tree = ast.parse((REPO_ROOT / "app" / "services" / "report.py").read_text(encoding="utf-8"))

    def test_both_persist_call_sites_pass_them(self):
        calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "persist_report_doc"
        ]
        self.assertEqual(len(calls), 2)
        for call in calls:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            self.assertEqual(getattr(keywords.get("locale"), "id", None), "locale")
            self.assertEqual(getattr(keywords.get("template_id"), "id", None), "template_id")
            self.assertIsInstance(keywords.get("renderer"), ast.Attribute)
            self.assertEqual(getattr(keywords["renderer"].value, "id", None), "render_result")
            self.assertEqual(keywords["renderer"].attr, "renderer")

    async def test_fetch_returns_persisted_locale_and_template(self):
        class _Result:
            def first(self):
                return ("rid", "T", "md", "/p", "q", None, "en", "broker-modern", "generated/r/base.pdf")

        class _FetchSession(_FakeSession):
            async def execute(self, stmt, params=None):
                self.executed.append((str(stmt), params or {}))
                return _Result()

        session = _FetchSession()
        with patch.object(rpt, "SessionFactory", lambda: session):
            doc = await rpt.fetch_report_doc("rid")
        self.assertEqual(doc["locale"], "en")
        self.assertEqual(doc["template_id"], "broker-modern")


class RerenderAndRebuildTests(unittest.TestCase):
    """AST contract: rebuild selection is explicit, not a formatting-sensitive source string."""

    def setUp(self):
        self.tree = ast.parse((REPO_ROOT / "web" / "routers" / "report.py").read_text(encoding="utf-8"))

    def test_rerender_uses_stored_locale_and_template(self):
        function = _function(self.tree, "report_doc_rerender")
        assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)]
        locale_assignment = next(
            node for node in assignments if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "doc_locale"
        )
        template_assignment = next(
            node for node in assignments
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "target_template"
        )
        self.assertTrue(_doc_get(locale_assignment.value, "locale"))
        self.assertIsInstance(template_assignment.value, ast.BoolOp)
        self.assertTrue(any(_doc_get(value, "template_id") for value in template_assignment.value.values))
        render = _render_calls(function)[0]
        keywords = {keyword.arg: keyword.value for keyword in render.keywords}
        self.assertEqual(getattr(keywords["locale"], "id", None), "doc_locale")
        self.assertEqual(getattr(keywords["template_id"], "id", None), "target_template")

    def test_base_and_current_rendition_rebuild_selection(self):
        """Base uses report_doc template; selected rendition overrides only that template."""
        function = _function(self.tree, "report_doc_pdf")
        assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)]
        selection = next(
            node for node in assignments
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "template_id"
        )
        self.assertIsInstance(selection.value, ast.IfExp)
        self.assertIsInstance(selection.value.test, ast.Name)
        self.assertEqual(selection.value.test.id, "rendition")
        self.assertFalse(_doc_get(selection.value.body, "template_id"))
        self.assertTrue(_doc_get(selection.value.orelse, "template_id"))
        self.assertIsInstance(selection.value.body, ast.Call)
        self.assertIsInstance(selection.value.body.func, ast.Attribute)
        self.assertEqual(selection.value.body.func.attr, "get")
        self.assertEqual(getattr(selection.value.body.func.value, "id", None), "rendition")
        self.assertEqual(getattr(selection.value.body.args[0], "value", None), "template_id")
        render = _render_calls(function)[0]
        keywords = {keyword.arg: keyword.value for keyword in render.keywords}
        self.assertEqual(getattr(keywords["template_id"], "id", None), "template_id")
        self.assertTrue(_doc_get(keywords["locale"], "locale"))

    def test_every_router_render_passes_locale(self):
        calls = _render_calls(_function(self.tree, "report_doc_rerender"))
        calls += _render_calls(_function(self.tree, "report_doc_pdf"))
        self.assertTrue(calls)
        self.assertTrue(all(any(keyword.arg == "locale" for keyword in call.keywords) for call in calls))


if __name__ == "__main__":
    unittest.main()
