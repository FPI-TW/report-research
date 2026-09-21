"""Private S3-compatible object storage for original reports.

Object keys are data, never filesystem paths.  This module intentionally imports boto3 only
when R2 is enabled so the normal local development path has no network/client side effects.
All its methods are synchronous; FastAPI callers must use :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from app.config import get_settings


class ObjectStorageError(RuntimeError):
    """R2 authentication, connection, or service failure (HTTP callers map to 503)."""


class ObjectNotFound(ObjectStorageError):
    """A requested key was confirmed absent (HTTP callers map to 404)."""


class _ObjectAlreadyExists(ObjectStorageError):
    """Internal signal for an atomic create-only PutObject precondition failure."""


def original_object_key(file_hash: str, file_name: str) -> str:
    """Canonical key for a source report; pairing is by SHA-256, never filename."""
    suffix = Path(file_name).suffix.lower()
    if not file_hash or len(file_hash) < 2 or not suffix:
        raise ValueError("original object key requires file_hash and original extension")
    return f"originals/{file_hash[:2]}/{file_hash}{suffix}"


@contextmanager
def verified_file_snapshot(path: str | Path, expected_sha256: str) -> Iterator[tuple[Path, str]]:
    """Copy a mutable source once, then use only the verified immutable snapshot downstream."""
    p = Path(path)
    if len(expected_sha256) != 64:
        raise ObjectStorageError("expected SHA-256 must be a full digest")
    with TemporaryDirectory(prefix="report-mark-r2-upload-") as directory:
        snapshot = Path(directory) / p.name
        try:
            source = p.open("rb")
        except OSError as exc:
            raise ObjectStorageError(f"cannot snapshot upload source: {exc}") from exc
        digest = hashlib.sha256()
        with source, snapshot.open("xb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                target.write(chunk)
        actual = digest.hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise ObjectStorageError(f"upload snapshot SHA-256 mismatch expected={expected_sha256} actual={actual}")
        yield snapshot, actual


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    # A missing bucket is a credential/configuration/service outage, never evidence that an
    # individual object is absent.  Treating it as a 404 would make hybrid silently serve an
    # old local copy and hide a broken R2 deployment.
    return code in {"404", "NoSuchKey", "NotFound"}


def _is_precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    # S3 documents both 412 and the concurrent conditional-write 409 response for If-None-Match.
    # In either case, only a subsequent HEAD with an exact full SHA can make this idempotent.
    return code in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}


def original_available(storage: "ObjectStorage", object_key: str | None, file_path: str | None) -> bool:
    """Whether ``/file`` can serve this original.  r2 never consults ``file_path``."""
    import os

    if storage.mode == "r2":
        return bool(object_key)
    return bool(object_key) or (bool(file_path) and os.path.isfile(file_path))


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """RFC 6266 header value with an ASCII fallback plus RFC 5987 ``filename*`` for CJK names."""
    from urllib.parse import quote

    name = Path(filename).name or "download"
    ascii_name = "".join(ch if 32 < ord(ch) < 127 and ch not in '"\\' else "_" for ch in name)
    if not Path(ascii_name).stem.strip("_"):
        ascii_name = "download" + Path(name).suffix
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


class ObjectStorage:
    """Singleton-friendly synchronous R2 client with deliberately small surface area."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.mode = self.settings.object_storage_mode
        self._client = None
        self._client_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.mode != "local"

    @property
    def local_fallback_allowed(self) -> bool:
        return self.mode in {"local", "hybrid"}

    def _build_client(self):
        """Build once behind ``_client_lock``; isolated for deterministic concurrency tests."""
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # clearer than an import-time crash in local-only installs
            raise ObjectStorageError("boto3 is required when object storage is enabled") from exc
        return boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            aws_access_key_id=self.settings.r2_access_key_id,
            aws_secret_access_key=self.settings.r2_secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"mode": "standard", "max_attempts": 3},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    def _get_client(self):
        if not self.enabled:
            raise ObjectStorageError("OBJECT_STORAGE_MODE=local does not use R2")
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = self._build_client()
        return self._client

    def _call(self, method: str, **kwargs):
        try:
            return getattr(self._get_client(), method)(**kwargs)
        except Exception as exc:  # botocore types are intentionally not import-time dependencies
            if _is_precondition_failed(exc):
                raise _ObjectAlreadyExists(kwargs.get("Key", "object already exists")) from exc
            if _is_not_found(exc):
                raise ObjectNotFound(kwargs.get("Key", "object not found")) from exc
            raise ObjectStorageError(f"R2 {method} failed: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._call("head_object", Bucket=self.settings.r2_bucket, Key=key)
            return True
        except ObjectNotFound:
            return False

    def head_object(self, key: str) -> dict:
        """Return object metadata, retaining confirmed-missing vs service-error semantics."""
        return self._call("head_object", Bucket=self.settings.r2_bucket, Key=key)

    def sha256(self, key: str, *, chunk_size: int = 1024 * 1024) -> str:
        """Hash a remote object incrementally; reconciliation must not buffer reports in RAM."""
        response = self._call("get_object", Bucket=self.settings.r2_bucket, Key=key)
        body = response.get("Body")
        digest = hashlib.sha256()
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)
        except Exception as exc:
            raise ObjectStorageError(f"R2 hash read failed: {exc}") from exc
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    @contextmanager
    def _verified_snapshot(self, path: str | Path, expected_sha256: str) -> Iterator[tuple[Path, str]]:
        """Copy to an immutable temp file and prove its bytes match the caller's expected SHA."""
        with verified_file_snapshot(path, expected_sha256) as snapshot:
            yield snapshot

    @staticmethod
    def _stored_sha256(head: dict) -> str | None:
        metadata = head.get("Metadata") if isinstance(head, dict) else None
        if not isinstance(metadata, dict):
            return None
        value = metadata.get("sha256") or metadata.get("SHA256")
        return value if isinstance(value, str) else None

    def _put_create_only(self, *, key: str, body, expected_sha256: str, extra: dict) -> None:
        """Atomically create ``key`` or prove its existing immutable content is identical.

        PutObject's ``IfNoneMatch='*'`` is the only race-safe check: HEAD-then-PUT would allow a
        later writer to overwrite a previously verified object.  A precondition failure is an
        idempotent success only when the existing object's full SHA metadata exactly matches the
        verified source snapshot; otherwise the key is treated as an integrity collision.
        """
        try:
            self._call(
                "put_object",
                Bucket=self.settings.r2_bucket,
                Key=key,
                Body=body,
                IfNoneMatch="*",
                Metadata={"sha256": expected_sha256},
                **extra,
            )
            return
        except _ObjectAlreadyExists:
            pass

        try:
            stored_sha256 = self._stored_sha256(self.head_object(key))
        except ObjectStorageError as exc:
            raise ObjectStorageError(f"R2 create-only verification failed key={key}: {exc}") from exc
        if stored_sha256 is not None and hmac.compare_digest(stored_sha256, expected_sha256):
            return
        raise ObjectStorageError(
            f"R2 create-only key collision or missing SHA metadata key={key} expected={expected_sha256}"
        )

    def upload_file(self, path: str | Path, key: str, *, expected_sha256: str) -> None:
        """Upload a verified immutable snapshot; metadata and bytes always share one digest."""
        p = Path(path)
        extra: dict[str, str] = {}
        content_type, _ = mimetypes.guess_type(p.name)
        if content_type:
            extra["ContentType"] = content_type
        with self._verified_snapshot(p, expected_sha256) as (snapshot, digest):
            # A file object keeps large originals streaming; the verified snapshot and metadata
            # describe exactly the same immutable bytes.
            with snapshot.open("rb") as source:
                self._put_create_only(key=key, body=source, expected_sha256=digest, extra=extra)

    def upload_bytes(self, data: bytes, key: str) -> None:
        self._put_create_only(
            key=key,
            body=data,
            expected_sha256=hashlib.sha256(data).hexdigest(),
            extra={"ContentType": "application/pdf"},
        )

    def download_bytes(self, key: str) -> bytes:
        response = self._call("get_object", Bucket=self.settings.r2_bucket, Key=key)
        body = response["Body"]
        try:
            return body.read()
        except Exception as exc:
            raise ObjectStorageError(f"R2 read failed: {exc}") from exc
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    def delete(self, key: str) -> None:
        self._call("delete_object", Bucket=self.settings.r2_bucket, Key=key)

    def ping(self) -> None:
        """Cheapest call that proves endpoint, credentials and bucket are all usable.

        ``list_objects_v2(MaxKeys=1)`` rather than ``head_object`` on a sentinel key: a HEAD
        response has no body, so a missing *bucket* and a missing *key* both surface as a bare
        404 and would be indistinguishable from a healthy "not found".  The list call reports
        ``NoSuchBucket`` with a body, which ``_call`` raises as ``ObjectStorageError``.  It is
        also the operation the reconcile job already performs with these credentials, so no
        additional token permission is required.  Raises on any failure; returns nothing.
        """
        self._call("list_objects_v2", Bucket=self.settings.r2_bucket, MaxKeys=1)

    def list_keys(self, prefix: str) -> list[str]:
        """List one owned prefix completely; callers must treat results as report-only inventory."""
        keys: list[str] = []
        continuation: str | None = None
        while True:
            kwargs = {"Bucket": self.settings.r2_bucket, "Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            page = self._call("list_objects_v2", **kwargs)
            keys.extend(item["Key"] for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            continuation = page.get("NextContinuationToken")
            if not continuation:
                raise ObjectStorageError("R2 inventory pagination missing continuation token")

    def presign_get(self, key: str, *, filename: str | None = None, inline: bool = False) -> str:
        """Short-lived GET URL.  ``filename`` restores the human name on a cross-origin download.

        Object keys are content-addressed (``originals/<hash>.pdf``), so without an explicit
        ``Content-Disposition`` the browser saves ``<hash>.pdf``; ``<a download>`` cannot fix
        that because the attribute is ignored once the 302 leaves our origin.
        """
        params = {"Bucket": self.settings.r2_bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = content_disposition(filename, inline=inline)
        try:
            return self._get_client().generate_presigned_url(
                "get_object", Params=params, ExpiresIn=self.settings.r2_presign_ttl_seconds,
            )
        except Exception as exc:
            raise ObjectStorageError(f"R2 presign failed: {exc}") from exc

    @contextmanager
    def downloaded_file(self, key: str, filename: str) -> Iterator[Path]:
        """Download an object to a transient filename and remove it on every exit path."""
        with TemporaryDirectory(prefix="report-mark-r2-") as directory:
            target = Path(directory) / Path(filename).name
            self._call("download_file", Bucket=self.settings.r2_bucket, Key=key, Filename=str(target))
            yield target


_STORAGE: ObjectStorage | None = None
_STORAGE_LOCK = threading.Lock()


def get_object_storage() -> ObjectStorage:
    global _STORAGE
    if _STORAGE is None:
        with _STORAGE_LOCK:
            if _STORAGE is None:
                _STORAGE = ObjectStorage()
    return _STORAGE
