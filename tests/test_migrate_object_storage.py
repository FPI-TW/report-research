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
                    {
                        "kind": "original", "id": "missing", "hash": "b" * 64, "name": "missing.pdf",
                        "path": "/does/not/exist.pdf",
                    },
                ],
                _args(), events, update,
            )
        self.assertEqual(rc, 0)
        self.assertIn("HASH_MISMATCH expected=", output)
        self.assertIn("original id=bad", output)
        self.assertIn("MISSING_LOCAL original id=missing", output)
        self.assertEqual(events, [("rows", "all", 0)])

    async def test_db_failure_after_upload_reports_safe_orphan(self):
        events = []
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"original")
            source.flush()
            digest = hashlib.sha256(b"original").hexdigest()

            async def update(*_args):
                raise RuntimeError("db unavailable")

            rc, output = await _run(
                [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": source.name}],
                _args("originals"), events, update,
            )
        self.assertEqual(rc, 1)
        self.assertEqual(events[1][0], "upload")
        self.assertIn(f"ORPHAN originals/{digest[:2]}/{digest}.pdf", output)

    async def test_rerun_with_no_null_key_rows_is_idempotent_skip(self):
        events = []

        async def update(*_args):
            raise AssertionError("must not update an already-migrated row")

        rc, output = await _run([], _args(), events, update)
        self.assertEqual(rc, 0)
        self.assertIn("migrated=0", output)
        self.assertEqual(events, [("rows", "all", 0)])

    async def test_rows_limit_applies_and_queries_only_unmigrated_keys(self):
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

    async def test_update_targets_only_the_nullable_source_key_column(self):
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

        with patch.object(migrate, "SessionFactory", lambda: _Session()):
            self.assertTrue(await migrate._update_key({"kind": "original", "id": "original-1"}, "originals/aa/a.pdf"))
        self.assertEqual(len(calls), 1)
        self.assertIn("research.research_report SET source_object_key", calls[0][0])
        self.assertIn("IS NULL", calls[0][0])


if __name__ == "__main__":
    unittest.main()
