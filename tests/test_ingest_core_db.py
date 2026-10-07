"""`ingest_one` 的 `pre_upsert` hook 與 `store.upsert_report` 同一個交易（真 PostgreSQL）。

上傳 worker 要在 hook 裡寫草稿標記，靠 `upsert_report` 內部那次 commit 一起落庫：標記若落在
另一個交易，upsert 失敗時會留下沒有研報的孤兒標記，upsert 成功前也可能有一段研報已可見、標記
還沒寫的空窗（設計文件 1.4「原子性」）。這裡以 `research.report_visibility` 當 hook 的寫入目標，
證明兩件事：

1. upsert 在 hook 寫入之後失敗（真的 DB 錯誤：向量維度不符）→ 整筆 rollback，hook 的寫入不會留下，
   而且在那之前沒有任何 commit。
2. 成功時 hook 的寫入與研報在同一個交易裡可見，hook 與 upsert 之間沒有 commit。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0004）就 skip，`REPORT_MARK_REQUIRE_DB`
設定時改成紅燈。**一律 rollback、絕不 commit**——本機預設連到的是生產庫：session 的 commit 換成
記錄呼叫的假物件（`upsert_report` 內部的 commit 也是它），結束時整個 rollback。斷言只針對自己塞進去
的 file_hash。不連網、不載模型：抽字、嵌入、抽取快取都是假物件，標註走預先寫好的標註快取。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extract import ExtractResult  # noqa: E402
from scripts import _ingest_core as ic  # noqa: E402

TAG_OK = {
    "market": "TW", "is_research": True, "confidence": 0.9, "instrument_types": ["equity"],
    "relates_stock": True, "relates_futures": False, "stock_targets": ["2330"], "futures_targets": [],
}
TEXT = "本報告討論半導體產業前景，維持買進評等。\n\n" * 40


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


class PreUpsertSameTransactionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.file_hash = uuid.uuid4().hex + uuid.uuid4().hex  # 64 hex，符合 report_visibility 的 CHECK
        self.path = self.tmp / "20260901_券商甲_台積電(2330)研究報告.pdf"
        self.path.write_bytes(b"%PDF-1.4\n")
        self.tags_dir = self.tmp / "tags"
        self.tags_dir.mkdir()
        (self.tags_dir / f"{self.file_hash}.json").write_text(json.dumps(TAG_OK), encoding="utf-8")

    def _run(self, fn):
        try:
            return asyncio.run(self._with_session(fn))
        except unittest.SkipTest:
            raise
        except AssertionError:
            raise
        except Exception as exc:  # 連不上、或表還沒建
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "庫尚未套用 report_visibility（revision 0004）")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    async def _with_session(self, fn):
        """自建 engine、跑完 dispose——不重用 app.services.db 的全域池（理由見 test_schema_constraints）。"""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

        from app.services.db import DATABASE_URL

        eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
        try:
            async with AsyncSession(eng, expire_on_commit=False) as session:
                await session.execute(text("SELECT 1 FROM research.report_visibility LIMIT 0"))
                await session.rollback()
                commit = mock.AsyncMock()
                with mock.patch.object(session, "commit", new=commit):
                    try:
                        return await fn(session, commit)
                    finally:
                        await session.rollback()
        finally:
            await eng.dispose()

    async def _ingest(self, session, hook, *, dim: int):
        def fake_embed(chunks, batch_size=8):
            return [[0.001] * dim for _ in chunks]

        with mock.patch("app.services.extract.extract_text",
                        lambda path, *a, **k: ExtractResult(self.file_hash, TEXT, len(TEXT), False, "zh")), \
                mock.patch("app.services.embed.embed_texts", fake_embed), \
                mock.patch("app.services.extraction.cache.write_record", lambda rec: None):
            return await ic.ingest_one(
                session, self.path, storage=mock.Mock(enabled=False), tags_dir=self.tags_dir, pre_upsert=hook,
            )

    async def _counts(self, session) -> tuple[int, int]:
        from sqlalchemy import text

        vis = (await session.execute(
            text("SELECT count(*) FROM research.report_visibility WHERE file_hash = :h"), {"h": self.file_hash}
        )).scalar_one()
        rep = (await session.execute(
            text("SELECT count(*) FROM research.research_report WHERE file_hash = :h"), {"h": self.file_hash}
        )).scalar_one()
        return vis, rep

    def _hook(self, events: list, commit: mock.AsyncMock):
        async def hook(session, report):
            from sqlalchemy import text

            events.append(("hook", commit.await_count))
            await session.execute(
                text("INSERT INTO research.report_visibility (file_hash, hidden, reason) VALUES (:h, false, :r)"),
                {"h": report.file_hash, "r": "ingest-core 同交易測試"},
            )
            # 同一個交易內看得到自己的寫入
            assert (await self._counts(session))[0] == 1

        return hook

    def test_upsert_failure_rolls_back_hook_write(self):
        async def fn(session, commit):
            events: list = []
            out = await self._ingest(session, self._hook(events, commit), dim=3)  # 向量維度不符 → upsert 拋 DB 錯誤
            self.assertEqual((out.kind, out.stage), ("fail", "ingest"))
            self.assertIn("dimensions", out.reason)
            self.assertEqual(events, [("hook", 0)])
            self.assertEqual(commit.await_count, 0, "hook 與 upsert 之間（以及之前）不得 commit")
            self.assertEqual(await self._counts(session), (0, 0), "upsert 失敗時 hook 的寫入不得留下")

        self._run(fn)

    def test_success_hook_write_and_report_in_one_transaction(self):
        async def fn(session, commit):
            events: list = []
            out = await self._ingest(session, self._hook(events, commit), dim=1024)
            self.assertEqual(out.kind, "ingested", out)
            self.assertEqual(events, [("hook", 0)], "hook 執行時還沒有任何 commit")
            # upsert_report 內部一次、extraction_log 之後一次；hook 的寫入由第一次一起落庫
            self.assertEqual(commit.await_count, 2)
            self.assertEqual(await self._counts(session), (1, 1))

        self._run(fn)


if __name__ == "__main__":
    unittest.main()
