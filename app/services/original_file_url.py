"""原檔的短效 presigned URL：站內 `/api/report/{report_id}/file` 與對外 API 共用的唯一實作。

presign 出去的 URL 是持有即可下載的憑證，產生前的檢查順序是刻意的：

1. `file_hash` 必須是 64 個小寫 hex，`object_key` 必須**逐字等於** `original_object_key(file_hash, file_name)`。
   DB 裡的 object key 是不可信的資料——指錯了就可能替另一篇研報（甚至 bucket 裡任何物件）簽出連結，
   所以這一步一定在任何遠端操作之前，指標不對連 HEAD 都不打。
2. `verify_object=True` 時以 HEAD 確認物件存在、且 metadata 的 sha256 等於 `file_hash`；
   HEAD 同時把「確定不存在」（`ObjectNotFound`）與「服務掛了」（`ObjectStorageError`）分開。
   對外搜尋一次要附多筆連結，用 `verify_object=False` 只做第 1 步、不逐筆 HEAD。
3. presign。`ttl_seconds=None` 沿用站內預設（`R2_PRESIGN_TTL_SECONDS`），只有明確給值時才傳給
   `presign_get`（站內路徑的呼叫參數與抽出前逐字相同）。

非物件儲存模式（local）沒有可交出去的 URL，一律 `OriginalNotFound`；本機檔案的回退
（hybrid 模式）只屬於站內路由，不在這裡。錯誤以三種例外區分，HTTP 對照由呼叫端決定：
找不到（`OriginalNotFound`）、完整性錯誤（`OriginalIntegrityError`）、儲存不可用（`OriginalStorageUnavailable`）。
"""

from __future__ import annotations

import asyncio
import re

from app.services.object_storage import (
    ObjectNotFound,
    ObjectStorage,
    ObjectStorageError,
    get_object_storage,
    original_object_key,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OriginalFileError(Exception):
    """產生原檔 URL 失敗（三個子類別之一）。"""


class OriginalNotFound(OriginalFileError):
    """沒有可交出去的原檔：沒有 object key、物件確定不存在，或不是物件儲存模式。"""


class OriginalIntegrityError(OriginalFileError):
    """指標或物件 metadata 與 `file_hash` 對不上：不簽出任何連結。"""


class OriginalStorageUnavailable(OriginalFileError):
    """物件儲存認證、連線或服務失敗。"""


def _check_pointer(*, file_name, object_key: str, file_hash) -> None:
    if not isinstance(file_hash, str) or not _SHA256_RE.fullmatch(file_hash):
        raise OriginalIntegrityError("file_hash 不是 sha256")
    try:
        canonical_key = original_object_key(file_hash, file_name)
    except (TypeError, ValueError) as exc:
        raise OriginalIntegrityError("無法由 file_hash 與檔名推得 object key") from exc
    if object_key != canonical_key:
        raise OriginalIntegrityError("object key 不是這篇研報的 canonical key")


async def mint_original_url(
    *,
    file_name,
    object_key,
    file_hash,
    ttl_seconds: int | None = None,
    verify_object: bool = True,
    storage: ObjectStorage | None = None,
) -> str:
    """檢查指標（必要時 HEAD 驗 sha256）後回 presigned GET URL。

    `storage` 給呼叫端注入（站內路由沿用自己取得的那一個，測試以此替換）；未給時用
    `get_object_storage()`。PDF 以 inline 簽出（瀏覽器內嵌），其他檔案為 attachment。
    """
    storage = storage if storage is not None else get_object_storage()
    if not storage.enabled:
        raise OriginalNotFound("非物件儲存模式，沒有可交出的原檔 URL")
    if not object_key:
        raise OriginalNotFound("研報沒有原檔的 object key")
    _check_pointer(file_name=file_name, object_key=object_key, file_hash=file_hash)
    presign_kwargs: dict = {"filename": file_name, "inline": str(file_name).lower().endswith(".pdf")}
    if ttl_seconds is not None:
        presign_kwargs["ttl_seconds"] = ttl_seconds
    try:
        if verify_object:
            metadata = await asyncio.to_thread(storage.head_object, object_key)
            raw_metadata = metadata.get("Metadata") if isinstance(metadata, dict) else None
            object_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            metadata_sha = object_metadata.get("sha256") or object_metadata.get("SHA256")
            if not isinstance(metadata_sha, str) or metadata_sha != file_hash:
                raise OriginalIntegrityError("物件 metadata 的 sha256 與 file_hash 不符")
        return await asyncio.to_thread(storage.presign_get, object_key, **presign_kwargs)
    except ObjectNotFound as exc:
        raise OriginalNotFound("原檔物件不存在") from exc
    except ObjectStorageError as exc:
        raise OriginalStorageUnavailable("物件儲存無法使用") from exc
