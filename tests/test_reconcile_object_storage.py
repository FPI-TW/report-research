"""Mocked reconciliation tests: every result is report-only and never mutates R2/DB."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.object_storage import ObjectNotFound

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "reconcile_object_storage", ROOT / "scripts" / "reconcile_object_storage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_object_storage"] = module
    spec.loader.exec_module(module)
    return module


reconcile = _load()


class _Storage:
    enabled = True

    def __init__(self, heads: dict[str, int | dict | Exception], hashes: dict[str, str], remote_keys: set[str]):
        self.heads = heads
        self.hashes = hashes
        self.remote_keys = remote_keys

    def head_object(self, key):
        value = self.heads[key]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, dict) else {"ContentLength": value}

    def sha256(self, key):
        return self.hashes[key]

    def list_keys(self, prefix):
        return sorted(key for key in self.remote_keys if key.startswith(prefix))


def _args(kind="all", limit=0):
    return argparse.Namespace(dry_run=True, kind=kind, limit=limit, concurrency=2)


async def _run_with(rows, storage, kind="all", limit=0):
    async def fake_rows(_kind, _limit):
        return rows

    output = io.StringIO()
    with patch.object(reconcile, "_rows", fake_rows), \
         patch.object(reconcile, "get_object_storage", return_value=storage), \
         contextlib.redirect_stdout(output):
        rc = await reconcile.run(_args(kind, limit))
    return rc, output.getvalue()


class ReconcileObjectStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_missing_original_and_remote_orphan(self):
        digest = hashlib.sha256(b"original").hexdigest()
        key = f"originals/{digest[:2]}/{digest}.pdf"
        orphan = "originals/ff/orphan.pdf"
        storage = _Storage({key: ObjectNotFound(key)}, {}, {key, orphan})
        rc, output = await _run_with(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": None, "key": key}],
            storage,
            "originals",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"MISSING {key}", output)
        self.assertIn(f"ORPHAN {orphan}", output)

    async def test_reports_remote_size_mismatch_for_original(self):
        data = b"pdf"
        digest = hashlib.sha256(data).hexdigest()
        key = f"originals/{digest[:2]}/{digest}.pdf"
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(data)
            local.flush()
            storage = _Storage({key: len(data) + 7}, {key: digest}, {key})
            rc, output = await _run_with(
                [
                    {
                        "kind": "original", "id": "r1", "hash": digest, "name": "source.pdf",
                        "path": local.name, "key": key,
                    },
                ],
                storage, "originals",
            )
        self.assertEqual(rc, 0)
        self.assertIn(f"SIZE_MISMATCH {key} local=3 remote=10", output)

    async def test_reports_legacy_original_without_sha_metadata(self):
        digest = hashlib.sha256(b"original").hexdigest()
        key = f"originals/{digest[:2]}/{digest}.pdf"
        storage = _Storage({key: 9}, {key: digest}, {key})
        rc, output = await _run_with(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": None, "key": key}],
            storage, "originals",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_METADATA_MISSING {key}", output)

    async def test_reports_metadata_sha_mismatch_against_remote_content(self):
        digest = hashlib.sha256(b"original").hexdigest()
        other = hashlib.sha256(b"other").hexdigest()
        key = f"originals/{digest[:2]}/{digest}.pdf"
        storage = _Storage({key: {"ContentLength": 8, "Metadata": {"sha256": other}}}, {key: digest}, {key})
        rc, output = await _run_with(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": None, "key": key}],
            storage, "originals",
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_MISMATCH {key} metadata={other} remote={digest}", output)

    async def test_reports_remote_sha_mismatch_for_original(self):
        original_digest = hashlib.sha256(b"original").hexdigest()
        original_key = f"originals/{original_digest[:2]}/{original_digest}.pdf"
        wrong = hashlib.sha256(b"wrong").hexdigest()
        storage = _Storage({original_key: 5}, {original_key: wrong}, {original_key})
        rc, output = await _run_with(
            [
                {
                    "kind": "original", "id": "r1", "hash": original_digest,
                    "name": "source.pdf", "path": None, "key": original_key,
                },
            ],
            storage,
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_MISMATCH {original_key} expected={original_digest}", output)

    async def test_orphan_scan_ignores_legacy_generated_prefix_and_respects_limit(self):
        original = f"originals/aa/{'a' * 64}.pdf"
        generated = "generated/doc/base-aaaaaaaaaaaa.pdf"
        storage = _Storage({original: 1}, {original: "a" * 64}, {original, generated})
        row = {"kind": "original", "id": "r1", "hash": "a" * 64, "name": "known.pdf", "path": None, "key": original}
        _rc, output = await _run_with([row], storage, "all")
        self.assertNotIn(f"ORPHAN {generated}", output)
        _rc, output = await _run_with([row], storage, "originals", limit=1)
        self.assertIn("ORPHAN_SCAN_SKIPPED", output)
        self.assertNotIn("ORPHAN", output.replace("ORPHAN_SCAN_SKIPPED", ""))

    async def test_unkeyed_original_is_nonclean_even_when_canonical_remote_exists(self):
        digest = hashlib.sha256(b"original").hexdigest()
        canonical = f"originals/{digest[:2]}/{digest}.pdf"
        storage = _Storage({canonical: 8}, {canonical: digest}, {canonical})
        rc, output = await _run_with(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": None, "key": None}],
            storage,
            "originals",
        )
        self.assertEqual(rc, 1)
        self.assertIn("UNKEYED original id=r1", output)
        self.assertIn(f"ORPHAN {canonical}", output)

    async def test_reports_original_key_mismatch_and_keeps_bad_remote_key_as_orphan(self):
        digest = hashlib.sha256(b"original").hexdigest()
        canonical = f"originals/{digest[:2]}/{digest}.pdf"
        wrong = f"originals/ff/{digest}.pdf"
        storage = _Storage({wrong: {"ContentLength": 8}}, {wrong: digest}, {wrong})
        rc, output = await _run_with(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "source.pdf", "path": None, "key": wrong}],
            storage,
            "originals",
        )
        self.assertEqual(rc, 1)
        self.assertIn(f"KEY_MISMATCH {wrong} expected={canonical}", output)
        self.assertIn(f"ORPHAN {wrong}", output)

    async def test_rows_limit_applies_to_originals(self):
        calls = []

        class _Result:
            def __iter__(self):
                return iter([("r1", "a" * 64, "a.pdf", "/a", "originals/aa/a.pdf")])

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, stmt, params):
                calls.append((str(stmt), params))
                return _Result()

        with patch.object(reconcile, "SessionFactory", lambda: _Session()):
            rows = await reconcile._rows("all", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {"limit": 1})


if __name__ == "__main__":
    unittest.main()
