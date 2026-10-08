# tests/test_sql_bind_casts.py
"""全 repo 守門：bind 參數不可緊貼 PostgreSQL 轉型（`:x::type`），一律寫 `CAST(:x AS type)`。

SQLAlchemy `text()` 以 `(?<![:\\w\\x5c]):(\\w+)(?!:)` 掃 bind 參數：`:since_days::int` 的負向
前瞻在完整名稱後失敗，regex 回溯成短名 `since_day`；編譯時這個短名又對不上，整段
`:since_days::int` 原樣送進 PostgreSQL，每次執行都是語法錯誤。比對 SQL 字串的測試照樣綠
（它比對的正是壞掉的字串），`scripts/lost_anchors_to_delta.py` 就是這樣壞的。

掃描範圍是 app/、web/、scripts/、eval/ 所有 .py 的字串常數（AST，含隱式串接後的整段與
f-string 的常數段），排除 docstring：模組說明本來就會逐字寫出被禁的寫法（例：
`app/services/reading/queries.py`）。不掃 tests/（測試刻意寫壞的範例）與
db/migrations/versions/（已套用的 revision 不再修改）。

SQL 字串（含 SELECT／INSERT／UPDATE／DELETE／WHERE／FROM）只用來算反向守門的下限；違規
則所有字串都查：由 builder 拼接的 WHERE 片段（`"r.market = :m"`）多半不含關鍵字，卻正是
最容易寫出 `:x::text[]` 的地方。違規一律改成 CAST，不設豁免清單。
"""
import ast
import re
import sys
import unittest
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SCAN_ROOTS = ("app", "web", "scripts", "eval")

# 緊貼參數名的 `::`。前面是 `:` 或識別字字元時不是 bind（`a::text`、`x::date::text`），
# `ARRAY[:x]::text[]`、`max(:x)::int` 的 `::` 前面隔著括號，綁定正確、不算違規。
BIND_CAST_RE = re.compile(r"(?<![:\w\\]):[A-Za-z_]\w*::")
SQL_KEYWORD_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|WHERE|FROM)\b")

# 反向守門：掃到的 SQL 字串總數下限（2026-10 實測約 590）。規則或走訪方式改壞時
# 數量會掉到零附近，這條擋住「什麼都沒掃到所以永遠綠」。
MIN_SQL_STRINGS = 400


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                nodes.add(id(body[0].value))
    return nodes


def _string_constants(path: Path):
    """回傳 (行號, 字串)：檔內所有非 docstring 的字串常數。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def _scan():
    """回傳 (違規清單, 各根目錄的 SQL 字串數)。"""
    offenders = []
    sql_counts = {}
    for root in SCAN_ROOTS:
        sql_counts[root] = 0
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            for lineno, value in _string_constants(path):
                if SQL_KEYWORD_RE.search(value):
                    sql_counts[root] += 1
                for m in BIND_CAST_RE.finditer(value):
                    offenders.append(f"{rel}:{lineno}: {m.group(0)}")
    return offenders, sql_counts


class BindCastScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.offenders, cls.sql_counts = _scan()

    def test_no_postgres_cast_glued_to_bind_parameter(self):
        self.assertEqual(
            self.offenders, [], "bind 參數的轉型改寫成 CAST(:x AS type)，`:x::type` 會讓 text() 綁錯參數"
        )

    def test_scan_reaches_enough_sql_strings(self):
        total = sum(self.sql_counts.values())
        self.assertGreaterEqual(total, MIN_SQL_STRINGS, self.sql_counts)

    def test_every_root_contributes_sql_strings(self):
        # 根目錄拼錯或搬家時 rglob 靜默回空，總數仍可能過下限。
        for root, count in self.sql_counts.items():
            with self.subTest(root=root):
                self.assertGreater(count, 0)


class BindCastRuleTests(unittest.TestCase):
    """規則本身要與 SQLAlchemy 的實際解析一致：該抓的抓得到、不該抓的不誤報。"""

    def _compiled(self, sql: str):
        return text(sql).compile(dialect=asyncpg.dialect())

    def test_flagged_forms_really_break_binding(self):
        for sql in (
            "WHERE (:since_days IS NULL OR d >= current_date - (:since_days::int))",
            "WHERE r.stock_targets @> :codes::text[]",
            "WHERE r.created_at >= :since::timestamptz",
        ):
            with self.subTest(sql=sql):
                self.assertRegex(sql, BIND_CAST_RE)
                # 參數名原樣留在送進 PG 的 SQL 裡，沒有變成 $n。
                self.assertRegex(str(self._compiled(sql)), r"(?<![:\w]):[A-Za-z_]\w*::")

    def test_cast_forms_bind_fully_and_are_not_flagged(self):
        for sql, names in (
            ("WHERE (CAST(:since_days AS int) IS NULL OR d >= current_date - CAST(:since_days AS int))",
             {"since_days"}),
            ("WHERE r.stock_targets @> ARRAY[:sc]::text[]", {"sc"}),
            ("SELECT max(updated_at)::date::text, '2026-10-08'::date FROM t WHERE x = :x", {"x"}),
            ("WHERE d >= (CAST(:d0 AS date) + 1)::timestamp AND t = :t ::int", {"d0", "t"}),
        ):
            with self.subTest(sql=sql):
                self.assertNotRegex(sql, BIND_CAST_RE)
                compiled = self._compiled(sql)
                self.assertEqual(set(compiled.binds), names)
                self.assertNotRegex(str(compiled), r"(?<![:\w]):[A-Za-z_]\w*")

    def test_docstrings_are_skipped_but_code_strings_are_not(self):
        tree = ast.parse(
            '"""模組說明：不可寫 `:x::text[]`。"""\n'
            "def f():\n"
            '    """函式說明 `:y::int`。"""\n'
            '    return "SELECT 1 WHERE a = :z::int"\n'
        )
        skip = _docstring_nodes(tree)
        flagged = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and BIND_CAST_RE.search(node.value)
        ]
        self.assertEqual(flagged, ["SELECT 1 WHERE a = :z::int"])


if __name__ == "__main__":
    unittest.main()
