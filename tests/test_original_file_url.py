"""`app/services/original_file_url.mint_original_url`：站內原檔路由與對外 API 共用的 presign 規則。

站內路由的 HTTP 行為由 `tests/test_report_file_api.py` 釘住；這裡直接驗共用函式的三種例外、
檢查順序（指標錯誤在任何遠端操作之前）、`verify_object=False` 不打 HEAD，以及 `ttl_seconds`
只在明確給值時才傳給 `presign_get`。
"""

from __future__ import annotations

import asyncio
import unittest

from app.services.object_storage import ObjectNotFound, ObjectStorageError
from app.services.original_file_url import (
    OriginalIntegrityError,
    OriginalNotFound,
    OriginalStorageUnavailable,
    mint_original_url,
)

DIGEST = "a" * 64
KEY = f"originals/aa/{DIGEST}.pdf"


class _Storage:
    enabled = True
    mode = "r2"

    def __init__(self, *, head=None, head_exc=None, presign_exc=None):
        self.head = head if head is not None else {"Metadata": {"sha256": DIGEST}}
        self.head_exc = head_exc
        self.presign_exc = presign_exc
        self.calls: list[tuple] = []

    def head_object(self, key):
        self.calls.append(("head", key))
        if self.head_exc is not None:
            raise self.head_exc
        return self.head

    def presign_get(self, key, **kwargs):
        self.calls.append(("presign", key, kwargs))
        if self.presign_exc is not None:
            raise self.presign_exc
        return "https://signed.example.test/x"


def _mint(storage, **kw):
    params = {"file_name": "a.pdf", "object_key": KEY, "file_hash": DIGEST, "storage": storage, **kw}
    return asyncio.run(mint_original_url(**params))


class MintOriginalUrlTests(unittest.TestCase):
    def test_verified_presign_uses_default_ttl_and_inline_pdf(self):
        storage = _Storage()
        self.assertEqual(_mint(storage), "https://signed.example.test/x")
        self.assertEqual(storage.calls, [
            ("head", KEY),
            ("presign", KEY, {"filename": "a.pdf", "inline": True}),
        ])

    def test_explicit_ttl_is_passed_and_docx_is_attachment(self):
        storage = _Storage()
        key = f"originals/aa/{DIGEST}.docx"
        _mint(storage, file_name="memo.docx", object_key=key, ttl_seconds=600, verify_object=False)
        self.assertEqual(storage.calls, [
            ("presign", key, {"filename": "memo.docx", "inline": False, "ttl_seconds": 600}),
        ])

    def test_wrong_pointer_fails_before_any_remote_call(self):
        cases = {
            "other key": {"object_key": "originals/bb/other.pdf"},
            "bad hash": {"file_hash": "A" * 64, "object_key": f"originals/AA/{'A' * 64}.pdf"},
            "short hash": {"file_hash": "ab", "object_key": "originals/ab/ab.pdf"},
            "no suffix": {"file_name": "noext", "object_key": f"originals/aa/{DIGEST}"},
            "none name": {"file_name": None},
        }
        for name, kw in cases.items():
            for verify in (True, False):
                with self.subTest(name=name, verify=verify):
                    storage = _Storage()
                    with self.assertRaises(OriginalIntegrityError):
                        _mint(storage, verify_object=verify, **kw)
                    self.assertEqual(storage.calls, [])

    def test_metadata_mismatch_is_integrity_error_without_presign(self):
        for head in ({}, {"Metadata": {}}, {"Metadata": {"sha256": "b" * 64}}, {"Metadata": "x"}):
            with self.subTest(head=head):
                storage = _Storage(head=head)
                with self.assertRaises(OriginalIntegrityError):
                    _mint(storage)
                self.assertEqual([c[0] for c in storage.calls], ["head"])

    def test_missing_object_and_storage_failure_are_distinct(self):
        with self.assertRaises(OriginalNotFound):
            _mint(_Storage(head_exc=ObjectNotFound(KEY)))
        with self.assertRaises(OriginalStorageUnavailable):
            _mint(_Storage(head_exc=ObjectStorageError("NoSuchBucket")))
        with self.assertRaises(OriginalStorageUnavailable):
            _mint(_Storage(presign_exc=ObjectStorageError("presign failed")), verify_object=False)

    def test_local_mode_or_missing_key_never_mints(self):
        local = _Storage()
        local.enabled = False
        with self.assertRaises(OriginalNotFound):
            _mint(local)
        storage = _Storage()
        with self.assertRaises(OriginalNotFound):
            _mint(storage, object_key=None)
        self.assertEqual(local.calls + storage.calls, [])


if __name__ == "__main__":
    unittest.main()
