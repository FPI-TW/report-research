"""E1d：回填腳本的純函式與 unit 契約，以及 store 的原地更新／重錨定 SQL。不碰 DB。"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services import store

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD / "report-mark-backfill.service"
TIMER = SYSTEMD / "report-mark-backfill.timer"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_extraction", ROOT / "scripts" / "backfill_extraction.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backfill_extraction"] = mod
    spec.loader.exec_module(mod)
    return mod


bf = _load()


def _directives(path: Path, key: str) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            out.append(v.strip())
    return out


class ScriptTests(unittest.TestCase):
    def test_candidates_newest_first_nulls_last_and_version_mismatch_only(self):
        sql = " ".join(bf.CANDIDATES_SQL.split())
        self.assertIn("extraction_version IS DISTINCT FROM :target", sql, "只挑版本不同者；IS DISTINCT FROM 才含 NULL")
        self.assertIn("ORDER BY r.report_date DESC NULLS LAST, r.id", sql)
        self.assertIn("LIMIT :limit", sql)

    def test_budget_expires(self):
        b = bf.Budget(None)
        self.assertFalse(b.exhausted())
        b = bf.Budget(0.0001)
        time.sleep(0.02)
        self.assertTrue(b.exhausted())

    def test_resolve_path_falls_back_to_mirror_dir(self):
        with mock.patch.object(bf, "SRC", ROOT / "tests"):
            self.assertEqual(bf.resolve_path("/nonexistent/x.pdf", "conftest.py"), ROOT / "tests" / "conftest.py")
            self.assertIsNone(bf.resolve_path("/nonexistent/x.pdf", "nope-9f3c.pdf"))

    def test_backfill_updates_in_place_never_via_upsert_report(self):
        src = inspect.getsource(bf)
        self.assertNotIn("upsert_report(", src, "回填不得走 DELETE+INSERT 的 upsert_report——會 CASCADE 清掉摘錄與訊號")
        self.assertIn("replace_report_extraction(", src)
        self.assertIn("reanchor_takeaways(", src)
        self.assertIn("mark_report_extraction(", src, "抽不出字時要保留舊文、只標版本")

    def test_no_llm_and_no_claude_lock(self):
        src = inspect.getsource(bf)
        for token in ("run_claude", "claude_cli_lock", "stream_completion", "llm."):
            self.assertNotIn(token, src)

    def test_each_report_commits_or_rolls_back_on_its_own(self):
        src = inspect.getsource(bf._backfill_path)
        self.assertGreaterEqual(src.count("await session.commit()"), 2)
        self.assertIn("await session.rollback()", inspect.getsource(bf.run))

    def test_r2_temp_directory_is_cleaned_when_backfill_raises(self):
        class _Storage:
            enabled = True

            def download_bytes(self, key):
                return b"pdf bytes"

        class _TrackedTemporaryDirectory:
            instances = []

            def __init__(self, **kwargs):
                self._real = tempfile.TemporaryDirectory(**kwargs)
                self.name = self._real.name
                self.cleaned = False
                self.instances.append(self)

            def cleanup(self):
                self.cleaned = True
                self._real.cleanup()

        async def _boom(*args, **kwargs):
            raise RuntimeError("extract failed")

        digest = __import__("hashlib").sha256(b"pdf bytes").hexdigest()
        row = ("rid", digest, "source.pdf", "/does-not-exist", "originals/aa/a.pdf", None)
        with mock.patch.object(bf, "resolve_path", return_value=None), \
             mock.patch.object(bf, "get_object_storage", return_value=_Storage()), \
             mock.patch.object(bf, "TemporaryDirectory", _TrackedTemporaryDirectory), \
             mock.patch.object(bf, "_backfill_path", _boom):
            with self.assertRaisesRegex(RuntimeError, "extract failed"):
                asyncio.run(bf.backfill_one(object(), row, "pypdf", "target", 0.6, 8))
        self.assertTrue(_TrackedTemporaryDirectory.instances[0].cleaned)
        self.assertFalse(Path(_TrackedTemporaryDirectory.instances[0].name).exists())

    def test_hybrid_prefers_valid_r2_over_mismatched_local_file(self):
        expected = __import__("hashlib").sha256(b"remote").hexdigest()
        captured = {}

        class _Storage:
            enabled = True
            mode = "hybrid"

            def download_bytes(self, key):
                return b"remote"

        async def _capture(_session, _rid, _hash, _name, path, *args, **_kwargs):
            captured["bytes"] = path.read_bytes()
            return "replaced"

        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"stale-local")
            local.flush()
            row = ("rid", expected, "source.pdf", local.name, "originals/x/source.pdf", None)
            with mock.patch.object(bf, "get_object_storage", return_value=_Storage()), \
                 mock.patch.object(bf, "_backfill_path", _capture):
                self.assertEqual(asyncio.run(bf.backfill_one(object(), row, "pypdf", "target", 0.6, 8)), "replaced")
        self.assertEqual(captured["bytes"], b"remote")

    def test_r2_never_falls_back_to_mismatched_local_file(self):
        expected = __import__("hashlib").sha256(b"remote").hexdigest()

        class _Storage:
            enabled = True
            mode = "r2"

            def download_bytes(self, key):
                from app.services.object_storage import ObjectNotFound

                raise ObjectNotFound(key)

        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"stale-local")
            local.flush()
            row = ("rid", expected, "source.pdf", local.name, "originals/x/source.pdf", None)
            with mock.patch.object(bf, "get_object_storage", return_value=_Storage()), \
                 mock.patch.object(bf, "_backfill_path") as helper:
                self.assertEqual(asyncio.run(bf.backfill_one(object(), row, "pypdf", "target", 0.6, 8)), "missing_file")
        helper.assert_not_called()

    def test_hybrid_refuses_upload_when_local_hash_is_stale(self):
        expected = __import__("hashlib").sha256(b"expected").hexdigest()

        class _Storage:
            enabled = True
            mode = "hybrid"
            uploads = []

            def upload_file(self, path, key, *, expected_sha256):
                self.uploads.append((path, key))

        storage = _Storage()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"stale")
            local.flush()
            with mock.patch.object(bf, "get_object_storage", return_value=storage):
                with self.assertRaisesRegex(ValueError, "refusing R2 upload"):
                    asyncio.run(
                        bf._backfill_path(
                            object(), "rid", expected, "source.pdf", Path(local.name), "pypdf", "t", 0.6, 8
                        )
                    )
        self.assertEqual(storage.uploads, [])

    def test_local_source_replacement_is_rejected_before_extract_or_upload(self):
        original = b"original source"
        digest = __import__("hashlib").sha256(original).hexdigest()

        class _Storage:
            enabled = False
            mode = "local"

        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(original)
            source.flush()

            def replace_then_resolve(*_args):
                Path(source.name).write_bytes(b"replaced source")
                return Path(source.name)

            row = ("rid", digest, "source.pdf", source.name, None, None)
            with mock.patch.object(bf, "get_object_storage", return_value=_Storage()), \
                 mock.patch.object(bf, "resolve_path", side_effect=replace_then_resolve), \
                 mock.patch.object(bf, "_backfill_path") as backfill:
                with self.assertRaisesRegex(RuntimeError, "snapshot SHA-256 mismatch"):
                    asyncio.run(bf.backfill_one(object(), row, "pypdf", "target", 0.6, 8))
        backfill.assert_not_called()

    def test_source_key_lost_race_is_detected_without_overwrite(self):
        data = b"verified source"
        digest = __import__("hashlib").sha256(data).hexdigest()
        uploads = []

        class _Storage:
            enabled = True

            def upload_file(self, path, key, *, expected_sha256):
                uploads.append((Path(path).read_bytes(), key, expected_sha256))

        class _Result:
            rowcount = 0

        class _Session:
            calls = []

            async def execute(self, stmt, params):
                self.calls.append((str(stmt), params))
                return _Result()

        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(data)
            source.flush()
            session = _Session()
            with mock.patch.object(bf, "get_object_storage", return_value=_Storage()):
                with self.assertRaisesRegex(RuntimeError, "update lost race"):
                    asyncio.run(
                        bf._backfill_path(
                            session, "rid", digest, "source.pdf", Path(source.name), "pypdf", "target", 0.6, 8,
                            expected_source_object_key=None,
                        )
                    )
        self.assertEqual(uploads[0][2], digest)
        self.assertIn("IS NOT DISTINCT FROM", session.calls[0][0])
        self.assertIsNone(session.calls[0][1]["expected_old"])

    def test_dry_run_unpacks_six_columns_without_mutation(self):
        row = ("rid", "a" * 64, "source.pdf", "/unused", "originals/aa/source.pdf", None)

        class _Result:
            def __init__(self, value):
                self.value = value

            def scalar(self):
                return 1

            def all(self):
                return [row]

        class _Session:
            calls = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, stmt, params):
                self.calls.append(str(stmt))
                return _Result(None)

        session = _Session()
        args = SimpleNamespace(extractor="pdfplumber", max_minutes=None, dry_run=True, limit=1, batch_size=8)
        with mock.patch.object(bf, "SessionFactory", lambda: session), \
             mock.patch.object(bf, "get_object_storage", side_effect=AssertionError("must not touch R2")):
            self.assertEqual(asyncio.run(bf.run(args)), 0)
        self.assertEqual(len(session.calls), 2)


class StoreTests(unittest.TestCase):
    def test_replace_report_extraction_updates_by_id_and_replaces_chunks(self):
        src = inspect.getsource(store.replace_report_extraction)
        self.assertIn("DELETE FROM research.report_chunk WHERE report_id = :rid", src)
        self.assertIn("UPDATE research.research_report SET", src)
        self.assertNotIn("DELETE FROM research.research_report", src, "原地更新不得刪研報列")
        for col in ("full_text", "extractor", "extraction_version", "quality_score", "quality_flags",
                    "page_count", "pages_failed", "needs_review"):
            self.assertIn(f"{col} = ", src)

    def test_reanchor_rewrites_sha_for_every_row_even_when_unanchored(self):
        src = inspect.getsource(store.reanchor_takeaways)
        self.assertIn("text_sha256 = :sha", src)
        self.assertIn("quote_start = :qs", src)
        self.assertIn('"qs": a.start if a else None', src)

    def test_mark_keeps_text_and_flags_review(self):
        src = inspect.getsource(store.mark_report_extraction)
        self.assertNotIn("full_text", src)
        self.assertIn("needs_review = true", src)


class UnitContractTests(unittest.TestCase):
    def test_units_exist(self):
        self.assertTrue(SERVICE.is_file() and TIMER.is_file())

    def test_service_runs_the_script_with_explicit_extractor_and_budget(self):
        exec_ = _directives(SERVICE, "ExecStart")[0]
        self.assertIn("scripts/backfill_extraction.py", exec_)
        self.assertIn("--extractor pdfplumber", exec_, "回填目標不該跟環境檔的 EXTRACTOR 漂")
        self.assertIn("--max-minutes 240", exec_)
        self.assertIn("REPORT_MARK_ROOT", exec_)

    def test_service_timeout_exceeds_budget(self):
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        self.assertGreater(timeout, 240 * 60 + 600, "逾時要大於預算＋最後一篇的收尾，否則 systemd 會在收尾時砍掉它")

    def test_service_is_oneshot_with_hardcoded_home_and_alert_chain(self):
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        self.assertEqual(_directives(SERVICE, "OnFailure"), ["report-mark-alert@%n.service"])
        homes = [v for v in _directives(SERVICE, "Environment") if v.startswith("HOME=")]
        self.assertEqual(homes, ["HOME=/home/kashionz"])
        self.assertTrue(any("EnvironmentFile" in ln for ln in SERVICE.read_text(encoding="utf-8").splitlines()))

    def test_timer_runs_at_one_am_and_never_catches_up(self):
        self.assertEqual(_directives(TIMER, "OnCalendar"), ["*-*-* 01:00:00"])
        self.assertEqual(_directives(TIMER, "Persistent"), ["false"], "E1 共識第 7 條：錯過的一晚不補跑")

    def test_sync_env_example_switches_extractor(self):
        env = SYSTEMD / "report-mark-sync.env.example"
        self.assertEqual(_directives(env, "EXTRACTOR"), ["pdfplumber"])


if __name__ == "__main__":
    unittest.main()
