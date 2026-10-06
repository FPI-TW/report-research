"""研報上傳 worker 的決定性部分（Admin v1.5 上傳管線 PR-5）：整輪鎖、殘留回收、掃描、搬正、狀態轉移、清除。

入口是 `scripts/process_uploads.py`（子命令 `scan`／`ingest`／`cleanup`）與殼 `scripts/process_uploads.sh`
（systemd `report-mark-upload.service`，timer 每 5 分鐘）。入庫那一步要呼叫 `scripts/_ingest_core.ingest_one`
（會打 LLM），所以留在入口檔；這裡**零 LLM**、不 import `scripts.*`，只做 Python 決定的事。

狀態機（詞彙在 `app/services/uploads.py`；每一次轉移都是**條件式 UPDATE**——`WHERE id = … AND state = 舊狀態`，
影響 0 列就代表別人先動了，這一筆跳過）：

    quarantined ─認領─▶ scanning ─OK─▶ clean ─認領（持 LLM 鎖）─▶ processing ─pre_upsert─▶ draft
    scanning ─FOUND─▶ infected（證據搬到 infected/<id>.bin、0400，30 天後清檔，DB 永久保留）
    scanning ─SHA 與 DB 不符／檔案不見─▶ blocked（failure_kind=hash_mismatch）
    scanning ─決定性錯誤（第 3 次）─▶ blocked；未滿 3 次與一切暫時性錯誤 ─▶ quarantined（scan_attempts+1，永不放行）
    processing ─▶ failed（failure_kind 見 uploads.FAILURE_KINDS）／duplicate（語料已有同 hash）
    processing ─斷路器─▶ clean（failure_kind=llm_breaker：延後，不算失敗）

**整輪鎖**（`round_lock`，`data/.upload_worker.lock`，非阻塞 flock）：殼取一次、整輪持有，以環境變數
`UPLOAD_WORKER_LOCK_FD` 把 fd 傳給子命令；子命令對繼承的 fd 再 flock 一次（同一個 open file description，
已持有時是 no-op 成功），藉此**證明**自己在殼的鎖底下，而不是盲信環境變數。手動直接跑子命令時自己取鎖。
所以持鎖時看到的 `scanning`／`processing` 必定是上一輪被殺的殘留（`recover_stale`）。

**草稿原子性**：`draft_hook` 是 `ingest_one` 的 `pre_upsert`——在 `store.upsert_report` 之前、同一個 session、
同一個交易裡寫 visibility 草稿列（`publication='draft'`）與 `report_upload.state='draft'`，由 upsert 那次 commit
一起落庫。研報因此從來沒有「已入庫、還沒標成草稿」的可見空窗，`make db-audit` 的 `upload_draft_mismatch`
也不會在中間成立（`tests/test_upload_worker_db.py` 在每一次 commit 的當下從另一條連線檢查）。

**清除**（`purge_rejected`）照 `app/services/upload_review.py` 的約定：候選來自 `list_purgeable`（只是清單，不是
許可）；在自己的交易裡以條件式 UPDATE 認領（設 `purged_at`），每一句 DELETE 都再帶一次 `corpus_purgeable_sql`。
守門不成立（已發布過、或屬於別筆進行中／已發布的上傳）時只刪這筆上傳自己的隔離區檔案。檔案與 R2 物件一律在
commit **之後**才刪（刪了檔、交易卻 rollback，會留下 DB 說有、檔案卻沒有的研報）。刪語料時另持
`scripts/_claude_lock.py` 的鎖（由入口傳進來）：與 sync 的入庫互斥，同 hash 的 NAS 檔不會在清除途中進來又被刪掉。

bind 參數一律 `CAST(:x AS ...)`。批次腳本的 logger 無聲，訊息用 print（journal 收 stdout/stderr）。
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Optional

from sqlalchemy import text

from app.services import quarantine, uploads
from app.services.upload_review import corpus_purgeable_sql, list_purgeable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_FILE = REPO_ROOT / "data" / ".upload_worker.lock"
DEFAULT_CLEAN_DIR = REPO_ROOT / "data" / "uploads" / "clean"
LOCK_FD_ENV = "UPLOAD_WORKER_LOCK_FD"

SCAN_BATCH = 50  # 每輪最多掃幾筆（全站處理中上限 50，一輪就掃得完）
PURGE_BATCH = 50
DETERMINISTIC_SCAN_MAX = 3  # 決定性掃描錯誤（同一份檔）第幾次轉 blocked（設計 1.3）
EVIDENCE_RETENTION_DAYS = 30  # 感染／攔截證據保留天數（設計決策 11）
ORPHAN_MIN_AGE_SECONDS = 3600  # 隔離區孤兒檔：超過 1 小時才刪（設計 1.2；web 在 rename 與 INSERT 之間崩潰）
# 殘留回收時，處理中被中止（OOM、逾時、重開機）達這個次數就轉 failed，不再自動重試：同一份檔每 5 分鐘
# 把 worker 打掛一次的話，沒有這道上限就是無窮迴圈。管理員重試（failed → clean）會再給一次機會。
MAX_PROCESS_ATTEMPTS = 3
DETAIL_MAX = 2000  # report_upload.failure_detail 的 CHECK
SCAN_ERROR_MAX = 1000

CLEAN_DIR_MODE = 0o750
CLEAN_FILE_MODE = 0o640
INFECTED_FILE_MODE = 0o400
NAME_MAX_BYTES = 255  # ext4 的檔名上限是 bytes，不是字元（中文檔名 255 字會超過）

BACKFILL_SCRIPT = "backfill_extraction.py"
BACKFILL_UNIT = "report-mark-backfill.service"

_DETERMINISTIC_RE = re.compile(r"^\[決定性 (\d+)/\d+\]")
_WEBHOOK_RE = re.compile(r"^https?://[^\s\"\\]+$")


# ── 路徑 ────────────────────────────────────────────────────────────────


def _setting_path(raw: str, default: Path) -> Path:
    if not raw:
        return default
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def lock_file(settings=None) -> Path:
    from app.config import get_settings

    return _setting_path((settings or get_settings()).upload_worker_lock_file, DEFAULT_LOCK_FILE)


def clean_root(settings=None) -> Path:
    from app.config import get_settings

    return _setting_path((settings or get_settings()).upload_clean_dir, DEFAULT_CLEAN_DIR)


def fs_name(original_name: str) -> str:
    """DB 的原始檔名 → 乾淨檔的檔名。保留原名（`parse_filename` 靠它推券商、日期、是否行政文件），
    只在 UTF-8 超過 255 bytes 時截主檔名（保留 `.pdf`）。收檔時已去掉路徑成分與控制字元，這裡再擋一次。"""
    name = original_name.replace("/", "_").replace("\\", "_").replace("\0", "")
    if name in ("", ".", ".."):
        name = "upload.pdf"
    if len(name.encode("utf-8")) <= NAME_MAX_BYTES:
        return name
    stem, suffix = os.path.splitext(name)
    budget = NAME_MAX_BYTES - len(suffix.encode("utf-8"))
    out = ""
    for ch in stem:
        if len((out + ch).encode("utf-8")) > budget:
            break
        out += ch
    return (out.rstrip() or "upload") + suffix


def clean_file(root: Path, file_hash: str, original_name: str) -> Path:
    """`<clean_root>/<hash>/<原始檔名>`：一個 hash 一個目錄，檔名保留原名（`research_report.file_path`）。

    設計文件寫的是 `<hash>.pdf`，但 `ingest_one` 以 `path.name` 當檔名推券商、日期、行政文件與顯示名稱，
    介面固定不能改；用 hash 當檔名會讓這些全部失效（顯示名稱也會變成一串 hash）。"""
    if not re.fullmatch(r"[0-9a-f]{64}", file_hash or ""):
        raise ValueError(f"file_hash 不合法：{file_hash!r}")
    return root / file_hash / fs_name(original_name)


def infected_file(qroot: Path, upload_id: str) -> Path:
    return qroot / quarantine.INFECTED_DIRNAME / f"{upload_id}{quarantine.BIN_SUFFIX}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clip(value: Optional[str], limit: int = DETAIL_MAX) -> Optional[str]:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _say(msg: str) -> None:
    print(f"[upload-worker] {msg}", flush=True)


def journal_error(msg: str) -> None:
    """stderr 一行 ERROR。在 systemd 底下（有 `JOURNAL_STREAM`）加 `<3>` 前綴，journald 以 err 優先序收
    （`journalctl -p err` 撈得到）；手動執行時改成肉眼可讀的 `ERROR`。"""
    prefix = "<3>" if os.environ.get("JOURNAL_STREAM") else "ERROR "
    print(f"{prefix}[upload-worker] {msg}", file=sys.stderr, flush=True)


def write_hashes(path: Path, hashes: list[str]) -> None:
    """本輪入庫的 file_hash（每行一個）原子寫入；空清單寫 0-byte 檔（殼以 `[ -s ]` 判斷）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(hashes))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ── 整輪鎖 ──────────────────────────────────────────────────────────────


