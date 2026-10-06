"""ClamAV clamd 的最小客戶端：只用標準庫 socket，實作 `PING`、`VERSION`、`zINSTREAM`。

**為什麼自寫、不用 PyPI 的 clamd 套件**：協定只有三個指令、約百行；少一個相依就少一次授權守門
（`tests/test_license_guard.py`）與相依審查。ClamAV 本體（GPL-2.0）跑在獨立容器（`deploy/clamav/`），
這裡只講它的 TCP 協定，不連結它的程式碼。

**這是安全閘門，一律 fail-closed**（不適用「派生功能 fail-open」那條鐵律）：
唯一放行的條件是 `ScanResult.passed`（clamd 明確回 `stream: OK`，而且掃描前 `VERSION` 證明病毒碼夠新）。
其餘一切——連不上、逾時、回應看不懂、病毒碼過舊或日期解析不出來、超過串流上限——都不是 OK。

**錯誤分兩類**（`ScanResult.transient`；上傳 worker 依此決定退回隔離區重試或轉 blocked）：
  暫時性  connection_refused、connection_error、timeout、protocol、clamd_busy、
          signatures_stale、signatures_unknown——clamd 停了、重啟中、重載病毒碼中、病毒碼更新卡住。
          檔案留在隔離區、下一輪再試，**永不放行**。
  決定性  size_limit（clamd 回 `INSTREAM size limit exceeded` 或客戶端自己的上限先到）、
          clamd_error（clamd 對這份內容回了其他 ERROR）。重試同一份檔案只會得到同一個答案。
  **超限一律未通過**：clamd 的預設值會在超過 MaxFileSize／MaxScanSize 時只掃一部分就回 OK，
  所以 `deploy/clamav/conf/clamd.conf` 開了 `AlertExceedsMax`；客戶端這邊另外自己數位元組，
  超過 `clamd_stream_max_bytes` 就不送了，不賭 clamd 怎麼回。

**重載中沒有專屬回應**：`ConcurrentDatabaseReload no` 時，重載期間的新連線會被接受但晾著，
重載完（約 30–60 秒）才處理；超過 `clamd_timeout` 就落在 timeout（暫時性）。

**FOUND 也包含啟發式**：`AlertExceedsMax`／`AlertEncrypted` 命中時簽章名稱以 `Heuristics.` 開頭
（例如 `Heuristics.Limits.Exceeded.MaxFileSize`、`Heuristics.Encrypted.PDF`），`ScanResult.heuristic`
讓呼叫端區分「病毒」與「規則攔截」；兩者都不是 OK。

**病毒碼日期**：`VERSION` 回 `ClamAV 1.4.3/27412/Mon Oct  5 08:30:42 2026`，日期是 clamd 容器的本地時間；
compose 固定 `TZ=Etc/UTC`，這裡一律當 UTC 解讀。格式不符、少了日期、或日期在未來超過一小時（時區被改掉時
會這樣）都視為「判斷不出來」＝ signatures_unknown，不放行。

設定在 `app/config.py`（`CLAMD_HOST`、`CLAMD_PORT`、`CLAMD_TIMEOUT`、`CLAMD_SIGNATURE_MAX_AGE_HOURS`、
`CLAMD_STREAM_MAX_BYTES`）。web 不直接連 clamd（設計：掃描狀態由 DB 推導），呼叫端是上傳 worker 與
`scripts/clamav_smoke.py`。
"""

from __future__ import annotations

import io
import re
import socket
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Literal

CHUNK_SIZE = 64 * 1024
_MAX_REPLY = 64 * 1024          # clamd 的回應都是一行；再長就是協定錯了
_MAX_DETAIL = 500
_FUTURE_SKEW = timedelta(hours=1)

TRANSIENT_KINDS = frozenset({
    "connection_refused", "connection_error", "timeout", "protocol", "clamd_busy",
    "signatures_stale", "signatures_unknown",
})
DETERMINISTIC_KINDS = frozenset({"size_limit", "clamd_error"})

# clamd 對內容以外的原因回 ERROR（資源不足、自己的讀取逾時）：換個時間再送會好，算暫時性。
_BUSY_PATTERNS = ("allocate memory", "too many", "temporary", "timed out", "timeout", "reload")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
_VERSION_RE = re.compile(r"^ClamAV (?P<engine>[^/\s]+)(?:/(?P<db>\d+)/(?P<date>.+))?$")
_CTIME_RE = re.compile(
    r"^[A-Z][a-z]{2}\s+(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+"
    r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\s+(?P<y>\d{4})$"
)


