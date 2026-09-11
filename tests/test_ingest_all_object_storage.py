"""The bulk-ingest path must not upload replaced source bytes under an old hash."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ingest_all", ROOT / "scripts" / "ingest_all.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ingest_all"] = module
    spec.loader.exec_module(module)
    return module


ingest_all = _load()


class IngestAllObjectStorageTests(unittest.TestCase):
    def test_changed_source_is_rejected_before_r2_upload_or_db_upsert(self):
        expected_hash = hashlib.sha256(b"bytes used by extraction").hexdigest()
        calls: list[str] = []

        class _Storage:
            enabled = True

            def upload_file(self, *_args):
                calls.append("upload")

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def execute(self, *_args):
                return []

            async def commit(self):
                calls.append("commit")

            async def rollback(self):
                calls.append("rollback")

        async def _upsert(*_args):
            calls.append("upsert")

        tag = SimpleNamespace(
            market="TW", is_research=True, confidence=0.9, instrument_types=[], relates_stock=False,
            relates_futures=False, stock_targets=[], futures_targets=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            source.write_bytes(b"replacement bytes after extraction")
            record = {
                "file_hash": expected_hash,
                "file_name": source.name,
                "file_path": str(source),
                "is_admin": False,
                "scanned": False,
                "text": "research text",
            }
            with patch.object(ingest_all, "SessionFactory", lambda: _Session()), \
                 patch.object(ingest_all, "get_object_storage", return_value=_Storage()), \
                 patch.object(ingest_all.cache, "iter_records", return_value=iter([record])), \
                 patch.object(ingest_all, "load_tag", return_value=tag), \
                 patch.object(ingest_all, "chunk_text", return_value=["chunk"]), \
                 patch.object(ingest_all, "embed_texts", return_value=[[0.1]]), \
                 patch.object(ingest_all, "upsert_report", _upsert), \
                 patch.object(ingest_all, "FAIL_LOG", Path(directory) / "failures.log"):
                asyncio.run(ingest_all.main(None, 1))

        self.assertNotIn("upload", calls)
        self.assertNotIn("upsert", calls)
        self.assertEqual(calls, ["rollback"])

    def test_sync_path_rechecks_hash_before_upload_call(self):
        source = (ROOT / "scripts" / "sync_new_reports.py").read_text(encoding="utf-8")
        check = source.index("if file_sha256(path) != res.file_hash")
        upload = source.index("storage.upload_file, path, source_object_key, expected_sha256=res.file_hash")
        self.assertLess(check, upload)


if __name__ == "__main__":
    unittest.main()
