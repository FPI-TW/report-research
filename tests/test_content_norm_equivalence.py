# tests/test_content_norm_equivalence.py
"""`content_norm` 生成欄 與 `textnorm.norm_for_match()` 的等價性（F4）。

混合檢索的字面路徑是 `content_norm LIKE '%term%'`，其中 `content_norm` 由 DB 的
GENERATED 運算式算出，而 term 由 Python 的 `norm_for_match()` 算出。**兩邊必須同義**。

失效時的症狀最難查：沒有例外、沒有日誌、沒有 500 —— 只有「LIKE 永遠不命中」，
召回悄悄變差，而 dense 路徑還會把結果補回來，所以連使用者都不見得察覺。

實測（2026-07-28，生產 DB）12 個候選字碼中有 **6 個分歧**，且**兩個方向都有**：

    \\x1c–\\x1f、\\x85  Python 的 \\s 視為空白而移除，PostgreSQL 的 \\s 不視為 → Python 較短
    İ (U+0130)        PG 小寫成 'i'，Python 小寫成 'i̇'（帶組合點）→ Python 較長

今天只影響 568,150 筆中的 2 筆，所以本檔的價值是**回歸防線**而非修 bug：
它鎖住「已知的分歧集合」，任何一邊的正規化被改動（例如有人動了 schema 的
運算式或 textnorm 的 regex）都會讓這裡變紅。

**跑得到 DB 才有意義**：需要 DB 的那組原本無條件 `SkipTest`，於是在沒有 DB 的
環境（本機常態、CI 直到 schema job 出現之前）它等於不存在。現在 CI 有一個帶
`pgvector/pgvector:pg16` service container 的獨立 job，並在那裡設
`REPORT_MARK_REQUIRE_DB=1`：有設就不准 skip——否則 service container 哪天壞掉，
只會安靜地退回零覆蓋，而那正是這份測試想避免的失效模式。
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.textnorm import norm_for_match  # noqa: E402

# 一般語料會出現的字元：兩邊必須完全一致。
_MUST_AGREE = [
    "台積電 2330 先進製程",
    "TSMC  N2\tramp",
    "全形　空白與Ａｌｐｈａ",       # 全形空白 + 全形英數（NFKC 會轉半形）
    "混合 CJK 與 latin 的 chunk",
    "換行\n與\r\n混用",
    "﻿零寬不換行空白開頭",          # U+FEFF
    "",
    "   ",
]

# 已知分歧（實測）。列在這裡不是「接受它」，而是鎖住範圍——
# 新增分歧會讓 test_no_new_divergences 變紅。
_KNOWN_DIVERGENT = ["\x1c", "\x1d", "\x1e", "\x1f", "\x85", "İ"]


class SchemaExpressionContractTests(unittest.TestCase):
    """靜態契約：schema 的運算式與 textnorm 的 docstring 必須指向同一個定義。"""

    def test_schema_still_uses_expected_expression(self):
        schema = (REPO_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        self.assertIn("normalize(content, NFKC)", schema)
        self.assertIn("regexp_replace", schema)
        self.assertIn("lower(", schema)

    def test_textnorm_documents_the_same_expression(self):
        """`norm_for_match` 的 docstring 必須寫明它對應哪一條 DB 運算式。

        兩邊的維護者不同時，這個 docstring 是唯一的連結。
        """
        src = (REPO_ROOT / "app" / "services" / "textnorm.py").read_text(encoding="utf-8")
        self.assertIn("content_norm generated column", src)
        self.assertIn("normalize(content, NFKC)", src)

    def test_python_side_is_nfkc_lower_strip_ws(self):
        """Python 端的三步驟順序不可改：NFKC → lower → 去空白。"""
        # 全形英數經 NFKC 轉半形後才小寫
        self.assertEqual(norm_for_match("Ａ Ｂ"), "ab")
        # 各種空白一律移除
        self.assertEqual(norm_for_match("a \t\n　b"), "ab")


class PostgresEquivalenceTests(unittest.TestCase):
    """對真實 PostgreSQL 比對（需要 DB；無法連線時跳過）。

    **每次都自建 engine 並 dispose**，不重用 `app.services.db` 的全域連線池：
    那個池的連線綁在建立它的 event loop 上，而每個 `asyncio.run` 都是新的 loop，
    跨 loop 重用會在清理時炸 `RuntimeError: Event loop is closed`——那是測試寫法
    的問題，不是待測行為的問題。
    """

    _SQL = "SELECT lower(regexp_replace(normalize(:v, NFKC), '\\s+', '', 'g'))"

    def _compare(self, values):
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.services.db import DATABASE_URL

        async def _run():
            eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
            try:
                async with eng.connect() as conn:
                    stmt = text(self._SQL)
                    return [
                        (v, (await conn.execute(stmt, {"v": v})).scalar(), norm_for_match(v))
                        for v in values
                    ]
            finally:
                await eng.dispose()

        try:
            return asyncio.run(_run())
        except Exception as exc:  # pragma: no cover - 環境相依
            # 設了 REPORT_MARK_REQUIRE_DB 的環境（CI 的 schema job）承諾 DB 一定在，
            # 所以連不上是**基礎設施壞了**，必須紅。安靜 skip 會讓這份測試在
            # 「service container 掛掉」那天無聲失效，而沒有人看得出來。
            if os.getenv("REPORT_MARK_REQUIRE_DB"):
                raise
            raise unittest.SkipTest(f"DB 不可用，跳過等價性比對：{exc}")

    def test_ordinary_corpus_text_agrees(self):
        """一般語料必須逐字一致——這是字面召回正確性的根本。"""
        for v, pg, py in self._compare(_MUST_AGREE):
            with self.subTest(value=repr(v)):
                self.assertEqual(pg, py, f"PG 與 Python 對 {v!r} 正規化不一致")

    def test_no_new_divergences(self):
        """已知分歧集合不得擴大。

        新增分歧代表某一邊的正規化被改動了，而字面召回會**無聲**變差。
        """
        probes = [f"a{c}b" for c in _KNOWN_DIVERGENT] + [
            f"a{c}b" for c in ["​", " ", " ", "ﬁ", "①", "Ⅳ"]
        ]
        diverged = [
            v for v, pg, py in self._compare(probes) if pg != py
        ]
        expected = {f"a{c}b" for c in _KNOWN_DIVERGENT}
        self.assertEqual(
            set(diverged), expected,
            f"分歧集合改變了。新增：{set(diverged) - expected}；"
            f"消失：{expected - set(diverged)}（消失是好事，請同步更新 _KNOWN_DIVERGENT）",
        )


if __name__ == "__main__":
    unittest.main()
