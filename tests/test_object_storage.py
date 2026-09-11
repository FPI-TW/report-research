"""R2 storage seam: deterministic keys, fail-closed config, and temporary parser files."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import config
from app.services import object_storage as storage_module


class ObjectStorageConfigTests(unittest.TestCase):
    def test_default_mode_is_local(self):
        with patch.dict(os.environ, {"OBJECT_STORAGE_MODE": "local"}, clear=False):
            self.assertEqual(config._load().object_storage_mode, "local")

    def test_r2_without_credentials_fails_closed(self):
        with patch.dict(
            os.environ,
            {"OBJECT_STORAGE_MODE": "r2"},
            clear=False,
        ), patch.dict(
            os.environ,
            {key: "" for key in ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "R2_ENDPOINT_URL"):
                config._load()

    def test_presign_ttl_rejects_values_over_one_hour(self):
        with patch.dict(os.environ, {"R2_PRESIGN_TTL_SECONDS": "3601"}, clear=False):
            with self.assertRaisesRegex(ValueError, "1..3600"):
                config._load()


class ObjectKeyTests(unittest.TestCase):
    def test_canonical_keys(self):
        digest = "a" * 64
        self.assertEqual(storage_module.original_object_key(digest, "source.PDF"), f"originals/aa/{digest}.pdf")
        self.assertEqual(
            storage_module.generated_object_key("doc-1", b"pdf"),
            "generated/doc-1/base-c35b21d6ca39.pdf",
        )
        self.assertEqual(
            storage_module.generated_object_key("doc-1", b"pdf", "ren-1"),
            "generated/doc-1/renditions/ren-1-c35b21d6ca39.pdf",
        )

    def test_downloaded_file_is_removed_after_parser_scope(self):
        storage = storage_module.ObjectStorage()
        storage.settings = SimpleNamespace(r2_bucket="bucket")

        def fake_call(method, **kwargs):
            self.assertEqual(method, "download_file")
            Path(kwargs["Filename"]).write_bytes(b"temporary pdf")

        storage._call = fake_call
        with storage.downloaded_file("originals/aa/example.pdf", "example.pdf") as path:
            parent = path.parent
            self.assertTrue(path.is_file())
        self.assertFalse(parent.exists())

    def test_no_such_bucket_is_not_confirmed_object_missing(self):
        class _ClientError(Exception):
            response = {"Error": {"Code": "NoSuchBucket"}}

        class _Client:
            def head_object(self, **kwargs):
                raise _ClientError("bucket missing")

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with self.assertRaises(storage_module.ObjectStorageError) as caught:
            storage.head_object("originals/aa/a.pdf")
        self.assertNotIsInstance(caught.exception, storage_module.ObjectNotFound)

    def test_no_such_key_is_confirmed_missing(self):
        class _ClientError(Exception):
            response = {"Error": {"Code": "NoSuchKey"}}

        class _Client:
            def head_object(self, **kwargs):
                raise _ClientError("key missing")

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with self.assertRaises(storage_module.ObjectNotFound):
            storage.head_object("originals/aa/a.pdf")

    def test_generated_upload_stores_full_sha_metadata_without_changing_key_contract(self):
        captured = {}

        class _Client:
            def put_object(self, **kwargs):
                captured.update(kwargs)

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        key = storage_module.generated_object_key("doc", b"pdf")
        storage.upload_bytes(b"pdf", key)
        self.assertEqual(key, "generated/doc/base-c35b21d6ca39.pdf")
        self.assertEqual(
            captured["Metadata"]["sha256"], "c35b21d6ca39aa7cc3b79a705d989f1a6e88b99ab43988d74048799e3db926a3"
        )
        self.assertEqual(captured["IfNoneMatch"], "*")

    def test_original_upload_file_stores_full_sha_metadata(self):
        captured = {}

        class _Client:
            def put_object(self, **kwargs):
                captured.update(kwargs)

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"original PDF")
            source.flush()
            storage.upload_file(
                source.name, "originals/aa/example.pdf",
                expected_sha256="0457795fb83ad7301b316c4973f24b3069c8aa9fae7bdc8003bbad61afd72541",
            )
        self.assertEqual(
            captured["Metadata"]["sha256"],
            "0457795fb83ad7301b316c4973f24b3069c8aa9fae7bdc8003bbad61afd72541",
        )
        self.assertEqual(captured["IfNoneMatch"], "*")

    def test_upload_file_rejects_replaced_source_before_any_remote_write(self):
        captured = []

        class _Client:
            def put_object(self, **kwargs):
                captured.append(kwargs)

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(b"verified before caller upload")
            source.flush()
            expected = hashlib.sha256(Path(source.name).read_bytes()).hexdigest()
            source.seek(0)
            source.truncate(0)
            source.write(b"replacement after caller hash")
            source.flush()
            with self.assertRaises(storage_module.ObjectStorageError):
                storage.upload_file(source.name, "originals/aa/example.pdf", expected_sha256=expected)
        self.assertEqual(captured, [])

    def test_create_only_existing_object_with_same_full_sha_is_idempotent(self):
        data = b"same generated PDF"
        digest = hashlib.sha256(data).hexdigest()
        calls = []

        class _PreconditionFailed(Exception):
            response = {"Error": {"Code": "PreconditionFailed"}}

        class _Client:
            def put_object(self, **kwargs):
                calls.append(("put", kwargs))
                raise _PreconditionFailed("already exists")

            def head_object(self, **kwargs):
                calls.append(("head", kwargs))
                return {"Metadata": {"sha256": digest}}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        storage.upload_bytes(data, "generated/doc/base-aaaaaaaaaaaa.pdf")
        self.assertEqual([call[0] for call in calls], ["put", "head"])
        self.assertEqual(calls[0][1]["IfNoneMatch"], "*")

    def test_create_only_rejects_same_key_with_different_full_sha(self):
        """A 12-character generated-key collision must never replace the first full digest."""
        original = b"first PDF bytes"
        replacement = b"second PDF bytes"
        stored_digest = hashlib.sha256(original).hexdigest()
        attempted_writes = []

        class _PreconditionFailed(Exception):
            response = {"Error": {"Code": "412"}}

        class _Client:
            def put_object(self, **kwargs):
                attempted_writes.append(kwargs)
                raise _PreconditionFailed("already exists")

            def head_object(self, **kwargs):
                return {"Metadata": {"sha256": stored_digest}}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        # The common key represents the otherwise astronomically unlikely equal-12-prefix case.
        with self.assertRaises(storage_module.ObjectStorageError):
            storage.upload_bytes(replacement, "generated/doc/base-aaaaaaaaaaaa.pdf")
        self.assertEqual(len(attempted_writes), 1)
        self.assertEqual(attempted_writes[0]["IfNoneMatch"], "*")

    def test_create_only_rejects_existing_object_without_full_sha_metadata(self):
        class _PreconditionFailed(Exception):
            response = {"Error": {"Code": "PreconditionFailed"}}

        class _Client:
            def put_object(self, **_kwargs):
                raise _PreconditionFailed("already exists")

            def head_object(self, **_kwargs):
                return {"Metadata": {}}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with self.assertRaises(storage_module.ObjectStorageError):
            storage.upload_bytes(b"pdf", "generated/doc/base-aaaaaaaaaaaa.pdf")

    def test_concurrent_create_only_uploads_have_one_winner_and_never_overwrite(self):
        key = "generated/doc/base-aaaaaaaaaaaa.pdf"
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        objects: dict[str, tuple[bytes, str]] = {}
        successful_puts = 0

        class _PreconditionFailed(Exception):
            response = {"Error": {"Code": "PreconditionFailed"}}

        class _Client:
            def put_object(self, **kwargs):
                nonlocal successful_puts
                barrier.wait()
                with lock:
                    if kwargs["Key"] in objects:
                        raise _PreconditionFailed("already exists")
                    body = kwargs["Body"]
                    payload = body.read() if hasattr(body, "read") else body
                    objects[kwargs["Key"]] = (payload, kwargs["Metadata"]["sha256"])
                    successful_puts += 1

            def head_object(self, **kwargs):
                with lock:
                    _payload, digest = objects[kwargs["Key"]]
                return {"Metadata": {"sha256": digest}}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(storage.upload_bytes, data, key)
                for data in (b"winner", b"loser")
            ]
            outcomes = [future.exception() for future in futures]
        self.assertEqual(successful_puts, 1)
        self.assertEqual(sum(error is None for error in outcomes), 1)
        self.assertIsInstance(next(error for error in outcomes if error is not None), storage_module.ObjectStorageError)
        self.assertIn(objects[key][0], {b"winner", b"loser"})

    def test_create_only_put_service_error_is_not_treated_as_idempotent(self):
        class _Client:
            def put_object(self, **_kwargs):
                raise RuntimeError("network down")

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with self.assertRaises(storage_module.ObjectStorageError):
            storage.upload_bytes(b"pdf", "generated/doc/base-aaaaaaaaaaaa.pdf")

    def test_presign_uses_configured_bounded_ttl(self):
        captured = {}

        class _Client:
            def generate_presigned_url(self, *args, **kwargs):
                captured["args"] = args
                captured.update(kwargs)
                return "https://private.example.test/presigned"

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket", r2_presign_ttl_seconds=3600)
        self.assertEqual(storage.presign_get("generated/doc/base.pdf"), "https://private.example.test/presigned")
        self.assertEqual(captured["ExpiresIn"], 3600)

    def test_concurrent_first_client_call_builds_once(self):
        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage.settings = SimpleNamespace()
        calls = 0
        calls_lock = threading.Lock()
        client = object()

        def factory():
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.03)
            return client

        storage._build_client = factory
        with ThreadPoolExecutor(max_workers=8) as executor:
            clients = list(executor.map(lambda _unused: storage._get_client(), range(8)))
        self.assertEqual(calls, 1)
        self.assertTrue(all(value is client for value in clients))

    def test_concurrent_get_object_storage_constructs_one_shared_singleton(self):
        original = storage_module._STORAGE
        storage_module._STORAGE = None
        calls = 0
        calls_lock = threading.Lock()
        barrier = threading.Barrier(8)
        singleton = object()

        def factory():
            nonlocal calls
            with calls_lock:
                calls += 1
            # Keep the constructor active long enough for every barrier-released caller
            # to race the module-level check/assignment.
            time.sleep(0.03)
            return singleton

        try:
            with patch.object(storage_module, "ObjectStorage", side_effect=factory):
                def get_after_barrier(_unused):
                    barrier.wait()
                    return storage_module.get_object_storage()

                with ThreadPoolExecutor(max_workers=8) as executor:
                    values = list(executor.map(get_after_barrier, range(8)))
        finally:
            storage_module._STORAGE = original
        self.assertEqual(calls, 1)
        self.assertTrue(all(value is singleton for value in values))

    def test_download_bytes_closes_streaming_body_on_success(self):
        class _Body:
            closed = False

            def read(self):
                return b"PDF"

            def close(self):
                self.closed = True

        body = _Body()

        class _Client:
            def get_object(self, **_kwargs):
                return {"Body": body}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        self.assertEqual(storage.download_bytes("generated/doc/base.pdf"), b"PDF")
        self.assertTrue(body.closed)

    def test_download_bytes_closes_streaming_body_on_read_failure(self):
        class _Body:
            closed = False

            def read(self):
                raise RuntimeError("stream failed")

            def close(self):
                self.closed = True

        body = _Body()

        class _Client:
            def get_object(self, **_kwargs):
                return {"Body": body}

        storage = storage_module.ObjectStorage()
        storage.mode = "r2"
        storage._client = _Client()
        storage.settings = SimpleNamespace(r2_bucket="bucket")
        with self.assertRaises(storage_module.ObjectStorageError):
            storage.download_bytes("generated/doc/base.pdf")
        self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
