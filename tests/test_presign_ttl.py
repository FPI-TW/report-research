# tests/test_presign_ttl.py
"""presign 可指定有效期（`ObjectStorage.presign_get(ttl_seconds=...)`）與 `EXTERNAL_FILE_URL_TTL_SECONDS`。

presign 用真的 boto3 搭配假憑證離線產生：SigV4 簽章完全在本機計算、不連網，所以斷言
落在實際 URL 的 `X-Amz-Expires`，而不是假 client 收到的參數——旋鈕接錯一層也抓得到。
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.services import object_storage as storage_module  # noqa: E402


def _r2_storage(r2_ttl: int = 3600) -> storage_module.ObjectStorage:
    storage = storage_module.ObjectStorage()
    storage.mode = "r2"
    storage.settings = SimpleNamespace(
        r2_endpoint_url="https://account.r2.example.invalid",
        r2_bucket="bucket",
        r2_access_key_id="fixed-test-secret-presignaccess",
        r2_secret_access_key="fixed-test-secret-presignsecret",
        r2_presign_ttl_seconds=r2_ttl,
    )
    return storage


def _expires(url: str) -> list[str]:
    return parse_qs(urlsplit(url).query)["X-Amz-Expires"]


class PresignTtlTests(unittest.TestCase):
    def test_explicit_ttl_reaches_the_signed_url(self):
        url = _r2_storage().presign_get("originals/ab/abc.pdf", filename="報告.pdf", ttl_seconds=600)
        self.assertEqual(_expires(url), ["600"])
        self.assertIn("X-Amz-Signature=", url)

    def test_none_keeps_configured_r2_ttl(self):
        storage = _r2_storage(r2_ttl=1800)
        self.assertEqual(_expires(storage.presign_get("originals/ab/abc.pdf")), ["1800"])
        self.assertEqual(_expires(storage.presign_get("originals/ab/abc.pdf", ttl_seconds=None)), ["1800"])

    def test_out_of_range_ttl_is_refused(self):
        storage = _r2_storage()
        for bad in (0, -1, 3601, True, 600.0, "600"):
            with self.subTest(ttl=bad), self.assertRaises(ValueError):
                storage.presign_get("originals/ab/abc.pdf", ttl_seconds=bad)  # type: ignore[arg-type]

    def test_local_mode_still_refuses_presign(self):
        storage = storage_module.ObjectStorage()
        storage.mode = "local"
        storage.settings = SimpleNamespace(r2_bucket="bucket", r2_presign_ttl_seconds=3600)
        for ttl in (None, 600):
            with self.subTest(ttl=ttl), self.assertRaises(storage_module.ObjectStorageError):
                storage.presign_get("originals/ab/abc.pdf", ttl_seconds=ttl)


class ExternalFileUrlTtlConfigTests(unittest.TestCase):
    def test_default_is_600(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXTERNAL_FILE_URL_TTL_SECONDS", None)
            self.assertEqual(config._load().external_file_url_ttl_seconds, 600)

    def test_bounds_are_inclusive(self):
        for value in ("60", "3600", "900"):
            with self.subTest(value=value), patch.dict(os.environ, {"EXTERNAL_FILE_URL_TTL_SECONDS": value}):
                self.assertEqual(config._load().external_file_url_ttl_seconds, int(value))

    def test_out_of_range_fails_closed(self):
        for value in ("59", "3601", "0", "-600"):
            with self.subTest(value=value), patch.dict(os.environ, {"EXTERNAL_FILE_URL_TTL_SECONDS": value}):
                with self.assertRaisesRegex(ValueError, "60..3600"):
                    config._load()

    def test_non_integer_fails_closed(self):
        for value in ("", "ten", "600.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"EXTERNAL_FILE_URL_TTL_SECONDS": value}):
                with self.assertRaisesRegex(ValueError, "EXTERNAL_FILE_URL_TTL_SECONDS"):
                    config._load()

    def test_does_not_change_internal_presign_ttl(self):
        with patch.dict(os.environ, {"EXTERNAL_FILE_URL_TTL_SECONDS": "120", "R2_PRESIGN_TTL_SECONDS": "3600"}):
            s = config._load()
        self.assertEqual((s.external_file_url_ttl_seconds, s.r2_presign_ttl_seconds), (120, 3600))


if __name__ == "__main__":
    unittest.main()
