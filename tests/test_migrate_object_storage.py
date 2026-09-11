"""Mocked safety tests for the mutating-but-rerunnable legacy R2 migration."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "migrate_object_storage", ROOT / "scripts" / "migrate_object_storage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_object_storage"] = module
    spec.loader.exec_module(module)
    return module


migrate = _load()


def _args(kind="all", limit=0, dry_run=False):
    return argparse.Namespace(kind=kind, limit=limit, dry_run=dry_run, concurrency=2)


class _Storage:
    enabled = True

    def __init__(self, events):
        self.events = events

    def upload_file(self, path, key, *, expected_sha256):
        self.events.append(("upload", Path(path).read_bytes(), key))


async def _run(rows, args, events, update):
    async def fake_rows(kind, limit):
        events.append(("rows", kind, limit))
        return rows

    output = io.StringIO()
    with patch.object(migrate, "_rows", fake_rows), \
         patch.object(migrate, "get_object_storage", return_value=_Storage(events)), \
         patch.object(migrate, "_update_key", update), \
         contextlib.redirect_stdout(output):
        rc = await migrate.run(args)
    return rc, output.getvalue()


class MigrateObjectStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrency_bounds_hashing_and_uploads_including_dry_run(self):
        """The semaphore covers thread-backed verification as well as R2 operations."""
        active = 0
        maximum = 0
        lock = threading.Lock()

        def slow(operation):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return operation

        class _SlowStorage:
            enabled = True

            def upload_file(self, _path, _key, *, expected_sha256):
                slow(None)

        with tempfile.TemporaryDirectory() as directory:
            rows = []
            for index in range(4):
                path = Path(directory) / f"{index}.pdf"
                data = f"pdf-{index}".encode()
                path.write_bytes(data)
                rows.append(
                    {
                        "kind": "original", "id": str(index), "hash": hashlib.sha256(data).hexdigest(),
                        "name": path.name, "path": str(path),
                    }
                )

            def slow_hash(path):
                return slow(hashlib.sha256(Path(path).read_bytes()).hexdigest())

            async def fake_rows(_kind, _limit):
                return rows

            async def update(*_args):
                return True

            with patch.object(migrate, "_rows", fake_rows), \
                 patch.object(migrate, "_file_sha256", slow_hash), \
                 patch.object(migrate, "_update_key", update), \
                 patch.object(migrate, "get_object_storage", return_value=_SlowStorage()):
                self.assertEqual(await migrate.run(_args(dry_run=True)), 0)
                self.assertLessEqual(maximum, 2)
                maximum = 0
                self.assertEqual(await migrate.run(_args()), 0)
                self.assertLessEqual(maximum, 2)

    async def test_dry_run_verifies_original_but_does_not_upload_or_update(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"original")
            source.flush()
            digest = hashlib.sha256(b"original").hexdigest()
            events = []

            async def update(*_args):
                events.append(("update",))
                return True

            rc, output = await _run(
                [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": source.name}],
                _args("originals", dry_run=True), events, update,
            )
        self.assertEqual(rc, 0)
        self.assertIn(f"PLAN original id=r1 key=originals/{digest[:2]}/{digest}.pdf", output)
        self.assertEqual(events, [("rows", "originals", 0)])

    async def test_generated_base_and_rendition_upload_before_matching_key_update(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.pdf"
            rendition = Path(directory) / "rendition.pdf"
            base.write_bytes(b"base")
            rendition.write_bytes(b"rendition")

            async def update(row, key):
                events.append(("update", row["kind"], row["id"], key))
                return True

            rc, _output = await _run(
                [
                    {"kind": "base", "id": "doc-1", "path": str(base)},
                    {"kind": "rendition", "id": "ren-1", "report_id": "doc-1", "path": str(rendition)},
                ],
                _args("generated"), events, update,
            )
        self.assertEqual(rc, 0)
        uploads = [event for event in events if event[0] == "upload"]
        updates = [event for event in events if event[0] == "update"]
        expected_keys = {
            "generated/doc-1/base-cae662172fd4.pdf",
            "generated/doc-1/renditions/ren-1-0e4d51ac3800.pdf",
        }
        self.assertEqual({event[2] for event in uploads}, expected_keys)
        self.assertEqual({event[1] for event in updates}, {"base", "rendition"})
        for update_event in updates:
            upload_index = next(
                index for index, event in enumerate(events)
                if event[0] == "upload" and event[2] == update_event[3]
            )
            self.assertLess(upload_index, events.index(update_event))

    async def test_hash_mismatch_and_missing_file_never_mutate(self):
        events = []
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"wrong")
            source.flush()

            async def update(*_args):
                events.append(("update",))
                return True

            rc, output = await _run(
                [
                    {"kind": "original", "id": "bad", "hash": "a" * 64, "name": "bad.pdf", "path": source.name},
                    {"kind": "base", "id": "missing", "path": "/does/not/exist.pdf"},
                ],
                _args(), events, update,
            )
        self.assertEqual(rc, 0)
        self.assertIn("HASH_MISMATCH expected=", output)
        self.assertIn("original id=bad", output)
        self.assertIn("MISSING_LOCAL base id=missing", output)
        self.assertEqual(events, [("rows", "all", 0)])

    async def test_db_failure_after_upload_reports_safe_orphan(self):
        events = []
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"base")
            source.flush()

            async def update(*_args):
                raise RuntimeError("db unavailable")

            rc, output = await _run(
                [{"kind": "base", "id": "doc-1", "path": source.name}], _args("generated"), events, update
            )
        self.assertEqual(rc, 1)
        self.assertEqual(events[1][0], "upload")
        self.assertIn("ORPHAN generated/doc-1/base-cae662172fd4.pdf", output)

    async def test_rerun_with_no_null_key_rows_is_idempotent_skip(self):
        events = []

        async def update(*_args):
            raise AssertionError("must not update an already-migrated row")

        rc, output = await _run([], _args(), events, update)
        self.assertEqual(rc, 0)
        self.assertIn("migrated=0", output)
        self.assertEqual(events, [("rows", "all", 0)])

    async def test_rows_limit_is_total_and_queries_only_unmigrated_keys(self):
        calls = []

        class _Result:
            def __iter__(self):
                return iter([("r1", "a" * 64, "a.pdf", "/a.pdf")])

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def execute(self, stmt, params):
                calls.append((str(stmt), params))
                return _Result()

        with patch.object(migrate, "SessionFactory", lambda: _Session()):
            rows = await migrate._rows("all", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("source_object_key IS NULL", calls[0][0])
        self.assertEqual(calls[0][1], {"limit": 1})

    async def test_update_targets_only_the_matching_nullable_key_column(self):
        calls = []

        class _Result:
            rowcount = 1

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def execute(self, stmt, params):
                calls.append((str(stmt), params))
                return _Result()

            async def commit(self):
                return None

        rows = [
            {"kind": "original", "id": "original-1"},
            {"kind": "base", "id": "doc-1"},
            {"kind": "rendition", "id": "ren-1"},
        ]
        with patch.object(migrate, "SessionFactory", lambda: _Session()):
            for row in rows:
                self.assertTrue(await migrate._update_key(row, "generated/test.pdf"))
        self.assertIn("source_object_key", calls[0][0])
        self.assertIn("research.report_doc SET pdf_object_key", calls[1][0])
        self.assertIn("research.report_rendition SET pdf_object_key", calls[2][0])
        self.assertTrue(all("IS NULL" in statement for statement, _params in calls))


if __name__ == "__main__":
    unittest.main()
