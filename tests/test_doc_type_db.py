"""文件類型（revision 0012＋app/services/doc_types.py）對真的 PostgreSQL 成立。

`tests/test_doc_type_guard.py` 只保證每條使用者讀取 SQL 都呼叫了類型片段；這裡驗片段本身真的有效，
以及 revision 0012 的兩條 CHECK：

- 廷豐 profile：兩篇研報（含 chunk 與訊號）把其一改成 `non_research`（等同改動前的 is_research=false）之後，
  混合檢索、檢索頁瀏覽、問答選篇、閱讀頁、相似研報、觀點雷達、總覽、每日簡報與原檔 presign 都看不到它；
  改回 `research` 全部回來。讀取路徑與 `tests/test_report_visibility_db.py` 的同一份 `_snapshot`。
- 自訂 profile（`non_research` 只開閱讀）：閱讀頁、原檔看得到，檢索、雷達、簡報、總覽看不到——
  功能是逐項開關，不是整份文件一起。
- `upsert_report` 由 doc_type 推得 is_research；兩欄不一致、類型名稱形狀不對都被 DB 拒絕。
- 庫裡既有的每一列（在測試環境就是真實語料）：廷豐 profile 下各功能的片段與改動前的
  `is_research IS NOT FALSE` 選到同一批（零回歸的直接證據；只讀）。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0012）就 skip。**一律 rollback、絕不 commit**。
回填本身（既有庫的 is_research true／false → research／non_research）在空庫驗不到，由 schema job 的
「doc_type 回填演練」步驟對 0011 的庫實跑 0012 驗證。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from app import profile as P
from app.services import doc_types, store

REPO_ROOT = Path(__file__).resolve().parents[1]

# 兩篇研報、讀取路徑快照與 savepoint 輔助沿用可見性的 DB 測試（tests/ 不是套件，依路徑載入）。
_spec = importlib.util.spec_from_file_location("_visibility_db", REPO_ROOT / "tests" / "test_report_visibility_db.py")
assert _spec is not None and _spec.loader is not None
vdb = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vdb  # 該檔的 @dataclass 要從 sys.modules 找回自己的模組
_spec.loader.exec_module(vdb)

_SET_TYPE = text(
    "UPDATE research.research_report SET doc_type = :t, is_research = (:t = 'research') WHERE file_hash = :h"
)

# 自訂 profile 下各路徑屬於哪個功能（_snapshot 的鍵 → non_research 只開 reading 時看不看得到 A）。
_READING_KEYS = ("doc", "chunk", "signals", "similar", "presign")
_HIDDEN_KEYS = ("hybrid", "dense", "lexical", "rank", "select", "browse", "radar", "brief", "brief_links")

_READING_ONLY_PROFILE = """
[institution]
name = "測試機構"
product_name = "測試研報平台"

[doc_types.research]
features = ["search", "reading", "tagging_markets", "summary", "title", "takeaway", "signal", "radar", "brief",
            "overview"]