@dataclass(frozen=True)
class ClamdConfig:
    host: str = "127.0.0.1"
    port: int = 3310
    timeout: float = 120.0
    max_signature_age: timedelta = timedelta(hours=72)
    max_stream_bytes: int = 30 * 1024 * 1024

    @classmethod
    def from_settings(cls, settings=None) -> ClamdConfig:
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
        return cls(
            host=settings.clamd_host,
            port=settings.clamd_port,
            timeout=settings.clamd_timeout,
            max_signature_age=timedelta(hours=settings.clamd_signature_max_age_hours),
            max_stream_bytes=settings.clamd_stream_max_bytes,
        )


@dataclass(frozen=True)
class EngineVersion:
    """`VERSION` 的解析結果。解析不出來的欄位是 None（呼叫端一律當「判斷不出來」）。"""

    raw: str
    engine: str | None = None
    db_version: int | None = None
    db_date: datetime | None = None   # UTC aware

    def age(self, now: datetime | None = None) -> timedelta | None:
        if self.db_date is None:
            return None
        return (now or datetime.now(timezone.utc)) - self.db_date

    @property
    def label(self) -> str:
        """寫進 `report_upload.scan_engine` 的字串：`ClamAV 1.4.3/27412/2026-10-05T08:30:42Z`。"""
        if self.engine is None:
            return self.raw[:200]
        date = self.db_date.strftime("%Y-%m-%dT%H:%M:%SZ") if self.db_date else "?"
        db = self.db_version if self.db_version is not None else "?"
        return f"ClamAV {self.engine}/{db}/{date}"


@dataclass(frozen=True)
class ScanResult:
    status: Literal["ok", "found", "error"]
    signature: str | None = None
    kind: str | None = None
    detail: str | None = None
    engine: EngineVersion | None = None

    @property
    def passed(self) -> bool:
        """唯一的放行條件。"""
        return self.status == "ok"

    @property
    def transient(self) -> bool:
        return self.status == "error" and self.kind in TRANSIENT_KINDS

    @property
    def heuristic(self) -> bool:
        return self.status == "found" and bool(self.signature) and self.signature.startswith("Heuristics.")


class ClamdError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail[:_MAX_DETAIL]

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_KINDS


def _error(kind: str, detail: str = "", engine: EngineVersion | None = None) -> ScanResult:
    return ScanResult("error", kind=kind, detail=detail[:_MAX_DETAIL], engine=engine)


def parse_version(raw: str) -> EngineVersion:
    text = raw.strip().rstrip("\0").strip()
    m = _VERSION_RE.match(text)
    if not m:
        return EngineVersion(raw=text)
    db_date = None
    if m.group("date"):
        c = _CTIME_RE.match(" ".join(m.group("date").split()))
        if c and c.group("mon") in _MONTHS:
            try:
                db_date = datetime(int(c.group("y")), _MONTHS[c.group("mon")], int(c.group("day")),
                                   int(c.group("h")), int(c.group("m")), int(c.group("s")), tzinfo=timezone.utc)
            except ValueError:
                db_date = None
    db = int(m.group("db")) if m.group("db") else None
    return EngineVersion(raw=text, engine=m.group("engine"), db_version=db, db_date=db_date)


def check_signatures(engine: EngineVersion, max_age: timedelta, now: datetime | None = None) -> ScanResult | None:
    """病毒碼年齡閘門：夠新回 None；否則回暫時性錯誤（過舊或判斷不出來，都不放行）。"""
    age = engine.age(now)
    if age is None:
        return _error("signatures_unknown", f"VERSION 解析不出病毒碼日期：{engine.raw!r}", engine)
    if age < -_FUTURE_SKEW:
        return _error("signatures_unknown", f"病毒碼日期在未來（時區不是 UTC？）：{engine.raw!r}", engine)
    if age > max_age:
        hours = age.total_seconds() / 3600
        return _error("signatures_stale", f"病毒碼 {hours:.0f} 小時未更新（上限 {max_age}）", engine)
    return None


# ── 傳輸 ────────────────────────────────────────────────────────────────


def _connect(cfg: ClamdConfig) -> socket.socket:
    try:
        sock = socket.create_connection((cfg.host, cfg.port), timeout=cfg.timeout)
    except ConnectionRefusedError as exc:
        raise ClamdError("connection_refused", f"{cfg.host}:{cfg.port} {exc}") from exc
    except TimeoutError as exc:
        raise ClamdError("timeout", f"連線 {cfg.host}:{cfg.port} 逾時") from exc
    except OSError as exc:
        raise ClamdError("connection_error", f"{cfg.host}:{cfg.port} {exc}") from exc
    sock.settimeout(cfg.timeout)
    return sock


