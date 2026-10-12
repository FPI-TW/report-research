"""單篇入庫核心 `scripts/_ingest_core.ingest_one`：每一種結果、副作用順序與 `pre_upsert` hook。

不連網、不載模型、不連 DB：抽字、標註（`run_claude`）、嵌入、store 與物件儲存全換成假物件，
所有寫檔都導向 tempfile。hook 與 upsert 在真 PostgreSQL 上同交易的證明在
tests/test_ingest_core_db.py。sync 的計數／失敗紀錄由 tests/test_batch_http_dispatch.py 的
SyncInlineTagTests 走 `_run` 驗。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extract import ExtractResult  # noqa: E402
from scripts import _claude_cli as cc  # noqa: E402
from scripts import _ingest_core as ic  # noqa: E402

TAG_OK = {
    "market": "TW", "is_research": True, "confidence": 0.9, "instrument_types": ["equity"],
    "relates_stock": True, "relates_futures": False, "stock_targets": ["2330"], "futures_targets": [],
}
TEXT = "本報告討論半導體產業前景，維持買進評等。\n\n" * 40
NAME = "20260901_券商甲_台積電(2330)研究報告.pdf"


class _Session:
    def __init__(self, events: list):
        self.events = events

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


class _Storage:
    def __init__(self, events: list, enabled: bool = True):
        self.events = events
        self.enabled = enabled

    def upload_file(self, path, key, *, expected_sha256):
        self.events.append(("upload", key, expected_sha256))


class IngestOneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.tags_dir = self.tmp / "tags"
        self.path = self.tmp / NAME
        self.path.write_bytes(b"%PDF-1.4\n" + NAME.encode())
        self.file_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.events: list = []
        self.session = _Session(self.events)
        self.reports: list = []
        self.llm_calls: list = []
        self.llm_result = cc.CliResult(json.dumps(TAG_OK), None)
        self.exists = False
        self.extract = lambda path: ExtractResult(self.file_hash, TEXT, len(TEXT), False, "zh")

    def _fake_run_claude(self, prompt, model, **kw):
        self.llm_calls.append((model, kw))
        self.events.append("llm")
        return self.llm_result

    async def _fake_log(self, session, row):
        self.events.append(("log", row.stopped_at))

    async def _fake_upsert(self, session, report, chunks, embeddings):
        self.assertIs(session, self.session)
        self.reports.append((report, chunks, embeddings))
        self.events.append("upsert")
        return "rid-1"

    def _fake_embed(self, chunks, batch_size=8):
        self.events.append(("embed", len(chunks), batch_size))
        return [[0.0] * 4 for _ in chunks]

    async def run_one(self, *, path=None, **kw):
        with contextlib.ExitStack() as st:
            p = st.enter_context
            p(mock.patch("app.services.extract.extract_text", lambda path, *a, **k: self.extract(path)))
            p(mock.patch("app.services.store.report_exists", mock.AsyncMock(side_effect=lambda s, h: self.exists)))
            p(mock.patch("app.services.store.upsert_extraction_log", self._fake_log))
            p(mock.patch("app.services.store.upsert_report", self._fake_upsert))
            p(mock.patch("app.services.embed.embed_texts", self._fake_embed))
            p(mock.patch("app.services.extraction.cache.write_record", lambda rec: self.events.append("cache")))
            p(mock.patch.object(ic, "run_claude", self._fake_run_claude))
            kw.setdefault("storage", _Storage(self.events, enabled=False))
            kw.setdefault("tags_dir", self.tags_dir)
            return await ic.ingest_one(self.session, path or self.path, **kw)

    # ── 成功 ────────────────────────────────────────────────────────────────
    async def test_ingested_outcome_and_side_effect_order(self):
        committed: list = []
        out = await self.run_one(on_committed=lambda h: (committed.append(h), self.events.append("on_committed")))
        self.assertEqual(out.kind, "ingested")
        self.assertEqual((out.file_hash, out.report_id, out.market), (self.file_hash, "rid-1", "TW"))
        self.assertEqual(out.chunks, len(self.reports[0][1]))
        self.assertGreater(out.chunks, 0)
        self.assertEqual(
            (out.stopped_at, out.cache_written, out.stage, out.failure_kind), ("ingested", True, None, None)
        )
        self.assertEqual(committed, [self.file_hash])
        tail = self.events[self.events.index("upsert"):]
        self.assertEqual(tail, ["upsert", ("log", "ingested"), "commit", "on_committed", "cache"],
                         "commit → 通知已入庫 → 寫快取（審查 L9）")
        self.assertTrue((self.tags_dir / f"{self.file_hash}.json").is_file(), "新標註要寫進標註快取")
        model, kw = self.llm_calls[0]
        self.assertEqual(model, ic.TAG_MODEL)
        self.assertEqual(kw["max_tokens"], ic.TAG_MAX_TOKENS)
        self.assertEqual(kw["meta"], {"task": "tag", "file_hash": self.file_hash, "report_id": None})
        self.assertEqual(kw["timeout"], 150)

    async def test_cached_tag_skips_llm_and_tagged_paths(self):
        self.tags_dir.mkdir()
        (self.tags_dir / f"{self.file_hash}.json").write_text(json.dumps(TAG_OK), encoding="utf-8")
        tagged: dict = {}
        out = await self.run_one(tagged_paths=tagged)
        self.assertEqual(out.kind, "ingested")
        self.assertEqual(self.llm_calls, [])
        self.assertEqual(tagged, {})

    async def test_tagged_paths_records_files_sent_to_llm(self):
        tagged: dict = {}
        await self.run_one(tagged_paths=tagged)
        self.assertEqual(tagged, {self.file_hash: self.path})

    async def test_batch_size_is_passed_to_embedding(self):
        await self.run_one(batch_size=7)
        self.assertIn(7, [e[2] for e in self.events if isinstance(e, tuple) and e[0] == "embed"])

    async def test_cache_failure_is_fail_open(self):
        with mock.patch.object(ic, "_write_cache", side_effect=OSError("disk full")), \
                mock.patch("builtins.print"):
            out = await self.run_one()
        self.assertEqual((out.kind, out.cache_written), ("ingested", False))

    async def test_boilerplate_is_stripped_from_chunks_but_not_full_text(self):
        def strip(text, source):
            return text.replace("維持買進評等", ""), 1

        with mock.patch("app.services.boilerplate.strip_boilerplate", strip):
            await self.run_one()
        report, chunks, _ = self.reports[0]
        self.assertIn("維持買進評等", report.full_text)
        self.assertFalse(any("維持買進評等" in c for c in chunks))

    async def test_needs_review_uses_settings_thresholds(self):
        with mock.patch("app.services.store.needs_review", return_value=True) as nr:
            await self.run_one()
        self.assertTrue(self.reports[0][0].needs_review)
        from app.config import get_settings

        s = get_settings()
        self.assertEqual(nr.call_args.args[2], s.extraction_review_min)
        self.assertEqual(nr.call_args.kwargs, {
            "min_coverage": s.extraction_review_min_coverage, "max_garbled": s.extraction_review_max_garbled,
        })

    # ── R2 上傳順序 ─────────────────────────────────────────────────────────
    async def test_upload_before_upsert_with_expected_sha(self):
        out = await self.run_one(storage=_Storage(self.events))
        key = f"originals/{self.file_hash[:2]}/{self.file_hash}.pdf"
        self.assertEqual(out.kind, "ingested")
        self.assertLess(self.events.index(("upload", key, self.file_hash)), self.events.index("upsert"))
        self.assertEqual(self.reports[0][0].source_object_key, key)

    async def test_changed_source_is_not_uploaded(self):
        self.path.write_bytes(b"%PDF-1.4\nreplaced after extraction")
        out = await self.run_one(storage=_Storage(self.events))
        self.assertEqual((out.kind, out.stage), ("fail", "ingest"))
        self.assertIn("SHA-256 changed", out.reason)
        self.assertFalse([e for e in self.events if isinstance(e, tuple) and e[0] == "upload"])
        self.assertNotIn("upsert", self.events)
        self.assertIn("rollback", self.events)

    async def test_disabled_storage_has_no_object_key(self):
        await self.run_one()
        self.assertIsNone(self.reports[0][0].source_object_key)

    # ── pre_upsert hook ──────────────────────────────────────────────────────
    async def test_pre_upsert_runs_in_same_session_right_before_upsert(self):
        seen: list = []

        async def hook(session, report):
            seen.append((session, report.file_hash))
            self.events.append("hook")

        out = await self.run_one(pre_upsert=hook, storage=_Storage(self.events))
        self.assertEqual(out.kind, "ingested")
        self.assertEqual(seen, [(self.session, self.file_hash)])
        hook_at = self.events.index("hook")
        self.assertEqual(self.events[hook_at + 1], "upsert", "hook 緊接在 upsert_report 之前")
        self.assertLess(self.events.index(("upload", mock.ANY, self.file_hash)), hook_at, "原檔已上傳")
        self.assertNotIn("commit", self.events[:hook_at], "hook 之前不得 commit（否則不同交易）")

    async def test_pre_upsert_failure_rolls_back_and_skips_upsert(self):
        committed: list = []

        async def hook(session, report):
            raise RuntimeError("draft marker failed")

        out = await self.run_one(pre_upsert=hook, on_committed=committed.append)
        self.assertEqual((out.kind, out.stage), ("fail", "ingest"))
        self.assertIn("draft marker failed", out.reason)
        self.assertNotIn("upsert", self.events)
        self.assertEqual(self.events[-1], "rollback")
        self.assertEqual(committed, [])

    async def test_upsert_failure_after_hook_rolls_back(self):
        async def hook(session, report):
            self.events.append("hook")

        async def boom(session, report, chunks, embeddings):
            raise RuntimeError("expected 1024 dimensions")

        with mock.patch.object(self, "_fake_upsert", boom):
            out = await self.run_one(pre_upsert=hook)
        self.assertEqual((out.kind, out.stage), ("fail", "ingest"))
        self.assertEqual(self.events[-2:], ["hook", "rollback"])
        self.assertNotIn("commit", self.events)

    # ── 標註前的閘 ──────────────────────────────────────────────────────────
    async def test_missing_file(self):
        out = await self.run_one(path=self.tmp / "gone.pdf")
        self.assertEqual(out.kind, "missing")
        self.assertEqual(self.events, [])

    async def test_extract_exception(self):
        def boom(path):
            raise ValueError("bad pdf")

        self.extract = boom
        out = await self.run_one()
        self.assertEqual(
            (out.kind, out.stage, out.reason, out.file_hash), ("fail", "extract", "ValueError('bad pdf')", None)
        )
        self.assertEqual(self.events, [], "抽字拋例外不寫 extraction_log")

    async def test_extract_error_is_logged(self):
        self.extract = lambda path: ExtractResult(self.file_hash, "", 0, True, "zh", error="Stream ended")
        for dry in (False, True):  # dry-run 照舊寫這一列（抽出核心之前的 sync 就是這樣）
            self.events.clear()
            out = await self.run_one(dry_run=dry)
            self.assertEqual(
                (out.kind, out.stage, out.reason, out.stopped_at), ("fail", "extract", "Stream ended", "extract_error")
            )
            self.assertEqual(self.events, [("log", "extract_error"), "commit"])

    async def test_admin_file(self):
        path = self.tmp / "20260909_公告_人事異動.pdf"
        path.write_bytes(b"x")
        out = await self.run_one(path=path)
        self.assertEqual((out.kind, out.stopped_at), ("skip_admin", "skip_admin"))
        self.assertEqual(self.events, [("log", "skip_admin"), "commit"])

    async def test_scanned(self):
        self.extract = lambda path: ExtractResult(self.file_hash, "", 0, True, "zh")
        out = await self.run_one()
        self.assertEqual((out.kind, out.stopped_at), ("skip_scanned", "scanned"))
        self.assertEqual(self.events, [("log", "scanned"), "commit"])

    async def test_exists_writes_nothing(self):
        self.exists = True
        out = await self.run_one()
        self.assertEqual((out.kind, out.stopped_at, out.file_hash), ("skip_exists", None, self.file_hash))
        self.assertEqual(self.events, [])

    async def test_dry_run_stops_before_tagging_and_writes_no_gate_logs(self):
        out = await self.run_one(dry_run=True)
        self.assertEqual(out.kind, "would_ingest")
        self.assertEqual(self.events, [])
        self.extract = lambda path: ExtractResult(self.file_hash, "", 0, True, "zh")
        out = await self.run_one(dry_run=True)
        self.assertEqual((out.kind, out.stopped_at), ("skip_scanned", None))
        self.assertEqual(self.events, [])

    # ── 標註後的閘 ──────────────────────────────────────────────────────────
    async def test_non_research(self):
        self.llm_result = cc.CliResult(json.dumps({**TAG_OK, "is_research": False}), None)
        out = await self.run_one()
        self.assertEqual(
            (out.kind, out.stopped_at, out.stage, out.failure_kind), ("skip_non_research", "not_research", None, None)
        )
        self.assertEqual(self.events, ["llm", ("log", "not_research"), "commit"])

    async def test_tag_failures(self):
        cases = {
            "unparseable": (cc.CliResult("不是 JSON", None), "skip_untagged", "tag", None),
            "cli": (cc.CliResult(None, "CLI 退出碼 1：x"), "skip_untagged", "tag", None),
            "empty": (cc.CliResult(None, "API[empty] 空回應"), "skip_untagged", "tag", "empty"),
            "blocked": (cc.CliResult(None, "API[content_filter] 觸發供應商內容審查：HTTP 400"),
                        "skip_blocked", "tag_blocked", "content_filter"),
            "truncated": (cc.CliResult(None, "API[truncated] 輸出截斷：max_tokens=1024"),
                          "skip_truncated", "tag_truncated", "truncated"),
        }
        for name, (res, kind, stage, fk) in cases.items():
            with self.subTest(case=name):
                self.events.clear()
                self.llm_result = res
                out = await self.run_one()
                self.assertEqual((out.kind, out.stage, out.failure_kind, out.stopped_at), (kind, stage, fk, None))
                self.assertEqual(out.reason, res.error or "回應無法解析為標籤")
                self.assertEqual(self.events, ["llm"], "標註失敗不寫 extraction_log、不入庫")
                self.assertFalse((self.tags_dir / f"{self.file_hash}.json").exists())

    async def test_empty_chunks_is_scanned(self):
        with mock.patch("app.services.chunk.chunk_text", return_value=[]):
            out = await self.run_one()
        self.assertEqual((out.kind, out.stopped_at), ("skip_scanned", "scanned"))
        self.assertEqual(self.events[-2:], [("log", "scanned"), "commit"])

    async def test_batch_aborting_errors_propagate(self):
        """環境層級失敗與 400 升級要拋到入口中止整批，不可收成單篇結果。"""
        escalation = cc.BadRequestEscalation("API[bad_request] 同一則 400", ["h1", "h2"])
        for exc in (cc.CliNotFoundError("不在 PATH"), escalation):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(self, "_fake_run_claude", side_effect=exc):
                    with self.assertRaises(type(exc)):
                        await self.run_one()

    def test_kinds_are_the_sync_stats_keys_plus_two(self):
        """Outcome.kind 與 sync 的計數器鍵逐字相同（除了 missing／would_ingest），sync 直接拿它當鍵。"""
        from test_sync_new_reports import snr

        self.assertIn("ingested", ic.KINDS)
        sync_keys = set(snr.ABNORMAL_COUNTERS) | {
            "ingested", "skip_admin", "skip_scanned", "skip_exists", "skip_non_research",
        }
        self.assertEqual(set(ic.KINDS) - {"missing", "would_ingest"}, sync_keys)


if __name__ == "__main__":
    unittest.main()