[doc_types.non_research]
features = ["reading"]
"""


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


class DocTypeDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(vdb._with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "庫尚未套用 revision 0012")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_disabled_type_disappears_from_every_user_path(self):
        hash_a, hash_b = vdb._hashes("dt-")

        async def fn(session):
            pair = await vdb._setup_pair(session, hash_a, hash_b)
            before = await vdb._snapshot(session, pair, pair.rid_a)
            await session.execute(_SET_TYPE, {"t": "non_research", "h": hash_a})
            off = await vdb._snapshot(session, pair, pair.rid_a)
            await session.execute(_SET_TYPE, {"t": "research", "h": hash_a})
            back = await vdb._snapshot(session, pair, pair.rid_a)
            return before, off, back

        before, off, back = self._run(fn)
        for key in vdb._SET_KEYS:
            with self.subTest(path=key, phase="before"):
                self.assertEqual(before[key], {"A", "B"})
            with self.subTest(path=key, phase="non_research"):
                self.assertEqual(off[key], {"B"})
            with self.subTest(path=key, phase="back"):
                self.assertEqual(back[key], {"A", "B"})
        for key in ("coverage", "overview", "brief_count_delta", "brief_mix_total"):
            with self.subTest(path=key):
                self.assertEqual(off[key], before[key] - 1)
                self.assertEqual(back[key], before[key])
        self.assertEqual(before["catalog"], [(vdb.CODE, 2)])
        self.assertEqual(off["catalog"], [(vdb.CODE, 1)])
        self.assertEqual(off["lead_term"], f"{vdb.TERM}B")
        # 評等變動：A 既不是「這次」也不是 B 的「上次」。
        self.assertEqual(before["changes"], [("buy", "neutral")])
        self.assertEqual(off["changes"], [])
        self.assertEqual(back["changes"], [("buy", "neutral")])

    def test_features_toggle_independently(self):
        hash_a, hash_b = vdb._hashes("dtp-")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "acme"
        root.mkdir()
        (root / "profile.toml").write_text(textwrap.dedent(_READING_ONLY_PROFILE), encoding="utf-8")

        async def fn(session):
            pair = await vdb._setup_pair(session, hash_a, hash_b)
            await session.execute(_SET_TYPE, {"t": "non_research", "h": hash_a})
            with patch.dict(os.environ, {"PROFILE_DIR": str(root)}):
                P.reset_profile_cache()
                try:
                    return await vdb._snapshot(session, pair, pair.rid_a)
                finally:
                    P.reset_profile_cache()

        snap = self._run(fn)
        for key in _READING_KEYS:
            with self.subTest(path=key, feature="reading（開）"):
                self.assertEqual(snap[key], {"A", "B"})
        for key in _HIDDEN_KEYS:
            with self.subTest(path=key, feature="其他（關）"):
                self.assertEqual(snap[key], {"B"})
        self.assertEqual(snap["changes"], [])

    def test_upsert_writes_both_columns_and_constraints_bind_them(self):
        hash_a, _ = vdb._hashes("dtc-")

        async def fn(session):
            row = store.ReportRow(
                file_hash=hash_a, file_name="x.pdf", file_path="/nonexistent/x.pdf", market=None,
                doc_type="non_research", confidence=0.1, full_text="類型測試",
            )
            with vdb._NoCommit(session):
                rid = await store.upsert_report(session, row, ["類型測試"], [vdb._vec(0)])
            stored = (await session.execute(text(
                "SELECT doc_type, is_research FROM research.research_report WHERE id = CAST(:id AS uuid)"
            ), {"id": rid})).one()
            mismatch = await vdb._violation(session, text(
                "UPDATE research.research_report SET is_research = true WHERE id = CAST(:id AS uuid)"
            ), {"id": rid})
            bad_name = await vdb._violation(session, text(
                "UPDATE research.research_report SET doc_type = 'Bad-Name', is_research = false "
                "WHERE id = CAST(:id AS uuid)"
            ), {"id": rid})
            default = (await session.execute(text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'research' AND table_name = 'research_report' AND column_name = 'doc_type'"
            ))).one()
            return tuple(stored), mismatch, bad_name, tuple(default)

        stored, mismatch, bad_name, default = self._run(fn)
        self.assertEqual(stored, ("non_research", False))
        self.assertIsNotNone(mismatch)
        self.assertIn("research_report_doc_type_legacy_sync", mismatch)
        self.assertIsNotNone(bad_name)
        self.assertIn("research_report_doc_type_check", bad_name)
        # 舊版程式只寫 is_research（入庫閘只放行研報）：DEFAULT 'research' 讓它照樣通過同步約束。
        self.assertEqual(default, ("'research'::text", "NO"))

    def test_tingfong_fragments_select_the_same_rows_as_before(self):
        """庫裡既有的每一列：廷豐 profile 下每個功能的片段與 `is_research IS NOT FALSE` 一致（只讀）。"""

        async def fn(session):
            out = {}
            for feature in P.DOC_FEATURES:
                cond = doc_types.doc_type_enabled_sql("r", feature)
                out[feature] = (await session.execute(text(
                    "SELECT count(*) FROM research.research_report r "
                    f"WHERE ({cond}) IS DISTINCT FROM (r.is_research IS NOT FALSE)"
                ))).scalar_one()
            return out

        self.assertEqual(self._run(fn), {f: 0 for f in P.DOC_FEATURES})


if __name__ == "__main__":
    unittest.main()