@contextmanager
def round_lock(path: Path, *, owner: str = "process_uploads") -> Iterator[bool]:
    """整輪鎖。yield True＝持有（自己取的，或證明了殼傳下來的 fd 正持有它）；False＝別人在跑。"""
    inherited = (os.environ.get(LOCK_FD_ENV) or "").strip()
    if inherited:
        fd = int(inherited)
        st_fd, st_path = os.fstat(fd), os.stat(path)
        if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
            raise RuntimeError(f"{LOCK_FD_ENV}={fd} 指向的不是鎖檔 {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            held = False
        else:
            held = True
        # 不解鎖：鎖屬於殼（同一個 open file description），殼結束才放。
        yield held
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            yield False
            return
        payload = json.dumps({"pid": os.getpid(), "script": owner,
                              "started_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        try:  # 診斷資訊（ops 代理試探鎖時印出持有者）；寫不進去不影響鎖
            os.ftruncate(fd, 0)
            os.pwrite(fd, payload.encode("utf-8"), 0)
        except OSError:
            pass
        try:
            yield True
        finally:
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ── backfill 偵測 ────────────────────────────────────────────────────────


def backfill_running(*, proc_root: Path = Path("/proc"), systemctl: Optional[str] = "systemctl",
                     runner=subprocess.run) -> Optional[str]:
    """`scripts/backfill_extraction.py` 正在跑就回說明，否則 None（記憶體保護：它也載 BGE-M3、不取 claude 鎖）。

    兩個來源取聯集：
    1. `/proc/<pid>/cmdline` 有 `backfill_extraction.py` 的行程——手動 `uv run python scripts/backfill_extraction.py`
       與 unit 觸發的都抓得到（`uv run` 的父行程與 python 子行程都會命中，任一即可）。
    2. `systemctl show -p ActiveState report-mark-backfill.service` 是 activating／active／deactivating
       （oneshot 執行中是 activating）——補 /proc 看不到的情況（例如行程屬於別的 PID namespace）。
    偵測失敗（讀不到 /proc、沒有 systemctl）一律當作沒在跑：這是記憶體保護，不是安全閘門，偵測壞掉
    不該讓上傳永遠卡住。
    """
    me = os.getpid()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        if any(a == BACKFILL_SCRIPT or a.endswith("/" + BACKFILL_SCRIPT) for a in args):
            return f"pid={entry.name} 正在跑 {BACKFILL_SCRIPT}"
    if systemctl:
        try:
            proc = runner(
                [systemctl, "show", "-p", "ActiveState", "--value", BACKFILL_UNIT],
                capture_output=True, text=True, timeout=5, check=False,
            )
            state = (proc.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            state = ""
        if state in ("active", "activating", "deactivating", "reloading"):
            return f"{BACKFILL_UNIT} 是 {state}"
    return None


# ── 殘留回收 ────────────────────────────────────────────────────────────


@dataclass
class Recovery:
    scanning: int = 0
    processing: int = 0
    gave_up: int = 0


async def recover_stale(session) -> Recovery:
    """持整輪鎖時呼叫：`scanning` → `quarantined`、`processing` → `clean`（被中止達上限的轉 failed）。commit。"""
    r = Recovery()
    r.scanning = len((await session.execute(text(
        "UPDATE research.report_upload SET state = :q, state_changed_at = now(), "
        "scan_last_error = :why WHERE state = :s RETURNING id"
    ), {"q": uploads.STATE_QUARANTINED, "s": uploads.STATE_SCANNING,
        "why": "上一輪 worker 在掃描中被中止，退回隔離區重掃"})).all())
    r.gave_up = len((await session.execute(text(
        "UPDATE research.report_upload SET state = :failed, failure_kind = :kind, failure_detail = :why, "
        "processed_at = now(), state_changed_at = now() "
        "WHERE state = :p AND process_attempts >= :max RETURNING id"
    ), {"failed": uploads.STATE_FAILED, "kind": uploads.FAILURE_INGEST_ERROR, "p": uploads.STATE_PROCESSING,
        "max": MAX_PROCESS_ATTEMPTS,
        "why": f"worker 處理這份檔時被中止 {MAX_PROCESS_ATTEMPTS} 次（記憶體、逾時或重開機），不再自動重試；"
               "確認原因後可重試"})).all())
    r.processing = len((await session.execute(text(
        "UPDATE research.report_upload SET state = :c, state_changed_at = now() WHERE state = :p RETURNING id"
    ), {"c": uploads.STATE_CLEAN, "p": uploads.STATE_PROCESSING})).all())
    await session.commit()
    return r


# ── 稽核與通知 ──────────────────────────────────────────────────────────


async def _audit(session, action: str, upload_id: str, detail: dict) -> None:
    from app.services.accounts import record_audit

    # actor NULL＝CLI（worker）。detail 只放 upload_id、檔名、大小、狀態這類事實，不放原因全文與內文。
    await record_audit(session, actor_id=None, action=action, target_type="upload", target_id=upload_id,
                       detail=detail)


@dataclass(frozen=True)
class InfectionNotice:
    upload_id: str
    signature: str
    heuristic: bool


def send_webhook(message: str, *, url: Optional[str] = None, runner=subprocess.run) -> str:
    """有設 `REPORT_MARK_ALERT_WEBHOOK` 才送。URL 經 stdin 以 curl 設定檔餵進去（`-K -`），**不進 argv**
    （比照 deploy/systemd/report-mark-alert.sh）。回 `skipped`／`sent`／`bad_url`／`failed:<curl rc>`；
    失敗只說退出碼，不說 URL。"""
    url = os.environ.get("REPORT_MARK_ALERT_WEBHOOK", "") if url is None else url
    if not url:
        return "skipped"
    if not _WEBHOOK_RE.match(url):
        journal_error("REPORT_MARK_ALERT_WEBHOOK 不是合法 URL（須以 http:// 或 https:// 開頭、不含空白與引號），未送出")
        return "bad_url"
    payload = json.dumps({"text": message}, ensure_ascii=False)
    try:
        proc = runner(
            ["curl", "-fsS", "--connect-timeout", "5", "--max-time", "10", "-K", "-", "-X", "POST",
             "-H", "Content-Type: application/json", "-d", payload],
            input=f'url = "{url}"\n', capture_output=True, text=True, timeout=20, check=False,
        )
        rc = proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        journal_error(f"webhook 投遞失敗（{type(exc).__name__}）")
        return "failed:exec"
    if rc != 0:
        journal_error(f"webhook 投遞失敗 curl_rc={rc}")
        return f"failed:{rc}"
    return "sent"


def notify_infected(notice: InfectionNotice) -> None:
    """感染通知（設計決策 17）：journal ERROR＋可選 webhook。稽核在 DB 交易裡另寫（`upload.infected`）。"""
    kind = "規則攔截" if notice.heuristic else "惡意程式"
    msg = f"上傳檔案偵測到{kind}：upload_id={notice.upload_id} signature={notice.signature}（已隔離，不入庫）"
    journal_error(msg)
    send_webhook(f"[report-mark] {msg}")


# ── 掃描 ────────────────────────────────────────────────────────────────


@dataclass
class ScanStats:
    claimed: int = 0
    clean: int = 0
    infected: int = 0
    blocked: int = 0
    retry: int = 0
    stopped: Optional[str] = None  # 暫時性錯誤讓本輪停止掃描的原因


class _HashingReader:
    """把讀過的位元組一併算 SHA-256：送給 clamd 的內容與 DB 的 hash 是同一份（不是先算再重讀）。"""

    def __init__(self, fh: BinaryIO):
        self._fh = fh
        self.sha = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        chunk = self._fh.read(n)
        if chunk:
            self.sha.update(chunk)
        return chunk


def _deterministic_count(previous_error: Optional[str]) -> int:
    m = _DETERMINISTIC_RE.match(previous_error or "")
    return int(m.group(1)) if m else 0


async def _claim(session, upload_id: str, old: str, new: str, extra_sql: str = "") -> Optional[tuple]:
    row = (await session.execute(text(
        f"UPDATE research.report_upload SET state = :new, state_changed_at = now(){extra_sql} "
        "WHERE id = CAST(:id AS uuid) AND state = :old "
        "RETURNING id::text, file_hash, original_name, size_bytes, client_mtime, scan_attempts, scan_last_error, "
        "process_attempts"
    ), {"id": upload_id, "old": old, "new": new})).first()
    await session.commit()
    return tuple(row) if row else None


async def _block(session, upload_id: str, name: str, size: int, *, failure_kind: Optional[str],
                 detail: Optional[str], scan_error: Optional[str] = None, engine: Optional[str] = None) -> None:
    done = (await session.execute(text(
        "UPDATE research.report_upload SET state = :blocked, failure_kind = :kind, failure_detail = :detail, "
        "scan_last_error = COALESCE(:serr, scan_last_error), scan_engine = COALESCE(:eng, scan_engine), "
        "scan_attempts = scan_attempts + 1, scanned_at = now(), state_changed_at = now(), "
        "purge_after = now() + make_interval(days => :days) "
        "WHERE id = CAST(:id AS uuid) AND state = :scanning RETURNING id"
    ), {"blocked": uploads.STATE_BLOCKED, "kind": failure_kind, "detail": _clip(detail),
        "serr": _clip(scan_error, SCAN_ERROR_MAX), "eng": engine, "days": EVIDENCE_RETENTION_DAYS, "id": upload_id,
        "scanning": uploads.STATE_SCANNING})).first()
    if done:
        await _audit(session, "upload.blocked", upload_id, {
            "upload_id": upload_id, "file_name": name, "size_bytes": size, "state": uploads.STATE_BLOCKED,
            "failure_kind": failure_kind,
        })
    await session.commit()


async def scan_pending(
    session_factory,
    *,
    qroot: Path,
    scanner: Callable[[BinaryIO], object],
    notify: Callable[[InfectionNotice], None] = notify_infected,
    limit: int = SCAN_BATCH,
) -> ScanStats:
    """掃描 `quarantined`（最早上傳的先）。零 LLM、不取 claude 鎖。clamd 不可用時第一筆退回隔離區後就停：
    其餘的檔一定得到同一個答案，記一堆 attempts 只是噪音。"""
    st = ScanStats()
    async with session_factory() as session:
        ids = [r[0] for r in (await session.execute(text(
            "SELECT id::text FROM research.report_upload WHERE state = :q ORDER BY uploaded_at, id LIMIT :n"
        ), {"q": uploads.STATE_QUARANTINED, "n": limit})).all()]
        await session.rollback()
        for upload_id in ids:
            row = await _claim(session, upload_id, uploads.STATE_QUARANTINED, uploads.STATE_SCANNING)
            if row is None:
                continue  # 別人先動了（退回）
            st.claimed += 1
            _, file_hash, name, size, _mtime, _attempts, prev_error, _p = row
            src = quarantine.bin_path(qroot, upload_id)
            if not src.exists() and infected_file(qroot, upload_id).exists():
                src = infected_file(qroot, upload_id)  # 上一輪搬進 infected/ 之後、commit 之前被中止
            if not src.exists():
                await _block(session, upload_id, name, size, failure_kind=uploads.FAILURE_HASH_MISMATCH,
                             detail="隔離區找不到這份上傳的檔案")
                st.blocked += 1
                continue
            if sha256_file(src) != file_hash:
                await _block(session, upload_id, name, size, failure_kind=uploads.FAILURE_HASH_MISMATCH,
                             detail="隔離區檔案的 SHA-256 與收檔時記錄的不符")
                st.blocked += 1
                continue
            with open(src, "rb") as fh:
                reader = _HashingReader(fh)
                result = scanner(reader)
            engine = result.engine.label if getattr(result, "engine", None) is not None else None
            if result.status in ("ok", "found") and reader.sha.hexdigest() != file_hash:
                await _block(session, upload_id, name, size, failure_kind=uploads.FAILURE_HASH_MISMATCH,
                             detail="送去掃描的內容與收檔時記錄的 SHA-256 不符", engine=engine)
                st.blocked += 1
                continue
            if result.passed:
                await session.execute(text(
                    "UPDATE research.report_upload SET state = :clean, scan_engine = :eng, scanned_at = now(), "
                    "scan_last_error = NULL, scan_signature = NULL, state_changed_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND state = :scanning"
                ), {"clean": uploads.STATE_CLEAN, "eng": engine, "id": upload_id, "scanning": uploads.STATE_SCANNING})
                await session.commit()
                st.clean += 1
                continue
            if result.status == "found":
                await _quarantine_infected(session, qroot, src, upload_id, name, size, result.signature, engine)
                st.infected += 1
                notify(InfectionNotice(upload_id, result.signature, bool(result.heuristic)))
                continue
            err = f"{result.kind}: {result.detail or ''}".strip()
            if result.transient:
                await session.execute(text(
                    "UPDATE research.report_upload SET state = :q, scan_attempts = scan_attempts + 1, "
                    "scan_last_error = :err, scan_engine = COALESCE(:eng, scan_engine), state_changed_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND state = :scanning"
                ), {"q": uploads.STATE_QUARANTINED, "err": _clip(err, SCAN_ERROR_MAX), "eng": engine, "id": upload_id,
                    "scanning": uploads.STATE_SCANNING})
                await session.commit()
                st.retry += 1
                st.stopped = err
                _say(f"掃描服務暫時不可用（{err[:200]}），檔案留在隔離區，本輪停止掃描")
                break
            n = _deterministic_count(prev_error) + 1
            labelled = f"[決定性 {n}/{DETERMINISTIC_SCAN_MAX}] {err}"
            if n >= DETERMINISTIC_SCAN_MAX:
                await _block(session, upload_id, name, size, failure_kind=None, detail=None,
                             scan_error=labelled, engine=engine)
                st.blocked += 1
                continue
            await session.execute(text(
                "UPDATE research.report_upload SET state = :q, scan_attempts = scan_attempts + 1, "
                "scan_last_error = :err, scan_engine = COALESCE(:eng, scan_engine), state_changed_at = now() "
                "WHERE id = CAST(:id AS uuid) AND state = :scanning"
            ), {"q": uploads.STATE_QUARANTINED, "err": _clip(labelled, SCAN_ERROR_MAX), "eng": engine,
                "id": upload_id, "scanning": uploads.STATE_SCANNING})
            await session.commit()
            st.retry += 1
    return st


async def _quarantine_infected(session, qroot: Path, src: Path, upload_id: str, name: str, size: int,
                               signature: str, engine: Optional[str]) -> None:
    """先把檔案搬進 infected/ 並收成 0400（隔離優先），再寫 DB 與稽核。搬完、commit 前被中止時，下一輪的
    殘留回收把它退回 quarantined，掃描會在 infected/ 找到它再掃一次。"""
    dst = infected_file(qroot, upload_id)
    os.makedirs(dst.parent, mode=quarantine.DIR_MODE, exist_ok=True)
    os.chmod(dst.parent, quarantine.DIR_MODE)
    if src != dst:
        os.replace(src, dst)
    os.chmod(dst, INFECTED_FILE_MODE)
    await session.execute(text(
        "UPDATE research.report_upload SET state = :infected, scan_signature = :sig, scan_engine = :eng, "
        "scanned_at = now(), scan_last_error = NULL, state_changed_at = now(), "
        "purge_after = now() + make_interval(days => :days) "
        "WHERE id = CAST(:id AS uuid) AND state = :scanning"
    ), {"infected": uploads.STATE_INFECTED, "sig": signature[:500], "eng": engine, "days": EVIDENCE_RETENTION_DAYS,
        "id": upload_id, "scanning": uploads.STATE_SCANNING})
    await _audit(session, "upload.infected", upload_id, {
        "upload_id": upload_id, "file_name": name, "size_bytes": size, "state": uploads.STATE_INFECTED,
    })
    await session.commit()


# ── 入庫用的狀態轉移（呼叫端是 scripts/process_uploads.py）─────────────────


@dataclass(frozen=True)
class CleanItem:
    upload_id: str
    file_hash: str
    original_name: str
    size_bytes: int
    client_mtime: Optional[datetime]
    process_attempts: int


async def count_clean(session) -> int:
    n = (await session.execute(text("SELECT count(*) FROM research.report_upload WHERE state = :c"),
                               {"c": uploads.STATE_CLEAN})).scalar_one()
    await session.rollback()
    return int(n)


async def list_clean(session, limit: int) -> list[str]:
    ids = [r[0] for r in (await session.execute(text(
        "SELECT id::text FROM research.report_upload WHERE state = :c ORDER BY state_changed_at, uploaded_at, id "
        "LIMIT :n"
    ), {"c": uploads.STATE_CLEAN, "n": limit})).all()]
    await session.rollback()
    return ids


async def claim_for_processing(session, upload_id: str) -> Optional[CleanItem]:
    """clean → processing（`process_attempts+1`、清掉上次的延後標記）。commit；別人先動了回 None。"""
    row = await _claim(session, upload_id, uploads.STATE_CLEAN, uploads.STATE_PROCESSING,
                       ", process_attempts = process_attempts + 1, failure_kind = NULL, failure_detail = NULL")
    if row is None:
        return None
    upload_id, file_hash, name, size, mtime, _scan, _err, attempts = row
    return CleanItem(upload_id, file_hash, name, int(size), mtime, int(attempts))


async def corpus_has(session, file_hash: str) -> bool:
    return (await session.execute(text("SELECT 1 FROM research.research_report WHERE file_hash = :h LIMIT 1"),
                                  {"h": file_hash})).first() is not None


async def finish(session, upload_id: str, state: str, *, failure_kind: Optional[str] = None,
                 detail: Optional[str] = None) -> bool:
    """processing → 終點（failed／duplicate／blocked）。commit；回是否真的轉了。"""
    if state not in (uploads.STATE_FAILED, uploads.STATE_DUPLICATE, uploads.STATE_BLOCKED):
        raise ValueError(f"finish：不支援的狀態 {state!r}")
    purge = ", purge_after = now() + make_interval(days => :days)" if state == uploads.STATE_BLOCKED else ""
    done = (await session.execute(text(
        "UPDATE research.report_upload SET state = :state, failure_kind = :kind, failure_detail = :detail, "
        f"processed_at = now(), state_changed_at = now(){purge} "
        "WHERE id = CAST(:id AS uuid) AND state = :p RETURNING original_name, size_bytes"
    ), {"state": state, "kind": failure_kind, "detail": _clip(detail), "id": upload_id,
        "p": uploads.STATE_PROCESSING, "days": EVIDENCE_RETENTION_DAYS})).first()
    if done and state == uploads.STATE_BLOCKED:
        await _audit(session, "upload.blocked", upload_id, {
            "upload_id": upload_id, "file_name": done[0], "size_bytes": done[1], "state": state,
            "failure_kind": failure_kind,
        })
    await session.commit()
    return done is not None


async def back_to_clean(session, upload_id: str, *, failure_kind: Optional[str] = None,
                        detail: Optional[str] = None) -> None:
    """processing → clean（暫時性：斷路器、LLM 環境、DB）。`process_attempts` 認領時已 +1。commit。"""
    await session.execute(text(
        "UPDATE research.report_upload SET state = :c, failure_kind = :kind, failure_detail = :detail, "
        "state_changed_at = now() WHERE id = CAST(:id AS uuid) AND state = :p"
    ), {"c": uploads.STATE_CLEAN, "kind": failure_kind, "detail": _clip(detail), "id": upload_id,
        "p": uploads.STATE_PROCESSING})
    await session.commit()


async def defer_all_clean(session, *, detail: str) -> int:
    """斷路器有效：所有 `clean` 標 `failure_kind=llm_breaker`（延後，不算失敗、不是終態）。commit。"""
    n = len((await session.execute(text(
        "UPDATE research.report_upload SET failure_kind = :kind, failure_detail = :detail "
        "WHERE state = :c RETURNING id"
    ), {"kind": uploads.FAILURE_LLM_BREAKER, "detail": _clip(detail), "c": uploads.STATE_CLEAN})).all())
    await session.commit()
    return n


class DraftMarkerError(RuntimeError):
    pass


def draft_hook(upload_id: str):
    """`ingest_one` 的 `pre_upsert`：同一個交易裡寫 visibility 草稿列與 `report_upload.state='draft'`。

    - visibility 已有列（例如很久以前被隱藏過、語料後來被刪的同 hash）：只在 `published_at IS NULL` 時改成草稿，
      `hidden` 保留；曾經發布過的拒絕（拋例外 → `ingest_one` rollback → 上傳轉 failed）——發布過的研報不能
      再被當成草稿，日後也就不會被清除。
    - `report_upload` 必須仍是 `processing` 且 hash 相同，否則拋例外（同樣整筆 rollback）。
    hook 自己不 commit（由 `upsert_report` 那次 commit 一起落庫）。
    """

    async def hook(session, report) -> None:
        vis = (await session.execute(text(
            "INSERT INTO research.report_visibility (file_hash, hidden, publication) "
            "VALUES (:h, false, 'draft') "
            "ON CONFLICT (file_hash) DO UPDATE SET publication = 'draft', updated_at = now() "
            "WHERE research.report_visibility.published_at IS NULL "
            "RETURNING file_hash"
        ), {"h": report.file_hash})).first()
        if vis is None:
            raise DraftMarkerError("這個 file_hash 在 report_visibility 有發布紀錄，不能當成草稿入庫")
        up = (await session.execute(text(
            "UPDATE research.report_upload SET state = :draft, processed_at = now(), state_changed_at = now(), "
            "failure_kind = NULL, failure_detail = NULL "
            "WHERE id = CAST(:id AS uuid) AND state = :p AND file_hash = :h RETURNING id"
        ), {"draft": uploads.STATE_DRAFT, "id": upload_id, "p": uploads.STATE_PROCESSING,
            "h": report.file_hash})).first()
        if up is None:
            raise DraftMarkerError("上傳紀錄已不是 processing（或 file_hash 不符），不寫草稿")

    return hook


class HashMismatchError(RuntimeError):
    pass


def move_to_clean(src: Path, dst: Path, expected_sha: str, client_mtime: Optional[datetime]) -> None:
    """隔離區 → 乾淨檔（搬正）：搬之前與之後各重算一次 SHA-256（防 TOCTOU），再以 `client_mtime` 設回 mtime
    （`ingest_one` 以 mtime 補 report_date）。同檔案系統用 `os.replace`（原子）；跨檔案系統退回複製＋fsync＋驗證。
    搬之後才發現不符時刪掉目的檔（它不是那份通過掃描的內容）。"""
    if sha256_file(src) != expected_sha:
        raise HashMismatchError("搬正前的 SHA-256 與 DB 不符")
    os.makedirs(dst.parent, mode=CLEAN_DIR_MODE, exist_ok=True)
    try:
        os.replace(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        tmp = dst.with_name(f".{uuid.uuid4().hex}.part")
        try:
            with open(src, "rb") as fin, open(tmp, "xb") as fout:
                shutil.copyfileobj(fin, fout, 1024 * 1024)
                fout.flush()
                os.fsync(fout.fileno())
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                tmp.unlink()
        os.unlink(src)
    if sha256_file(dst) != expected_sha:
        dst.unlink(missing_ok=True)
        raise HashMismatchError("搬正後的 SHA-256 與 DB 不符")
    os.chmod(dst, CLEAN_FILE_MODE)
    if client_mtime is not None:
        ts = client_mtime.timestamp()
        os.utime(dst, (ts, ts))


def remove_quiet(path: Path) -> bool:
    """刪檔（或整個目錄）；不存在算成功、其他錯誤只印 WARNING。回是否刪了東西。"""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        _say(f"WARNING 刪除 {path} 失敗：{exc!r}")
        return False


async def corpus_uses_dir(session, directory: Path) -> bool:
    """語料裡有沒有研報的 file_path 指向這個目錄（重複件只在沒人用時才清本機乾淨檔）。"""
    prefix = str(directory).rstrip("/") + "/"
    return (await session.execute(text(
        "SELECT 1 FROM research.research_report WHERE left(file_path, :n) = :p LIMIT 1"
    ), {"n": len(prefix), "p": prefix})).first() is not None


# ── 清除 ────────────────────────────────────────────────────────────────


@dataclass
class PurgeStats:
    purged: int = 0
    corpus_purged: int = 0
    deferred: int = 0  # 守門成立但拿不到語料鎖，留到下一輪
    evidence: int = 0
    orphans: int = 0
    file_errors: int = 0
    notes: list[str] = field(default_factory=list)


async def purge_rejected(
    session_factory,
    *,
    qroot: Path,
    clean_dir: Path,
    tags_dir: Path,
    cache_dir: Path,
    storage,
    corpus_lock_held: bool,
    limit: int = PURGE_BATCH,
) -> PurgeStats:
    """清除寬限期已過的退回件（見模組 docstring「清除」）。`corpus_lock_held=False` 時語料可清的那些留到下一輪。"""
    from app.services.object_storage import original_object_key

    st = PurgeStats()
    guard = corpus_purgeable_sql("u.file_hash")
    async with session_factory() as session:
        candidates = await list_purgeable(session, limit=limit)
        await session.rollback()
        for cand in candidates:
            if cand.corpus_purgeable and not corpus_lock_held:
                st.deferred += 1
                continue
            claimed = (await session.execute(text(
                "UPDATE research.report_upload SET purged_at = now() "
                "WHERE id = CAST(:id AS uuid) AND state = :rejected AND purged_at IS NULL AND purge_after <= now() "
                "RETURNING file_hash, original_name, size_bytes"
            ), {"id": cand.upload_id, "rejected": uploads.STATE_REJECTED})).first()
            if claimed is None:
                await session.rollback()
                continue
            file_hash, name, size = claimed
            corpus = False
            object_keys: list[str] = []
            if corpus_lock_held:
                ok = (await session.execute(text(
                    f"SELECT {guard} FROM research.report_upload u WHERE u.id = CAST(:id AS uuid)"
                ), {"id": cand.upload_id})).scalar()
                if ok:
                    corpus = True
                    params = {"id": cand.upload_id}
                    deleted = (await session.execute(text(
                        "DELETE FROM research.research_report r USING research.report_upload u "
                        f"WHERE u.id = CAST(:id AS uuid) AND r.file_hash = u.file_hash AND {guard} "
                        "RETURNING r.file_name, r.source_object_key"
                    ), params)).all()
                    await session.execute(text(
                        "DELETE FROM research.report_visibility v USING research.report_upload u "
                        "WHERE u.id = CAST(:id AS uuid) AND v.file_hash = u.file_hash "
                        f"AND v.publication = 'draft' AND v.published_at IS NULL AND {guard}"
                    ), params)
                    await session.execute(text(
                        "DELETE FROM research.extraction_log e USING research.report_upload u "
                        f"WHERE u.id = CAST(:id AS uuid) AND e.file_hash = u.file_hash AND {guard}"
                    ), params)
                    # R2 物件鍵一律由 hash＋副檔名重新推導，不信任 DB 值（與收檔、presign 同一條規則）。
                    for fname in {d[0] for d in deleted} | {name}:
                        try:
                            object_keys.append(original_object_key(file_hash, fname))
                        except ValueError:
                            pass
            await _audit(session, "upload.purge", cand.upload_id, {
                "upload_id": cand.upload_id, "file_name": name, "size_bytes": size, "state": uploads.STATE_REJECTED,
                "corpus_purged": corpus,
            })
            await session.commit()
            st.purged += 1
            # ── commit 之後才刪檔 ──
            remove_quiet(quarantine.bin_path(qroot, cand.upload_id))
            if corpus:
                st.corpus_purged += 1
                remove_quiet(tags_dir / f"{file_hash}.json")
                remove_quiet(cache_dir / f"{file_hash}.json")
                remove_quiet(clean_dir / file_hash)
                if storage is not None and getattr(storage, "enabled", False):
                    for key in sorted(set(object_keys)):
                        try:
                            storage.delete(key)
                        except Exception as exc:  # noqa: BLE001 — 留給每週 R2 對帳報 orphan
                            st.file_errors += 1
                            _say(f"WARNING 刪除 R2 物件 {key} 失敗（每週對帳會列為 orphan）：{exc!r}")
    return st


async def purge_evidence(session_factory, *, qroot: Path, limit: int = PURGE_BATCH) -> int:
    """感染（與攔截）證據過了保留期：刪檔、設 `purged_at`、稽核 `upload.evidence_purged`。DB metadata 永久保留。"""
    n = 0
    async with session_factory() as session:
        rows = (await session.execute(text(
            "SELECT id::text FROM research.report_upload WHERE state IN (:i, :b) AND purged_at IS NULL "
            "AND purge_after <= now() ORDER BY purge_after, id LIMIT :n"
        ), {"i": uploads.STATE_INFECTED, "b": uploads.STATE_BLOCKED, "n": limit})).all()
        await session.rollback()
        for (upload_id,) in rows:
            done = (await session.execute(text(
                "UPDATE research.report_upload SET purged_at = now() "
                "WHERE id = CAST(:id AS uuid) AND state IN (:i, :b) AND purged_at IS NULL AND purge_after <= now() "
                "RETURNING original_name, size_bytes, state"
            ), {"id": upload_id, "i": uploads.STATE_INFECTED, "b": uploads.STATE_BLOCKED})).first()
            if done is None:
                await session.rollback()
                continue
            await _audit(session, "upload.evidence_purged", upload_id, {
                "upload_id": upload_id, "file_name": done[0], "size_bytes": done[1], "state": done[2],
            })
            await session.commit()
            for path in (infected_file(qroot, upload_id), quarantine.bin_path(qroot, upload_id)):
                if path.exists():
                    try:
                        os.chmod(path, 0o600)  # 0400 的檔案在某些檔案系統上刪不掉；先收回寫入權
                    except OSError:
                        pass
                    remove_quiet(path)
            n += 1
    return n


def _orphan_candidates(qroot: Path, now: float) -> list[tuple[Path, Optional[str]]]:
    out = []
    for directory, suffix in ((qroot, quarantine.BIN_SUFFIX),
                              (qroot / quarantine.INCOMING_DIRNAME, quarantine.PART_SUFFIX),
                              (qroot / quarantine.INFECTED_DIRNAME, quarantine.BIN_SUFFIX)):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                info = path.lstat()
            except OSError:
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            if now - info.st_mtime < ORPHAN_MIN_AGE_SECONDS:
                continue
            stem = path.name[: -len(suffix)] if path.name.endswith(suffix) else None
            try:
                upload_id = str(uuid.UUID(stem)) if stem else None
            except ValueError:
                upload_id = None
            out.append((path, upload_id))
    return out


async def purge_orphans(session_factory, *, qroot: Path, now: Optional[float] = None) -> int:
    """隔離區（含 incoming/、infected/）裡沒有對應 DB 列、而且超過 1 小時的檔案刪掉。檔名不是 upload_id 的也算。"""
    import time

    cands = _orphan_candidates(qroot, time.time() if now is None else now)
    if not cands:
        return 0
    ids = sorted({u for _, u in cands if u})
    known: set[str] = set()
    if ids:
        async with session_factory() as session:
            known = {r[0] for r in (await session.execute(text(
                "SELECT id::text FROM research.report_upload WHERE id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": ids})).all()}
            await session.rollback()
    n = 0
    for path, upload_id in cands:
        if upload_id is not None and upload_id in known:
            continue
        if remove_quiet(path):
            n += 1
    return n
