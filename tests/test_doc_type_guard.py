"""文件類型守門（不連 DB）：讀取一律經 `app/services/doc_types.py` 的片段，不再直接寫 `is_research` 過濾。

`is_research`（revision 0012 之前唯一的類型旗標）v1 仍保留在表上，但它只是舊版程式與備份還原的相容欄位：
寫入由 `doc_type` 推得、讀取一律走 `doc_type_enabled_sql`／`doc_type_enabled_id_sql`。漏掉的後果是**靜默的**——
廷豐 profile 下兩種寫法今天結果相同，要等第二種文件類型上線才會有一頁多出或少掉文件。

四組守門：

1. **SQL 裡不得出現 `is_research` 過濾**（`app/`、`web/`、`scripts/`、`eval/` 的字串字面，不含 docstring）：
   `is_research IS …`、`is_research = …`、`WHERE r.is_research` 這類寫法一律紅，例外列在 `_FILTER_EXEMPT`。
2. **Python 端不得讀 `.is_research` 屬性做判斷**：例外是標註器本身、tag JSON 的序列化與管理面的顯示欄位
   （`_ATTR_EXEMPT`）。判斷改用 `doc_types.ingest_gate`／`is_enabled`。
3. **面向使用者的語料查詢都帶類型片段**：掃描範圍與單位規則沿用 `tests/test_visibility_guard.py`
   （同一份 `_SCANNED`），每個查 `research.research_report` 等語料表的單位都要呼叫 doc_type 片段，
   例外列在 `_MARKER_EXEMPT`。
4. 豁免清單本身也受檢：列了卻已不存在、或已不再命中的項目會紅。

第 1 組取代了舊的「`is_research` 只准寫 `IS NOT FALSE`」守門（原在 `tests/test_schema_constraints.py`）：
過濾改走片段之後，任何 is_research 過濾寫法都不該再出現。

真的 SQL 行為（類型關掉的文件從各路徑消失、回填與兩欄同步約束）由 `tests/test_doc_type_db.py` 驗。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 掃描範圍、單位切分與 visibility 的豁免沿用同一份（tests/ 不是套件，依路徑載入）。
_spec = importlib.util.spec_from_file_location("_visibility_guard", REPO_ROOT / "tests" / "test_visibility_guard.py")
assert _spec is not None and _spec.loader is not None
vg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vg)

_ROOTS = ("app", "web", "scripts", "eval")

# SQL 裡的 is_research 過濾寫法（大小寫不分）。單純的欄位名（INSERT 欄位表、SELECT 欄位、bind 參數 :is_research）不算。
_FILTER_RE = re.compile(
    r"\bis_research\b\s*(?:IS\b|=|<>|!=)|\b(?:WHERE|AND|OR|NOT|ON)\s+(?:\w+\.)?is_research\b",
    re.I,
)

# (相對路徑, 單位名稱) → 為什麼可以寫 is_research 過濾。
_FILTER_EXEMPT = {
    ("scripts/db_audit.py", "CHECKS"): "is_research_null：稽核舊欄位本身（baseline 收斂成 NOT NULL 之前的庫）",
    ("app/services/tagging.py", "TAG_INSTRUCTION"): "標註 prompt 的輸出格式說明（不是 SQL；test_tagging 逐字釘住）",
}

# (相對路徑, 單位名稱) → 為什麼可以讀 .is_research。
_ATTR_EXEMPT = {
    ("app/services/tagging.py", "MarketTag.doc_type"): "標註器的判斷映射到文件類型（唯一的映射點）",
    ("scripts/_ingest_core.py", "_persist_tag"): "tag JSON 的格式不變（is_research 是標註器的原始輸出）",
    ("scripts/tag_all_cli.py", "tag_one"): "tag JSON 的格式不變",
    ("scripts/profile_corpus.py", "_db_meta"): "診斷 CLI 原樣列出舊欄位（不過濾）",
    ("web/routers/admin_uploads.py", "_preview"): "管理面預覽顯示舊欄位（產生的 client 契約，前端「研究報告」列）",
}

_MARKERS = {"doc_type_enabled_sql", "doc_type_enabled_id_sql"}

# (相對路徑, 單位名稱) → 為什麼不必帶類型片段。visibility 守門的豁免（批次入庫、開發 CLI、依已過濾的 id 再查）
# 理由同樣成立，直接沿用；這裡只列多出來的。
_MARKER_EXEMPT = dict(vg._EXEMPT)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _py_files():
    for root in _ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _scan_filters():
    offenders, seen = [], set()
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = vg._docstring_nodes(tree)
        rel = _rel(path)
        for name, unit in vg._units(tree):
            hit = any(
                isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
                and _FILTER_RE.search(n.value)
                for n in ast.walk(unit)
            )
            if not hit:
                continue
            if (rel, name) in _FILTER_EXEMPT:
                seen.add((rel, name))
            else:
                offenders.append(f"{rel}:{unit.lineno} {name}")
    return offenders, seen


def _scan_attrs():
    offenders, seen = [], set()
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = _rel(path)
        for name, unit in vg._units(tree):
            if not any(isinstance(n, ast.Attribute) and n.attr == "is_research" for n in ast.walk(unit)):
                continue
            if (rel, name) in _ATTR_EXEMPT:
                seen.add((rel, name))
            else:
                offenders.append(f"{rel}:{unit.lineno} {name}")
    return offenders, seen


def _has_marker(unit: ast.AST) -> bool:
    for n in ast.walk(unit):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in _MARKERS:
                return True
    return False


def _scan_markers():
    guarded, missing, seen = [], [], set()
    for path in vg._SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = vg._docstring_nodes(tree)
        rel = _rel(path)
        for name, unit in vg._units(tree):
            if not vg._queries_corpus(unit, docstrings):
                continue
            if (rel, name) in _MARKER_EXEMPT:
                seen.add((rel, name))
                continue
            guarded.append(f"{rel}:{name}")
            if not _has_marker(unit):
                missing.append(f"{rel}:{unit.lineno} {name}")
    return guarded, missing, seen


class IsResearchFilterGuardTests(unittest.TestCase):
    def test_no_is_research_filter_in_sql(self):
        offenders, _ = _scan_filters()
        self.assertEqual(
            offenders, [],
            "這些 SQL 直接以 is_research 過濾：改用 app/services/doc_types.py 的 "
            f"doc_type_enabled_sql(別名, 功能)，或在 _FILTER_EXEMPT 寫明理由：{offenders}",
        )

    def test_no_is_research_attribute_reads(self):
        offenders, _ = _scan_attrs()
        self.assertEqual(
            offenders, [],
            "這些程式以 .is_research 做判斷：改用 doc_types.ingest_gate／is_enabled（依 doc_type），"
            f"或在 _ATTR_EXEMPT 寫明理由：{offenders}",
        )

    def test_filter_regex_catches_known_forms(self):
        for bad in (
            "WHERE r.is_research IS NOT FALSE", "AND is_research = true", "WHERE is_research",
            "WHERE r.is_research\n  AND x", "is_research <> false",
        ):
            with self.subTest(bad=bad):
                self.assertRegex(bad, _FILTER_RE)
        for ok in (
            "INSERT INTO research.research_report (id, doc_type, is_research) VALUES (:id, :doc_type, :is_research)",
            "SELECT r.market, r.is_research, r.confidence FROM research.research_report r",
        ):
            with self.subTest(ok=ok):
                self.assertNotRegex(ok, _FILTER_RE)

    def test_exemptions_are_not_stale(self):
        _, f_seen = _scan_filters()
        _, a_seen = _scan_attrs()
        self.assertEqual(sorted(set(_FILTER_EXEMPT) - f_seen), [], "_FILTER_EXEMPT 有已不存在或已不命中的項目")
        self.assertEqual(sorted(set(_ATTR_EXEMPT) - a_seen), [], "_ATTR_EXEMPT 有已不存在或已不命中的項目")


class DocTypeMarkerGuardTests(unittest.TestCase):
    def test_every_user_read_query_filters_doc_type(self):
        guarded, missing, _ = _scan_markers()
        self.assertGreaterEqual(len(guarded), 18, f"抓到的查詢太少，掃描規則八成失效了：{guarded}")
        self.assertEqual(
            missing, [],
            "這些面向使用者的查詢沒有帶文件類型片段：加上 app/services/doc_types.py 的 "
            "doc_type_enabled_sql(別名, 功能)／doc_type_enabled_id_sql(欄位, 功能)，"
            f"或在 _MARKER_EXEMPT 寫明理由：{missing}",
        )

    def test_exemptions_are_not_stale(self):
        _, _, seen = _scan_markers()
        self.assertEqual(sorted(set(_MARKER_EXEMPT) - seen), [], "_MARKER_EXEMPT 有已不存在或已不查語料表的項目")


if __name__ == "__main__":
    unittest.main()
