"""`scripts/ingest_connectors.py` 的輪詢迴圈：重跑不重複入庫、側檔不合格進失敗清單、失敗的下一輪重試。

`ingest_one` 換成假的（以 file_hash 模擬「已在庫」）；DB session、物件儲存、跳過名單都是假物件；profile 與
狀態目錄都在 tempfile。單篇入庫本身（含側檔覆寫）在 tests/test_ingest_core.py。
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.profile import PROFILE_DIR_ENV, reset_profile_cache  # noqa: E402
from scripts import ingest_connectors as ic  # noqa: E402
from scripts._ingest_core import Outcome  # noqa: E402

OLD = time.time() - 3600


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        pass


class _Recorder:
    def __init__(self):
        self.recorded: list = []

    async def record(self, file_hash, reason):
        self.recorded.append((file_hash, reason))


class FakeCorpus:
    """假的 ingest_one：以內容雜湊判斷已入庫；`fail_once` 裡的檔名第一次回 fail。"""

    def __init__(self):
        self.db: set[str] = set()
        self.calls: list[tuple[str, object, str | None]] = []
        self.fail_once: set[str] = set()

    async def __call__(self, session, path, *, metadata=None, connector=None, on_committed=None, dry_run=False,
                       **kw):
        self.calls.append((path.name, metadata, connector))
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in self.fail_once:
            self.fail_once.discard(path.name)
            return Outcome("fail", path, file_hash=h, stage="ingest", reason="RuntimeError('db')")
        if h in self.db:
            return Outcome("skip_exists", path, file_hash=h)
        if dry_run:
            return Outcome("would_ingest", path, file_hash=h)
        self.db.add(h)
        if on_committed:
            on_committed(h)
        return Outcome("ingested", path, file_hash=h, report_id="rid", market="TW", chunks=3, cache_written=True,
                       tag_skipped=bool(metadata and metadata.skips_tagging()))


class IngestConnectorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.drop = self.tmp / "drop"
        self.data = self.tmp / "connector-data"
        self.profile = self.tmp / "profile"
        self.profile.mkdir()
        self.write_profile(f"""
            [connectors]
            enabled = ["drop"]
            [connectors.sources.drop]
            kind = "folder"
            path = "{self.drop}"
            settle_seconds = 0
            poll_minutes = 60
        """)
        self.corpus = FakeCorpus()
        self.recorder = _Recorder()
        st = contextlib.ExitStack()
        self.addCleanup(st.close)
        st.enter_context(mock.patch.dict(os.environ, {PROFILE_DIR_ENV: str(self.profile)}))
        st.enter_context(mock.patch.object(config, "_SETTINGS", dataclasses.replace(
            config.get_settings(), connector_data_dir=str(self.data))))
        st.enter_context(mock.patch.object(ic, "ingest_one", self.corpus))
        st.enter_context(mock.patch("app.services.db.SessionFactory", _Session))
        st.enter_context(mock.patch("app.services.object_storage.get_object_storage", lambda: object()))
        st.enter_context(mock.patch("app.services.llm_failures.open_recorder",
                                    mock.AsyncMock(return_value=self.recorder)))
        st.enter_context(mock.patch("builtins.print"))
        reset_profile_cache()
        self.addCleanup(reset_profile_cache)

    def write_profile(self, body: str):
        (self.profile / "profile.toml").write_text(
            '[institution]\nname = "測試"\nproduct_name = "測試研報"\n' + textwrap.dedent(body), encoding="utf-8")
        reset_profile_cache()

    def put(self, rel: str, data: bytes, sidecar: str | None = None) -> Path:
        path = self.drop / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (OLD, OLD))
        if sidecar is not None:
            path.with_suffix(".json").write_text(sidecar, encoding="utf-8")
        return path

    async def round(self, **kw) -> dict:
        args = argparse.Namespace(only=None, force=True, dry_run=False, limit=None, batch_size=8, hashes_out=None)
        for k, v in kw.items():
            setattr(args, k, v)
        return await ic._run(args)

    def hashes(self) -> list[str]:
        return [ln for ln in (self.data / ic.HASHES_NAME).read_text(encoding="utf-8").splitlines() if ln]

    def failures(self) -> list[list[str]]:
        path = self.data / ic.FAILURES_NAME
        return [ln.split("\t") for ln in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    # ── 重跑不重複入庫 ─────────────────────────────────────────────────────
    async def test_second_round_does_not_resend_processed_files(self):
        self.put("券商A/a.pdf", b"%PDF-a", '{"market": "TW", "stock_targets": ["2330"]}')
        self.put("b.pdf", b"%PDF-b")
        stats = await self.round()
        self.assertEqual((stats["ingested"], stats["tag_skipped"], stats["polled"]), (2, 1, 1))
        self.assertEqual(len(self.hashes()), 2)
        names = [c[0] for c in self.corpus.calls]
        self.assertEqual(names, ["b.pdf", "a.pdf"])
        meta = dict((c[0], c[1]) for c in self.corpus.calls)["a.pdf"]
        self.assertEqual(meta.market, "TW")
        self.assertEqual({c[2] for c in self.corpus.calls}, {"drop"})
        stats_file = (self.data / ic.STATS_NAME).read_text(encoding="utf-8")
        self.assertIn("abnormal=0", stats_file)

        self.corpus.calls.clear()
        stats = await self.round()
        self.assertEqual((stats["candidates"], stats["ingested"]), (0, 0))
        self.assertEqual(self.corpus.calls, [], "處理紀錄裡指紋相同的檔不再送")
        self.assertEqual(self.hashes(), [], "本輪 hashes 每輪覆寫")

    async def test_lost_state_falls_back_to_file_hash_dedup(self):
        self.put("a.pdf", b"%PDF-a")
        await self.round()
        (self.data / "state" / "drop.json").unlink()
        self.corpus.calls.clear()
        stats = await self.round()
        self.assertEqual((stats["candidates"], stats["skip_exists"], stats["ingested"]), (1, 1, 0))
        self.assertEqual(len(self.corpus.db), 1, "紀錄遺失也不會重複入庫")
        self.assertEqual(self.hashes(), [])

    async def test_failures_are_retried_next_round(self):
        self.put("a.pdf", b"%PDF-a")
        self.corpus.fail_once = {"a.pdf"}
        stats = await self.round()
        self.assertEqual((stats["fail"], stats["ingested"]), (1, 0))
        self.assertEqual(self.failures()[0][:3], ["drop", "a.pdf", "ingest"])
        stats = await self.round()
        self.assertEqual(stats["ingested"], 1, "fail 不記進處理紀錄，下一輪重試")

    async def test_changed_file_is_resent(self):
        path = self.put("a.pdf", b"%PDF-a")
        await self.round()
        path.write_bytes(b"%PDF-a-v2")
        os.utime(path, (OLD + 10, OLD + 10))
        stats = await self.round()
        self.assertEqual(stats["ingested"], 1)

    # ── 側檔 ──────────────────────────────────────────────────────────────
    async def test_invalid_sidecar_goes_to_failure_list_until_fixed(self):
        path = self.put("a.pdf", b"%PDF-a", '{"market": "TWSE"}')
        stats = await self.round()
        self.assertEqual((stats["sidecar_invalid"], stats["ingested"]), (1, 0))
        self.assertEqual(self.corpus.calls, [], "側檔不合格的檔不入庫")
        failure = self.failures()[0]
        self.assertEqual(failure[:3], ["drop", "a.pdf", "sidecar"])
        self.assertIn("不是標準市場代碼", failure[3])
        self.assertIn("abnormal=1", (self.data / ic.STATS_NAME).read_text(encoding="utf-8"))

        stats = await self.round()
        self.assertEqual(stats["sidecar_invalid"], 0, "同一份壞側檔不每輪重複報")
        path.with_suffix(".json").write_text('{"market": "TW"}', encoding="utf-8")
        stats = await self.round()
        self.assertEqual(stats["ingested"], 1, "改好側檔後指紋改變，自動重送")

    # ── 輪詢間隔與連接器錯誤 ──────────────────────────────────────────────
    async def test_poll_interval_is_honoured(self):
        self.put("a.pdf", b"%PDF-a")
        await self.round(force=False)
        self.put("b.pdf", b"%PDF-b")
        stats = await self.round(force=False)
        self.assertEqual(stats["polled"], 0, "未到 poll_minutes 不輪詢")
        stats = await self.round(force=True)
        self.assertEqual(stats["ingested"], 1)

    async def test_broken_connector_does_not_block_others(self):
        self.write_profile(f"""
            [connectors]
            enabled = ["bucket", "drop"]
            [connectors.sources.bucket]
            kind = "s3"
            prefix = "p/"
            env = "CONNECTOR_TEST_MISSING"
            [connectors.sources.drop]
            kind = "folder"
            path = "{self.drop}"
            settle_seconds = 0
        """)
        self.put("a.pdf", b"%PDF-a")
        stats = await self.round()
        self.assertEqual((stats["connector_error"], stats["ingested"]), (1, 1))
        self.assertEqual(self.failures()[0][:3], ["bucket", "-", "connector"])

    async def test_corrupt_state_file_is_reported_not_reset(self):
        self.put("a.pdf", b"%PDF-a")
        (self.data / "state").mkdir(parents=True)
        (self.data / "state" / "drop.json").write_text("{", encoding="utf-8")
        stats = await self.round()
        self.assertEqual((stats["connector_error"], stats["ingested"]), (1, 0))
        self.assertEqual(self.corpus.calls, [])

    async def test_no_enabled_connectors_is_a_noop(self):
        self.write_profile("")
        stats = await self.round()
        self.assertEqual(stats["polled"], 0)
        self.assertEqual(self.hashes(), [])

    async def test_dry_run_writes_no_state(self):
        self.put("a.pdf", b"%PDF-a")
        stats = await self.round(dry_run=True)
        self.assertEqual(stats["ingested"], 1)
        self.assertFalse((self.data / "state" / "drop.json").exists())
        self.assertFalse((self.data / ic.HASHES_NAME).exists())

    async def test_only_must_name_an_enabled_connector(self):
        with self.assertRaises(SystemExit):
            await self.round(only=["nope"])


if __name__ == "__main__":
    unittest.main()
