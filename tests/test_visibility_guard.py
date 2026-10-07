"""研報可見性守門（不連 DB）：面向使用者的讀取 SQL 一律排除被管理員隱藏的研報。

被隱藏的研報要從檢索、問答選篇、閱讀頁、相似研報、觀點雷達、總覽、每日簡報與原檔 presign
全部消失，而過濾條件只有一份（`app/services/visibility.py` 的 `visible_report_sql`／
`visible_report_id_sql`）。漏帶的後果是**靜默的**：被隱藏的研報繼續出現在某一頁，沒有任何錯誤，
而行為測試大多用假 session 回放結果，看不到 SQL 有沒有帶條件。

靜態守門的規則（AST）：
- 掃描範圍是面向使用者讀取路徑的模組（`_SCANNED`）。
- 單位＝頂層函式（含類別方法）或頂層敘述（例如 `_DOC_SQL = text(...)`）。
- 單位裡的字串字面（不含 docstring）出現 `FROM|JOIN research.(research_report|report_chunk|
  report_signal|report_takeaway)`，就必須在同一個單位裡呼叫 `visible_report_sql` 或
  `visible_report_id_sql`；否則必須列在 `_EXEMPT` 並寫明理由。
- 豁免清單本身也受檢：列了卻已不存在、或已不再查語料表的項目會紅（免得清單只增不減）。

限制：判斷粒度是「單位」，同一個函式裡多條查詢只要有一條帶了片段就會過；多子查詢的 SQL
（雷達的 `_COVERAGE_SQL`、`_catalog_cte`）每個子查詢都要各自帶，靠 code review 與
`tests/test_report_visibility_db.py` 的行為測試補上。

真的 SQL 行為（隱藏後各路徑看不到、恢復後回來、重新入庫後旗標仍在）由
`tests/test_report_visibility_db.py` 驗。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app.services import store, visibility

REPO_ROOT = Path(__file__).resolve().parents[1]

_SCANNED = [
    REPO_ROOT / "app" / "services" / p
    for p in (
        "store.py", "retrieval.py", "retrieval_pipeline.py", "answer.py", "agentic_qa.py",
        "overview.py", "brief.py", "reading/queries.py", "radar/queries.py",
    )
] + [
    REPO_ROOT / "web" / "routers" / p
    for p in ("search.py", "reading.py", "radar.py", "brief.py", "report_file.py", "ask.py", "qa_history.py")
]

_CORPUS_SQL = re.compile(
    r"\b(FROM|JOIN)\s+research\.(research_report|report_chunk|report_signal|report_takeaway)\b", re.I
)
_MARKERS = {"visible_report_sql", "visible_report_id_sql"}

# (相對路徑, 單位名稱) → 為什麼不必帶可見性片段。
_EXEMPT = {
    ("app/services/store.py", "upsert_report"): "批次入庫（先刪後插）",
    ("app/services/store.py", "replace_report_extraction"): "批次回填抽取結果",
    ("app/services/store.py", "reanchor_takeaways"): "批次重錨摘錄（回填抽取後）",
    ("app/services/store.py", "report_exists"): "批次去重判斷：隱藏的研報仍在庫裡，不可重複入庫",
    ("app/services/store.py", "search_chunks"): "只給開發 CLI scripts/search.py 用，不在 web 路徑",
    ("app/services/store.py", "fetch_chunk_embeddings"): "依已過濾過的 chunk id 取向量，不選篇",
    ("app/services/reading/queries.py", "_TAKEAWAYS_SQL"): "只以 fetch_doc（已過濾）回傳的 report_id 查",
}


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _units(tree: ast.Module):
    """(名稱, 節點)：頂層函式、類別方法與其餘頂層敘述。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{sub.name}", sub
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            yield (names[0] if names else f"<line {node.lineno}>"), node
        else:
            yield f"<line {node.lineno}>", node


def _queries_corpus(unit: ast.AST, docstrings: set[int]) -> bool:
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        and _CORPUS_SQL.search(n.value)
        for n in ast.walk(unit)
    )


def _has_marker(unit: ast.AST) -> bool:
    for n in ast.walk(unit):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in _MARKERS:
                return True
    return False


def _scan():
    """回 (需要片段的單位, 缺片段的單位, 有查語料表的豁免單位)。"""
    guarded, missing, exempt_seen = [], [], set()
    for path in _SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        rel = _rel(path)
        for name, unit in _units(tree):
            if not _queries_corpus(unit, docstrings):
                continue
            if (rel, name) in _EXEMPT:
                exempt_seen.add((rel, name))
                continue
            guarded.append(f"{rel}:{name}")
            if not _has_marker(unit):
                missing.append(f"{rel}:{unit.lineno} {name}")
    return guarded, missing, exempt_seen


class VisibilityGuardTests(unittest.TestCase):
    def test_every_user_read_query_excludes_hidden_reports(self):
        guarded, missing, _ = _scan()
        # 掃描規則失效（例如 regex 寫壞）時會變成「什麼都沒抓到所以全過」。
        self.assertGreaterEqual(len(guarded), 18, f"抓到的查詢太少，掃描規則八成失效了：{guarded}")
        self.assertEqual(
            missing, [],
            "這些面向使用者的查詢沒有排除被隱藏的研報：加上 app/services/visibility.py 的 "
            f"visible_report_sql(別名)／visible_report_id_sql(欄位)，或在 _EXEMPT 寫明理由：{missing}",
        )

    def test_exemptions_are_not_stale(self):
        _, _, exempt_seen = _scan()
        self.assertEqual(sorted(set(_EXEMPT) - exempt_seen), [], "豁免清單有已不存在或已不查語料表的項目")

    def test_scanned_files_exist(self):
        for path in _SCANNED:
            self.assertTrue(path.is_file(), path)


class FragmentTests(unittest.TestCase):
    def test_fragment_shape(self):
        sql = visibility.visible_report_sql("r")
        self.assertTrue(sql.startswith("NOT EXISTS ("))
        self.assertIn("research.report_visibility", sql)
        self.assertIn("= r.file_hash", sql)
        by_id = visibility.visible_report_id_sql("report_signal.report_id")
        self.assertIn("rvis_r.id = report_signal.report_id", by_id)

    def test_fragments_exclude_hidden_and_unpublished(self):
        """隱藏或尚未發布（上傳草稿，revision 0008）都不可見；兩個片段講同一種語言。"""
        cond = "(rvis.hidden OR rvis.publication <> 'published')"
        self.assertIn(cond, visibility.visible_report_sql("r"))
        self.assertIn(cond, visibility.visible_report_id_sql("s.report_id"))
        self.assertEqual(visibility.PUBLICATIONS, ("draft", "published"))

    def test_fragment_rejects_non_identifiers(self):
        for bad in ("", "r; DROP TABLE x", "r.file_hash", "1r", "r)"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                visibility.visible_report_sql(bad)
        for bad in ("", "x; --", "a.b.c", "s.report_id)"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                visibility.visible_report_id_sql(bad)

    def test_lexical_filter_sits_inside_the_capped_cte(self):
        """字面路的片段要在 `LIMIT :cap` 之前：被隱藏研報的 chunk 不能佔走候選名額。"""
        for per_report in (False, True):
            with self.subTest(per_report=per_report):
                sql = store._lexical_sql(1, [], per_report)
                cap_at = sql.index("LIMIT :cap")
                self.assertIn("research.report_visibility", sql[:cap_at])


if __name__ == "__main__":
    unittest.main()
