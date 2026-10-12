"""研報上傳的隔離區（本機目錄，Admin v1.5 上傳管線）：收檔時把 raw body 安全地落地。

收檔流程（web，`web/routers/admin_uploads.py` 呼叫）：

1. `ensure_dirs()`：隔離區與 `incoming/` 都是 0700（已存在也會收回成 0700）。建不出來或不是目錄
   ＝`QuarantineUnavailable`（API 回 503 `quarantine_unavailable`）。
2. `check_free_space()`：剩餘空間扣掉這次宣告的大小後低於門檻也是 `QuarantineUnavailable`。
3. `QuarantineWriter`：以 `O_CREAT | O_EXCL | O_NOFOLLOW` 開 `incoming/<upload_id>.part`（0600），
   邊寫邊算 SHA-256、邊計位元組；超過上限立刻拋 `UploadTooLarge`（呼叫端 `discard()` 刪 `.part`）。
4. `finalize()`：前 1024 bytes 要有 `%PDF-1.` 或 `%PDF-2.`、最後 1024 bytes 要有 `%%EOF`（沒有＝截斷），
   否則 `NotPdf`；通過後 fsync、rename 成隔離區根目錄的 `<upload_id>.bin`、再 fsync 目錄。
   檔名一律是 upload_id，**不用使用者檔名、不帶 .pdf**（避免被誤開）；原始檔名清理後只存 DB。

刻意不做的事：

- **不解析 PDF 內容**。主動內容（/JavaScript 等）與加密由 worker 在子行程裡檢查；web 是單 worker，
  讓它解析不可信的 PDF 等於把整站暴露給解析器 DoS。這裡只看前後各 1024 bytes 的字面。
- **不連 clamd**。掃描是 worker 的事；web 只把檔案放進隔離區、寫一列 `quarantined`。

之後的路徑（worker 用，這裡只定義名稱）：`infected/<upload_id>.bin`（感染證據，0400）；
掃描通過後搬到 `data/uploads/clean/<hash>.pdf`。rename 與 INSERT 之間崩潰留下的孤兒 `.bin`／`.part`
由 worker 清（超過 1 小時、DB 沒有對應列）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "data" / "quarantine"
INCOMING_DIRNAME = "incoming"
INFECTED_DIRNAME = "infected"
PART_SUFFIX = ".part"
BIN_SUFFIX = ".bin"

DIR_MODE = 0o700
FILE_MODE = 0o600
HEAD_BYTES = 1024
TAIL_BYTES = 1024
PDF_MAGICS: tuple[bytes, ...] = (b"%PDF-1.", b"%PDF-2.")
EOF_MARK = b"%%EOF"


class QuarantineError(Exception):
    """收檔的可預期錯誤；訊息是給管理員看的中文。"""


class QuarantineUnavailable(QuarantineError):
    """隔離區不可用（建不出目錄、權限不對、剩餘空間不足）：不收檔。"""


class UploadTooLarge(QuarantineError):
    pass


class NotPdf(QuarantineError):
    pass


def quarantine_dir(settings=None) -> Path:
    """`UPLOAD_QUARANTINE_DIR`；空字串＝repo 根 `data/quarantine/`，相對路徑以 repo 根為基準。"""
    raw = (settings or get_settings()).upload_quarantine_dir
    if not raw:
        return DEFAULT_DIR
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _ensure_private_dir(path: Path) -> None:
    os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):  # 符號連結或一般檔案：不跟著走
        raise NotADirectoryError(str(path))
    if stat.S_IMODE(info.st_mode) != DIR_MODE:
        os.chmod(path, DIR_MODE)


def ensure_dirs(root: Path) -> Path:
    """建立（或收回成 0700）隔離區與 `incoming/`，回 `incoming/` 的路徑。"""
    incoming = root / INCOMING_DIRNAME
    try:
        _ensure_private_dir(root)
        _ensure_private_dir(incoming)
    except OSError as exc:
        raise QuarantineUnavailable("隔離區目錄無法使用，暫時不能上傳") from exc
    return incoming


def check_free_space(root: Path, *, needed_bytes: int, min_free_bytes: int) -> None:
    """剩餘空間扣掉這次要寫的大小後必須仍不低於門檻。查不到也當作不可用（fail-closed）。"""
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        raise QuarantineUnavailable("隔離區目錄無法使用，暫時不能上傳") from exc
    if free - max(needed_bytes, 0) < min_free_bytes:
        raise QuarantineUnavailable("隔離區剩餘空間不足，暫時不能上傳")


def bin_path(root: Path, upload_id: str) -> Path:
    return root / f"{upload_id}{BIN_SUFFIX}"


def part_path(root: Path, upload_id: str) -> Path:
    return root / INCOMING_DIRNAME / f"{upload_id}{PART_SUFFIX}"


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class StoredFile:
    path: Path
    sha256: str
    size_bytes: int


class QuarantineWriter:
    """單一上傳的 `.part` 寫入器。用法：`open()` → 多次 `write()` → `finalize()`；任何失敗都 `discard()`。

    不是執行緒安全的；一個請求一個。`write()` 是同步的小區塊寫入（請求串流一次幾十 KB），
    `finalize()` 有 fsync，呼叫端以 `asyncio.to_thread` 執行。
    """

    def __init__(self, root: Path, upload_id: str, *, max_bytes: int):
        self.root = root
        self.upload_id = upload_id
        self.max_bytes = max_bytes
        self.part = part_path(root, upload_id)
        self.final = bin_path(root, upload_id)
        self.size = 0
        self._fd: int | None = None
        self._sha = hashlib.sha256()
        self._head = b""
        self._tail = b""
        self._finalized = False

    def open(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            self._fd = os.open(self.part, flags, FILE_MODE)
            os.fchmod(self._fd, FILE_MODE)  # umask 只會拿掉位元；這行讓結果與 umask 無關
        except OSError as exc:
            self._close()
            raise QuarantineUnavailable("隔離區無法寫入，暫時不能上傳") from exc

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._fd is None:
            raise RuntimeError("QuarantineWriter 尚未 open()")
        if self.size + len(chunk) > self.max_bytes:
            raise UploadTooLarge(f"檔案超過上限 {self.max_bytes // (1024 * 1024)} MB")
        view = memoryview(chunk)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self.size += len(chunk)
        self._sha.update(chunk)
        if len(self._head) < HEAD_BYTES:
            self._head += chunk[: HEAD_BYTES - len(self._head)]
        self._tail = (self._tail + chunk[-TAIL_BYTES:])[-TAIL_BYTES:]

    def check_pdf(self) -> None:
        """只看字面：開頭的版本標記與結尾的 %%EOF。不解析內容。"""
        if self.size == 0 or not any(m in self._head for m in PDF_MAGICS):
            raise NotPdf("只接受 PDF 檔（檔頭不是 %PDF-1.x／%PDF-2.x）")
        if EOF_MARK not in self._tail:
            raise NotPdf("PDF 檔案不完整（結尾沒有 %%EOF，可能是傳輸中斷或檔案截斷）")

    def finalize(self) -> StoredFile:
        """檢查字面 → fsync → rename 成 `<upload_id>.bin` → fsync 目錄。失敗時呼叫端負責 `discard()`。"""
        if self._fd is None:
            raise RuntimeError("QuarantineWriter 尚未 open()")
        self.check_pdf()
        os.fsync(self._fd)
        self._close()
        if os.path.lexists(self.final):  # upload_id 是新的 uuid4，撞名代表有東西不對；不覆寫
            raise QuarantineUnavailable("隔離區已有同名檔案，拒絕覆寫")
        os.rename(self.part, self.final)
        self._finalized = True
        _fsync_dir(self.part.parent)
        _fsync_dir(self.root)
        return StoredFile(path=self.final, sha256=self._sha.hexdigest(), size_bytes=self.size)

    def discard(self) -> None:
        """刪掉 `.part` 與（已 rename 的話）`.bin`。不拋例外：清理失敗只留給 worker 的孤兒清理。"""
        self._close()
        paths = [self.part] + ([self.final] if self._finalized else [])
        for path in paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("隔離區清理失敗（留給 worker 的孤兒清理）：%s", path, exc_info=True)

    def _close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
