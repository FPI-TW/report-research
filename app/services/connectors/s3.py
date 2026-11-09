"""S3 相容 bucket 連接器：把 bucket 某個 prefix 底下的新檔拉到本機落地資料夾。

`pull()` 是 `scripts/r2_inbox.py pull` 一般化出來的核心（廷豐的 R2 inbox 照舊用它、行為不變）：
「是不是新檔」沿用 rsync `--size-only` 的判準——落地資料夾沒有、或大小不同才拉；先寫 `.<名>.part`
再改名，中途被殺不會留下以正式檔名存在的半個檔；物件 metadata 的 `mtime` 有給就還原（入庫靠 mtime
補 report_date）。所以落地資料夾要保留（入庫後 `research_report.file_path` 也指向它），bucket 端的物件
可以設生命週期規則定期清掉，已經拉過的不受影響。

連線設定與憑證走環境變數，前綴由 profile 的 `env` 決定（例如 `env = "CONNECTOR_INBOX"`）：

| 變數 | 必填 | 說明 |
|---|---|---|
| `<env>_BUCKET` | 是 | bucket 名稱 |
| `<env>_ACCESS_KEY_ID`、`<env>_SECRET_ACCESS_KEY` | 是 | 唯讀（List＋Get）即可 |
| `<env>_ENDPOINT` | 否 | S3 相容端點（R2、MinIO…）；AWS S3 留空 |
| `<env>_REGION` | 否 | 預設 `auto`（R2）；AWS S3 填 bucket 所在區域 |

bucket 的 key 不是可信輸入：絕對路徑、`..` 一律拒收（`sidecar.safe_relative`）。boto3 只在真的要連線時
才 import（同 `app/services/object_storage.py`）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

from app.services.connectors.base import EXTS, FetchReport
from app.services.connectors.sidecar import MANIFEST_NAME, SIDECAR_SUFFIX, safe_relative

# s3 連接器只拉這些：研報檔、同名 JSON 側檔、根目錄的 CSV 清單。其他物件（縮圖、說明檔）不落地。
CONNECTOR_SUFFIXES = frozenset(EXTS | {SIDECAR_SUFFIX, ".csv"})


class ConnectorConfigError(RuntimeError):
    """連接器缺必要的環境設定（整個連接器不跑，不是逐檔失敗）。"""


class BucketReader(Protocol):
    def list_objects(self, prefix: str) -> list[tuple[str, int]]: ...

    def download_to(self, key: str, target: str | Path) -> None: ...

    def head_object(self, key: str) -> dict: ...


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def pull(
    store: BucketReader,
    prefix: str,
    mirror: Path,
    *,
    suffixes: Iterable[str] | None = None,
    errors: tuple[type[BaseException], ...] = (OSError,),
    log: Callable[[str], None] = _stderr,
) -> tuple[list[str], int]:
    """拉回 mirror 沒有或大小不同的物件；回傳（本輪新落地的相對路徑, 失敗數）。

    `suffixes` 有給就只拉這些副檔名（小寫比對；`_metadata.csv` 只認根目錄那一份）；None＝全部（R2 inbox）。
    `errors` 是單一物件下載失敗時要接住、計成失敗的例外型別（R2 inbox 傳 ObjectStorageError）。
    """
    wanted = None if suffixes is None else {s.lower() for s in suffixes}
    landed: list[str] = []
    failed = 0
    for key, size in store.list_objects(prefix):
        rel = safe_relative(key[len(prefix):])
        if rel is None:
            log(f"[pull] 拒收不安全的 key：{key}")
            continue
        if wanted is not None:
            suffix = Path(rel).suffix.lower()
            if suffix not in wanted or (suffix == ".csv" and rel != MANIFEST_NAME):
                continue
        target = mirror / rel
        if target.is_file() and target.stat().st_size == size:
            continue
        tmp = target.with_name(f".{target.name}.part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            store.download_to(key, tmp)
            mtime = store.head_object(key).get("Metadata", {}).get("mtime")
            if mtime and mtime.isdigit():
                os.utime(tmp, (int(mtime), int(mtime)))
            # 先落地成 .part 再改名：中途被殺不會留下以正式檔名存在的半個檔。
            os.replace(tmp, target)
            landed.append(rel)
        except errors as exc:
            failed += 1
            tmp.unlink(missing_ok=True)
            log(f"[pull] 失敗：{key}：{exc}")
    return landed, failed


@dataclass(frozen=True)
class BucketConfig:
    bucket: str
    access_key_id: str
    secret_access_key: str
    endpoint: str | None
    region: str

    @classmethod
    def from_env(cls, env_prefix: str, environ: Mapping[str, str] | None = None) -> BucketConfig:
        environ = os.environ if environ is None else environ

        def get(suffix: str) -> str:
            return (environ.get(f"{env_prefix}_{suffix}") or "").strip()

        missing = [f"{env_prefix}_{s}" for s in ("BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY") if not get(s)]
        if missing:
            raise ConnectorConfigError(f"s3 連接器缺環境變數：{'、'.join(missing)}")
        return cls(bucket=get("BUCKET"), access_key_id=get("ACCESS_KEY_ID"),
                   secret_access_key=get("SECRET_ACCESS_KEY"), endpoint=get("ENDPOINT") or None,
                   region=get("REGION") or "auto")


class BucketError(RuntimeError):
    pass


class S3Bucket:
    """最小的唯讀 S3 客戶端（list／get／head），與 `ObjectStorage` 同一組 botocore 設定。"""

    def __init__(self, config: BucketConfig, client=None) -> None:
        self.config = config
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise BucketError("s3 連接器需要 boto3") from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
                region_name=self.config.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"mode": "standard", "max_attempts": 3},
                    connect_timeout=5,
                    read_timeout=60,
                ),
            )
        return self._client

    def _call(self, method: str, **kwargs):
        try:
            return getattr(self._get_client(), method)(Bucket=self.config.bucket, **kwargs)
        except BucketError:
            raise
        except Exception as exc:  # botocore 的例外型別刻意不在 import 期依賴
            raise BucketError(f"S3 {method} 失敗：{exc}") from exc

    def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        continuation: str | None = None
        while True:
            kwargs = {"Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            page = self._call("list_objects_v2", **kwargs)
            out.extend((item["Key"], int(item["Size"])) for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return out
            continuation = page.get("NextContinuationToken")
            if not continuation:
                raise BucketError("S3 清單分頁缺 continuation token")

    def download_to(self, key: str, target: str | Path) -> None:
        self._call("download_file", Key=key, Filename=str(target))

    def head_object(self, key: str) -> dict:
        return self._call("head_object", Key=key)


class S3Connector:
    kind = "s3"

    def __init__(self, name: str, prefix: str, landing: Path, store: BucketReader) -> None:
        self.name = name
        self.prefix = prefix
        self.root = landing
        self.store = store

    def fetch(self) -> FetchReport:
        errors: list[str] = []
        landed, failed = pull(self.store, self.prefix, self.root, suffixes=CONNECTOR_SUFFIXES,
                              errors=(BucketError, OSError), log=errors.append)
        for line in errors:
            _stderr(f"[{self.name}] {line}")
        return FetchReport(landed=len(landed), failed=failed, errors=tuple(errors))