def _read_reply(sock: socket.socket) -> str:
    buf = bytearray()
    while b"\0" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_REPLY:
            raise ClamdError("protocol", "回應過長")
    return bytes(buf).split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def _command(cfg: ClamdConfig, command: bytes) -> str:
    sock = _connect(cfg)
    try:
        sock.sendall(b"z" + command + b"\0")
        return _read_reply(sock)
    except TimeoutError as exc:
        raise ClamdError("timeout", f"{command.decode()} 逾時") from exc
    except ClamdError:
        raise
    except OSError as exc:
        raise ClamdError("connection_error", f"{command.decode()}：{exc}") from exc
    finally:
        sock.close()


def ping(config: ClamdConfig | None = None) -> None:
    """clamd 回 PONG 就安靜返回；其他一律拋 ClamdError。"""
    reply = _command(config or ClamdConfig.from_settings(), b"PING")
    if reply != "PONG":
        raise ClamdError("protocol", f"PING 回應不是 PONG：{reply!r}")


def version(config: ClamdConfig | None = None) -> EngineVersion:
    """連線或協定失敗拋 ClamdError；回應格式不符不拋，回欄位為 None 的 EngineVersion。"""
    reply = _command(config or ClamdConfig.from_settings(), b"VERSION")
    if not reply:
        raise ClamdError("protocol", "VERSION 沒有回應")
    return parse_version(reply)


def _classify_reply(reply: str, engine: EngineVersion | None) -> ScanResult:
    if not reply:
        return _error("protocol", "clamd 未回應就關閉連線", engine)
    if "size limit exceeded" in reply.lower():
        return _error("size_limit", reply, engine)
    if reply.endswith(" ERROR"):
        msg = reply.lower()
        kind = "clamd_busy" if any(p in msg for p in _BUSY_PATTERNS) else "clamd_error"
        return _error(kind, reply, engine)
    if reply.startswith("stream: ") and reply.endswith(" FOUND"):
        signature = reply[len("stream: "):-len(" FOUND")].strip()
        if signature:
            return ScanResult("found", signature=signature, engine=engine)
        return _error("protocol", reply, engine)
    if reply == "stream: OK":
        return ScanResult("ok", engine=engine)
    return _error("protocol", f"看不懂的回應：{reply!r}", engine)


def _chunks(data: bytes | BinaryIO):
    stream = io.BytesIO(data) if isinstance(data, (bytes, bytearray, memoryview)) else data
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            return
        yield chunk


def instream(data: bytes | BinaryIO, config: ClamdConfig | None = None,
             *, engine: EngineVersion | None = None) -> ScanResult:
    """以 zINSTREAM 送內容（4 bytes 網路序長度前綴的分塊，0 長度塊收尾）。**不檢查病毒碼年齡**——
    上傳流程請用 `scan()`。網路與協定問題不拋例外，一律回 error 結果。"""
    cfg = config or ClamdConfig.from_settings()
    try:
        sock = _connect(cfg)
    except ClamdError as exc:
        return _error(exc.kind, exc.detail, engine)
    try:
        sent = 0
        try:
            sock.sendall(b"zINSTREAM\0")
            for chunk in _chunks(data):
                sent += len(chunk)
                if sent > cfg.max_stream_bytes:
                    # 客戶端上限先到：不再送、不收尾、不等回應——絕不可能得到 OK。
                    return _error("size_limit", f"超過串流上限 {cfg.max_stream_bytes} bytes", engine)
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
        except TimeoutError:
            return _error("timeout", f"送出 {sent} bytes 時逾時", engine)
        except OSError as exc:
            # clamd 超過 StreamMaxLength 會先寫一行 ERROR 再關連線：送到一半被斷時把那一行讀回來。
            try:
                reply = _read_reply(sock)
            except (OSError, ClamdError):
                reply = ""
            if reply:
                return _classify_reply(reply, engine)
            return _error("connection_error", f"送出 {sent} bytes 時連線中斷：{exc}", engine)
        try:
            reply = _read_reply(sock)
        except TimeoutError:
            return _error("timeout", f"等待掃描結果逾時（{cfg.timeout:g} 秒）", engine)
        except ClamdError as exc:
            return _error(exc.kind, exc.detail, engine)
        except OSError as exc:
            return _error("connection_error", f"讀取掃描結果失敗：{exc}", engine)
        return _classify_reply(reply, engine)
    finally:
        sock.close()


def scan(data: bytes | BinaryIO, config: ClamdConfig | None = None, *, now: datetime | None = None) -> ScanResult:
    """上傳管線的入口：先 `VERSION` 確認病毒碼夠新，再 zINSTREAM。結果帶 `engine` 供寫入 scan_engine。"""
    cfg = config or ClamdConfig.from_settings()
    try:
        engine = version(cfg)
    except ClamdError as exc:
        return _error(exc.kind, exc.detail)
    gate = check_signatures(engine, cfg.max_signature_age, now)
    if gate is not None:
        return gate
    return instream(data, cfg, engine=engine)
