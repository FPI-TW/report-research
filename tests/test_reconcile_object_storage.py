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

    async def test_reports_remote_size_mismatch_for_generated_pdf(self):
        data = b"pdf"
        digest = hashlib.sha256(data).hexdigest()
        key = f"generated/doc-1/base-{digest[:12]}.pdf"
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(data)
            local.flush()
            storage = _Storage({key: len(data) + 7}, {key: digest}, {key})
            rc, output = await _run_with(
                [{"kind": "base", "id": "doc-1", "path": local.name, "key": key}], storage, "generated"
            )
        self.assertEqual(rc, 0)
        self.assertIn(f"SIZE_MISMATCH {key} local=3 remote=10", output)

    async def test_reports_remote_sha_mismatch_for_original_and_rendition(self):
        original_digest = hashlib.sha256(b"original").hexdigest()
        original_key = f"originals/{original_digest[:2]}/{original_digest}.pdf"
        generated_digest = hashlib.sha256(b"generated").hexdigest()
        rendition_key = f"generated/doc-1/renditions/ren-1-{generated_digest[:12]}.pdf"
        wrong = hashlib.sha256(b"wrong").hexdigest()
        storage = _Storage(
            {original_key: 5, rendition_key: 5},
            {original_key: wrong, rendition_key: wrong},
            {original_key, rendition_key},
        )
        rc, output = await _run_with(
            [
                {
                    "kind": "original", "id": "r1", "hash": original_digest,
                    "name": "source.pdf", "path": None, "key": original_key,
                },
                {"kind": "rendition", "id": "ren-1", "report_id": "doc-1", "path": None, "key": rendition_key},
            ],
            storage,
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_MISMATCH {original_key} expected={original_digest}", output)
        self.assertIn(f"SHA_MISMATCH {rendition_key} expected={generated_digest[:12]}", output)

    async def test_reports_full_generated_digest_mismatch_despite_matching_key_prefix(self):
        canonical = hashlib.sha256(b"canonical generated PDF").hexdigest()
        # The first twelve characters are intentionally the same as the canonical key
        # suffix.  A prefix-only comparison would incorrectly accept this object.
        colliding_remote = canonical[:12] + ("f" * 52)
        key = f"generated/doc-1/base-{canonical[:12]}.pdf"
        storage = _Storage(
            {key: {"ContentLength": 3, "Metadata": {"sha256": canonical}}},
            {key: colliding_remote},
            {key},
        )
        rc, output = await _run_with(
            [{"kind": "base", "id": "doc-1", "path": None, "key": key}], storage, "generated"
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_MISMATCH {key} metadata={canonical} remote={colliding_remote}", output)

    async def test_reports_legacy_generated_object_without_full_sha_metadata(self):
        digest = hashlib.sha256(b"generated").hexdigest()
        key = f"generated/doc-1/base-{digest[:12]}.pdf"
        storage = _Storage({key: 9}, {key: digest}, {key})
        rc, output = await _run_with(
            [{"kind": "base", "id": "doc-1", "path": None, "key": key}], storage, "generated"
        )
        self.assertEqual(rc, 0)
        self.assertIn(f"SHA_METADATA_MISSING {key}", output)

    async def test_orphan_scan_respects_kind_and_limit(self):
        original = f"originals/aa/{'a' * 64}.pdf"
        generated = "generated/doc/base-aaaaaaaaaaaa.pdf"
        storage = _Storage({original: 1}, {original: "a" * 64}, {original, generated})
        row = {"kind": "original", "id": "r1", "hash": "a" * 64, "name": "known.pdf", "path": None, "key": original}
        _rc, output = await _run_with([row], storage, "originals")
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

    async def test_unkeyed_generated_base_and_rendition_are_nonclean(self):
        storage = _Storage({}, {}, set())
        rc, output = await _run_with(
            [
                {"kind": "base", "id": "doc-1", "path": None, "key": None},
                {"kind": "rendition", "id": "ren-1", "report_id": "doc-1", "path": None, "key": None},
            ],
            storage,
            "generated",
        )
        self.assertEqual(rc, 1)
        self.assertIn("UNKEYED base id=doc-1", output)
        self.assertIn("UNKEYED rendition id=ren-1", output)

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

    async def test_reports_generated_owner_and_path_key_mismatches_without_head(self):
        digest = hashlib.sha256(b"generated").hexdigest()
        wrong_base_owner = f"generated/other/base-{digest[:12]}.pdf"
        wrong_rendition = f"generated/doc-1/renditions/other-{digest[:12]}.pdf"

        class _NoHeadStorage(_Storage):
            def head_object(self, key):
                raise AssertionError(f"bad DB pointer must not be HEADed: {key}")

        storage = _NoHeadStorage({}, {}, {wrong_base_owner, wrong_rendition})
        rc, output = await _run_with(
            [
                {"kind": "base", "id": "doc-1", "path": None, "key": wrong_base_owner},
                {
                    "kind": "rendition", "id": "ren-1", "report_id": "doc-1", "path": None,
                    "key": wrong_rendition,
                },
            ],
            storage,
            "generated",
        )
        self.assertEqual(rc, 1)
        self.assertIn(f"KEY_MISMATCH {wrong_base_owner}", output)
        self.assertIn(f"KEY_MISMATCH {wrong_rendition}", output)
        self.assertIn(f"ORPHAN {wrong_base_owner}", output)
        self.assertIn(f"ORPHAN {wrong_rendition}", output)

    async def test_reports_generated_metadata_key_mismatch_even_with_valid_owner_and_path(self):
        key_sha = hashlib.sha256(b"pdf belonging to doc").hexdigest()
        metadata_sha = hashlib.sha256(b"different PDF but valid remote object").hexdigest()
        key = f"generated/doc-1/base-{key_sha[:12]}.pdf"
        expected = f"generated/doc-1/base-{metadata_sha[:12]}.pdf"
        storage = _Storage(
            {key: {"ContentLength": 4, "Metadata": {"sha256": metadata_sha}}},
            {key: metadata_sha},
            {key},
        )
        rc, output = await _run_with(
            [{"kind": "base", "id": "doc-1", "path": None, "key": key}], storage, "generated"
        )
        self.assertEqual(rc, 1)
        self.assertIn(f"KEY_MISMATCH {key} expected={expected}", output)
        self.assertIn(f"ORPHAN {key}", output)

    async def test_rows_limit_is_total_across_all_kinds(self):
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
