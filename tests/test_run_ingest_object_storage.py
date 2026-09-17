"""The Makefile ingest path uploads a verified source object before its DB upsert."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("run_ingest", ROOT / "scripts" / "run_ingest.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_ingest"] = module
    spec.loader.exec_module(module)
    return module


run_ingest = _load()


class RunIngestObjectStorageTests(unittest.TestCase):
    def test_upload_precedes_upsert_and_persists_canonical_key(self):
        source = b"source PDF"
        digest = hashlib.sha256(source).hexdigest()
        events = []

        class _Storage:
            enabled = True

            def upload_file(self, path, key, *, expected_sha256):
                events.append(("upload", Path(path).read_bytes(), key))

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *args):
                events.append(("sql",))

            async def commit(self):
                events.append(("commit",))

        async def _not_exists(*args):
            return False

        async def _upsert(_session, report, chunks, embeddings):
            events.append(("upsert", report.source_object_key, report.file_hash))

        async def _relax(_session):
            return None

        tag = SimpleNamespace(
            market="TW", is_research=True, confidence=0.9, instrument_types=[], relates_stock=False,
            relates_futures=False, stock_targets=[], futures_targets=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source)
            records = Path(directory) / "sample.jsonl"
            records.write_text(json.dumps({
                "file_name": "source.pdf", "file_hash": digest, "file_path": str(source_path),
                "is_admin": False, "scanned": False, "text": "report text",
            }) + "\n", encoding="utf-8")
            with patch.object(run_ingest, "EXTRACTED", records), \
                 patch.object(run_ingest, "SessionFactory", lambda: _Session()), \
                 patch.object(run_ingest, "get_object_storage", return_value=_Storage()), \
                 patch.object(run_ingest, "load_tag", return_value=tag), \
                 patch.object(run_ingest, "report_exists", _not_exists), \
                 patch.object(run_ingest, "chunk_text", return_value=["chunk"]), \
                 patch.object(run_ingest, "embed_texts", return_value=[[0.1]]), \
                 patch.object(run_ingest, "upsert_report", _upsert), \
                 patch.object(run_ingest, "relax_statement_timeout", _relax):
                asyncio.run(run_ingest.main(False))

        key = f"originals/{digest[:2]}/{digest}.pdf"
        self.assertEqual(events[0], ("upload", source, key))
        self.assertEqual(events[1], ("upsert", key, digest))

    def test_local_force_retains_only_verified_existing_canonical_object_key(self):
        source = b"source PDF"
        digest = hashlib.sha256(source).hexdigest()
        canonical = f"originals/{digest[:2]}/{digest}.pdf"

        def run(existing_key):
            captured = []

            class _Result:
                def first(self):
                    return ("source.pdf", existing_key) if existing_key else None

            class _Session:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def execute(self, *_args, **_kwargs):
                    return _Result()

                async def commit(self):
                    return None

            class _Storage:
                enabled = False

            async def upsert(_session, report, _chunks, _embeddings):
                captured.append(report.source_object_key)

            async def relax(_session):
                return None

            tag = SimpleNamespace(
                market="TW", is_research=True, confidence=0.9, instrument_types=[], relates_stock=False,
                relates_futures=False, stock_targets=[], futures_targets=[],
            )
            with tempfile.TemporaryDirectory() as directory:
                source_path = Path(directory) / "source.pdf"
                source_path.write_bytes(source)
                records = Path(directory) / "sample.jsonl"
                records.write_text(json.dumps({
                    "file_name": "source.pdf", "file_hash": digest, "file_path": str(source_path),
                    "is_admin": False, "scanned": False, "text": "report text",
                }) + "\n", encoding="utf-8")
                with patch.object(run_ingest, "EXTRACTED", records), \
                     patch.object(run_ingest, "SessionFactory", lambda: _Session()), \
                     patch.object(run_ingest, "get_object_storage", return_value=_Storage()), \
                     patch.object(run_ingest, "load_tag", return_value=tag), \
                     patch.object(run_ingest, "chunk_text", return_value=["chunk"]), \
                     patch.object(run_ingest, "embed_texts", return_value=[[0.1]]), \
                     patch.object(run_ingest, "upsert_report", upsert), \
                     patch.object(run_ingest, "relax_statement_timeout", relax):
                    asyncio.run(run_ingest.main(True))
            return captured

        self.assertEqual(run(canonical), [canonical])
        self.assertEqual(run("originals/ff/corrupt.pdf"), [None])


if __name__ == "__main__":
    unittest.main()
