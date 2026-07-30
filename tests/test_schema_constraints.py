# tests/test_schema_constraints.py
"""`research` schema 的 CHECK 約束清單對帳（golden：`db/expected_constraints.txt`）。

**要關掉的破口**：本 repo 沒有 migration 工具，`db/schema.sql` 全部用
`CREATE TABLE IF NOT EXISTS`。對**既有**表那是完全的 no-op ——欄位還有
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 補，CHECK 約束一個都沒有。所以：

    有人在 schema.sql 的 CHECK 加了一個新的 enum 值
    → 乾淨的庫（測試／新機器）有，生產庫沒有
    → `make schema` 照樣印 0 錯誤
    → 症狀是生產寫入被拒（或某個新狀態永遠存不進去），而 schema.sql 看起來完全正確

這支測試對「套完 schema.sql 的乾淨庫」抓出實際生效的約束定義，逐行與 golden 比對。
變更會變紅，逼提交者同時寫出給既有庫用的 ALTER（並在 golden 留下痕跡）。

**排序刻意在 Python 端做**（`sorted()`），不靠 SQL 的 `ORDER BY 1`：後者受 DB
initdb 的 collation locale 影響，換一顆映像就可能換順序，那種紅是假的。

DB 不可用時預設 skip；**設了 `REPORT_MARK_REQUIRE_DB` 就不准 skip**（CI 的 schema
job 會設）——否則 service container 壞掉只會安靜退回零覆蓋。
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

GOLDEN = REPO_ROOT / "db" / "expected_constraints.txt"

# 取「實際生效」的定義而非 schema.sql 原文：pg_get_constraintdef 是 PostgreSQL 自己
# 反解析出來的，所以 `x IN (a,b)` 會變成 `x = ANY (ARRAY[a,b])`。這正是我們要的——
# 比對的是庫裡真的長什麼樣，不是我們以為寫了什麼。
_SQL = """
SELECT t.relname || '.' || c.conname || ' = ' || pg_get_constraintdef(c.oid)
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'research' AND c.contype = 'c'
"""


def _read_golden() -> list[str]:
    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    return sorted(s for s in (line.strip() for line in lines) if s and not s.startswith("#"))


def _fetch_actual() -> list[str]:
    """每次自建 engine 並 dispose，不重用 app.services.db 的全域池。

    理由同 tests/test_content_norm_equivalence.py：那個池的連線綁在建立它的
    event loop 上，跨 `asyncio.run` 重用會在清理時炸 RuntimeError——那是測試
    寫法的問題，不是待測行為的問題。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.db import DATABASE_URL

    async def _run():
        eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
        try:
            async with eng.connect() as conn:
                return list((await conn.execute(text(_SQL))).scalars().all())
        finally:
            await eng.dispose()

    try:
        return sorted(asyncio.run(_run()))
    except Exception as exc:  # pragma: no cover - 環境相依
        if os.getenv("REPORT_MARK_REQUIRE_DB"):
            raise
        raise unittest.SkipTest(f"DB 不可用，跳過 CHECK 約束對帳：{exc}")


class GoldenFileShapeTests(unittest.TestCase):
    """不需要 DB 的部分：golden 檔本身必須存在且格式可解析。

    這幾條在本機也會跑，作用是「golden 被誤刪／寫壞時立刻知道」，而不是等到
    有 DB 的 CI job 才發現。
    """

    def test_golden_exists_and_is_parseable(self):
        rows = _read_golden()
        self.assertTrue(rows, "golden 是空的——若真的一條 CHECK 都不該有，請在檔中寫明理由")
        for row in rows:
            self.assertIn(" = CHECK ", row, f"格式應為 `表.約束名 = CHECK (...)`：{row!r}")

    def test_golden_has_no_duplicate_keys(self):
        keys = [r.split(" = ", 1)[0] for r in _read_golden()]
        self.assertEqual(len(keys), len(set(keys)), "golden 有重複的 表.約束名")


class AppliedSchemaMatchesGoldenTests(unittest.TestCase):
    def test_check_constraints_match_golden(self):
        actual = _fetch_actual()

        # 明確的更新路徑：改了約束時用這個重寫 golden，而不是手抄
        # pg_get_constraintdef 的輸出（括號與 ::text 轉型抄錯就是一場假紅）。
        if os.getenv("REPORT_MARK_WRITE_CONSTRAINTS"):
            header = [
                line
                for line in GOLDEN.read_text(encoding="utf-8").splitlines()
                if line.startswith("#") or not line.strip()
            ]
            GOLDEN.write_text("\n".join(header + actual) + "\n", encoding="utf-8")
            self.skipTest("REPORT_MARK_WRITE_CONSTRAINTS 已設：已重寫 golden，請檢視 diff")

        expected = _read_golden()
        missing = [r for r in expected if r not in actual]
        extra = [r for r in actual if r not in expected]
        self.assertEqual(
            (missing, extra),
            ([], []),
            "CHECK 約束與 db/expected_constraints.txt 不符。\n"
            f"golden 有但庫裡沒有（{len(missing)}）：\n  " + "\n  ".join(missing) + "\n"
            f"庫裡有但 golden 沒有（{len(extra)}）：\n  " + "\n  ".join(extra) + "\n"
            "若這是刻意的變更：(1) 用 REPORT_MARK_WRITE_CONSTRAINTS=1 重寫 golden，"
            "(2) 為既有庫寫出對應的 ALTER TABLE（CREATE TABLE IF NOT EXISTS 補不到）。",
        )


if __name__ == "__main__":
    unittest.main()
