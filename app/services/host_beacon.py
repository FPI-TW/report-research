"""主機信標：一台主機把「自己的服務狀態」與「sync 來源段的結果」發布到共用的 R2，另一台讀來監看。

## 為什麼需要它

正式環境（EC2）碰不到 NAS，新研報全靠辦公室主機的 sync 每輪 rsync 之後推到 R2 的 ``inbox/``
（``scripts/r2_inbox.py``）。那台主機停機、WSL 被收掉、sync 的 timer 停了、NAS 掛不上、推送失敗——
EC2 這邊看到的都一樣：inbox 沒有新檔，自己的 sync 每 3 小時照樣 rc=0、心跳照樣更新，後台一片綠。
「來源斷了」與「這幾天沒有新稿」在 EC2 上無從分辨，而辦公室主機自己的告警在它整台停掉時一則也送不出來。

所以由來源那台**主動發布**一份小 JSON（``beacons/<名稱>.json``，每次覆寫），EC2 讀它：

- 信標太久沒更新 ⇒ 那台主機失聯（停機、排程器死了、連不到 R2——連不到 R2 時 inbox 也推不上來，同一件事）。
- 信標有在更新 ⇒ 照裡面的內容判斷來源段：sync 有沒有在跑、NAS 掛載與 rsync 成不成功、inbox 推送成不成功。

不另開網路通道：兩台主機本來就共用這個 bucket，被監看的資料流走的也是它。

## 兩端

- **發布端**（``scripts/publish_host_beacon.py``，``report-mark-beacon.timer`` 每 10 分鐘）：``collect_sync`` 讀
  sync 殼寫在 ``data/`` 的幾個 key=value 狀態檔（逐鍵解析，不 source、不 eval），``project_services`` 把維運代理
  ``list`` 的結果縮成每個服務一列，``build`` 組成信標。``HOST_BEACON_NAME`` 沒設就不發布。
- **監看端**（web：``/healthz/feed`` 與 ``GET /api/admin/ops/remote-hosts``）：``read`` 取回並 ``parse``
  （信標來自 bucket，**不是可信輸入**：大小上限、版本、每個欄位逐一驗型別與範圍，不合的丟掉或整份拒收），
  再 ``evaluate``。``HOST_BEACON_WATCH`` 沒設就是 ``disabled``。

## 時間一律不信任發布端的時鐘

「信標多舊」用 R2 蓋的 ``LastModified`` 對監看端的現在時刻算，不用信標自報的時間（WSL 休眠醒來後時鐘會落後）。
信標裡的事件（來源段結果、輪次開始）只取它們與 ``published_epoch`` 的**差**——同一個時鐘量出來的間隔——
再加上信標的年齡；顯示用的時間同樣換算到監看端的時間軸。

## 狀態詞彙（封閉；``/healthz/feed`` 只回這一個字）

``ok``、``disabled``（沒設定要監看）、``unknown``（R2 讀不到：那是物件儲存探針的事，這裡不開也不關事件）之外都是告警：

- ``missing``：bucket 裡沒有這個信標（發布端從沒跑過，或名稱對不上）。
- ``invalid``：讀得到但不是看得懂的信標（版本不符、格式壞掉）。
- ``stale``：信標超過 ``HOST_BEACON_STALE_MINUTES`` 沒更新——那台主機失聯。此時裡面的內容都是舊聞，不再往下判。
- ``timer_inactive``：sync 的 timer 不在 active。
- ``sync_stalled``：來源段超過 ``HOST_BEACON_FEED_STALE_HOURS`` 沒有新結果（sync 每 3 小時一輪；沒有結果檔也算）。
- ``source_failed``：最近一輪 NAS 掛載或 rsync 失敗。
- ``push_disabled``：來源是 NAS 卻沒開 ``SYNC_INBOX_PUSH``——本機照常入庫，但什麼都不會推給另一台。
- ``push_failed``：最近一輪 inbox 推送失敗，或還有沒補傳的 delta（``data/inbox_delta_retained_*.txt``）。
- ``round_aborted``：最近一輪被中止（SIGTERM、SIGKILL、斷電）。rsync 可能已把部分新檔落到本地，而
  ``--size-only`` 讓下一輪 delta 不再列出它們——那批既不會入庫也不會被推送。

多項同時成立時 ``state`` 是 ``ALERT_STATES`` 裡最前面那一個，``reasons`` 列出全部。來源不是 NAS 的主機
（``SYNC_SOURCE=r2-inbox``）只判 ``stale``：它沒有來源段可言。

**匯入與下游批次的失敗不在這裡判**：那是那台主機自己的事（它有自己的 P5 與 freshness），不影響推給另一台的檔。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from app.services.object_storage import ObjectNotFound, ObjectStorage, ObjectStorageError
from app.services.ops_topology import probe_service_health, service_health

SCHEMA_VERSION = 1
KEY_PREFIX = "beacons/"
# 一份信標約 10 KiB（50 個服務）。上限放寬到 256 KiB，超過就拒收——不把 bucket 裡的任意物件整份讀進記憶體。
MAX_BYTES = 256 * 1024
MAX_SERVICES = 200
SYNC_TIMER_UNIT = "report-mark-sync.timer"

STATE_OK = "ok"
STATE_DISABLED = "disabled"
STATE_UNKNOWN = "unknown"
# 順序＝優先序：多項同時成立時 state 取最前面那一個。
ALERT_STATES = (
    "missing", "invalid", "stale", "timer_inactive", "sync_stalled", "source_failed", "push_disabled", "push_failed",
    "round_aborted",
)
STATES = (STATE_OK, STATE_DISABLED, STATE_UNKNOWN, *ALERT_STATES)

SOURCES = ("nas", "r2-inbox")
ROUND_STATES = ("running", "done", "failed", "signalled", "aborted", "unknown")
FEED_RESULTS = ("ok", "mount_failed", "rsync_failed")
PUSH_RESULTS = ("off", "none", "ok", "failed")
_SUMMARIES = ("running", "idle", "failed", "transitioning", "not_found", "unknown")
_HEALTHS = ("ok", "degraded", "down", "unknown")
_KINDS = ("systemd", "container")
_TIERS = ("critical", "important", "supporting")

_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")
_ROUND_ID = re.compile(r"[0-9]{8}_[0-9]{6}")
_MAX_EPOCH = 1 << 40
_MAX_TEXT = 200


class BeaconError(ValueError):
    """信標不是看得懂的格式（版本不符、不是 JSON、必要欄位缺或型別不對）。"""


def object_key(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise ValueError(f"不合法的信標名稱：{name!r}")
    return f"{KEY_PREFIX}{name}.json"


# ── 發布端 ───────────────────────────────────────────────────────────────


def read_kv(path: Path) -> dict[str, str]:
    """sync 殼寫的 key=value 檔 → dict。讀不到就是空的；重複的鍵以最後一個為準（與殼那側的讀法一致）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            out[key.strip()] = value.strip()
    return out


def _epoch(value) -> int | None:
    """非負整數的 epoch 秒；其餘（空字串、負數、bool、太大）一律 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isascii() or not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 <= value < _MAX_EPOCH:
        return None
    return value


def _iso_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int(ts.timestamp()) if ts.tzinfo is not None else None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # EPERM：行程在，只是不是我們的
        return True
    return True


def _round(state: Mapping[str, str], alive) -> dict:
    """``data/sync_round_state`` → 輪次。running 而行程已不在、或收到訊號收場的，都是 aborted。"""
    raw = state.get("state", "")
    status = raw if raw in ("running", "done", "failed", "signalled") else "unknown"
    pid = _epoch(state.get("pid", ""))
    if status == "signalled" or (status == "running" and pid is not None and not alive(pid)):
        status = "aborted"
    rc = state.get("rc", "")
    return {
        "state": status,
        "started_epoch": _epoch(state.get("started_epoch", "")),
        "updated_epoch": _iso_epoch(state.get("updated_at")),
        "rc": int(rc) if rc.isascii() and rc.isdigit() and len(rc) <= 3 else None,
    }


def _feed(state: Mapping[str, str]) -> dict | None:
    """``data/.sync_feed_state`` → 來源段結果。沒有可用的 epoch 就當作沒有這個檔（監看端判 sync_stalled）。"""
    epoch = _epoch(state.get("epoch", ""))
    if epoch is None:
        return None
    result = state.get("result", "")
    push = state.get("push", "")
    round_id = state.get("round", "")
    return {
        "round": round_id if _ROUND_ID.fullmatch(round_id) else None,
        "epoch": epoch,
        # 認不得的值不猜成 ok：result 當成 rsync 失敗、push 當成失敗（寧可多抓）。
        "result": result if result in FEED_RESULTS else "rsync_failed",
        "new_files": _epoch(state.get("new_files", "")) or 0,
        "push": push if push in PUSH_RESULTS else "failed",
    }


def collect_sync(data_dir: Path, env: Mapping[str, str], *, alive=pid_alive) -> dict:
    """讀 sync 殼留在 ``data/`` 的狀態檔。每一項都可能不存在（sync 還沒跑過、舊版的殼）——那就是 None。"""
    source = (env.get("SYNC_SOURCE") or "nas").strip()
    try:
        pending = sum(1 for _ in data_dir.glob("inbox_delta_retained_*.txt"))
    except OSError:
        pending = 0
    return {
        "source": source if source in SOURCES else "nas",
        "inbox_push": (env.get("SYNC_INBOX_PUSH") or "0").strip() == "1",
        "timer_active": None,
        "round": _round(read_kv(data_dir / "sync_round_state"), alive),
        "feed": _feed(read_kv(data_dir / ".sync_feed_state")),
        "heartbeat_epoch": _epoch(read_kv(data_dir / ".last_successful_sync").get("epoch", "")),
        "pending_replays": pending,
    }


def _last_run(item: Mapping) -> str | None:
    systemd = item.get("systemd") or {}
    container = item.get("container") or {}
    return (systemd.get("exec_main_exit_at") or systemd.get("exec_main_start_at") or systemd.get("active_enter_at")
            or container.get("started_at"))


def project_services(listing: Mapping) -> list[dict]:
    """維運代理 ``list`` 的結果 → 每個服務一列（名稱、層級、狀態與判讀）。判讀與依賴圖一致：探針以它所探測的外部依賴
    認得的退出碼結束時算 ok（``probe_service_health``），其餘照 ``service_health``。判讀在發布端做，EC2 只顯示。"""
    externals = [e for e in listing.get("externals") or [] if isinstance(e, dict) and e.get("name")]
    out: list[dict] = []
    for item in listing.get("items") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        health, reason = probe_service_health(item, externals) or service_health(item)
        timer = item.get("timer_state") if item.get("timer") else None
        systemd = item.get("systemd") or {}
        out.append({
            "name": item["name"],
            "kind": item.get("kind"),
            "tier": item.get("tier"),
            "description": item.get("description") or "",
            "summary": item.get("summary"),
            "health": health,
            "health_reason": reason,
            "timer": item.get("timer"),
            "timer_active": None if not item.get("timer") else (timer or {}).get("active_state") == "active",
            "last_run_at": _last_run(item),
            "next_run_at": (timer or {}).get("next_elapse_at"),
            "exit_status": systemd.get("exec_main_status") if systemd.get("exec_main_code") == "exited" else None,
        })
    return out


def sync_timer_active(services: list[dict] | None) -> bool | None:
    for svc in services or []:
        if svc.get("timer") == SYNC_TIMER_UNIT:
            return svc.get("timer_active")
    return None


def build(*, name: str, host: str, published_epoch: int, sync: dict, services: list[dict] | None,
          services_error: str | None) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "name": name,
        "host": host[:_MAX_TEXT],
        "published_epoch": published_epoch,
        "sync": {**sync, "timer_active": sync_timer_active(services)},
        "services": services,
        "services_error": services_error[:_MAX_TEXT] if services_error else None,
    }


def encode(beacon: dict) -> bytes:
    data = json.dumps(beacon, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise BeaconError(f"信標 {len(data)} bytes，超過上限 {MAX_BYTES}")
    return data


# ── 監看端 ───────────────────────────────────────────────────────────────


def _text(value, limit: int = _MAX_TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _choice(value, allowed: tuple[str, ...], default: str | None = None) -> str | None:
    return value if isinstance(value, str) and value in allowed else default


def _flag(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < _MAX_EPOCH else 0


def _exit_code(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 256 else None


def _parse_service(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not _NAME.fullmatch(raw["name"]):
        return None
    return {
        "name": raw["name"],
        "kind": _choice(raw.get("kind"), _KINDS, "systemd"),
        "tier": _choice(raw.get("tier"), _TIERS, "supporting"),
        "description": _text(raw.get("description")),
        "summary": _choice(raw.get("summary"), _SUMMARIES, "unknown"),
        "health": _choice(raw.get("health"), _HEALTHS, "unknown"),
        "health_reason": _text(raw.get("health_reason")),
        "timer": _text(raw.get("timer")) or None,
        "timer_active": _flag(raw.get("timer_active")),
        "last_run_at": _text(raw.get("last_run_at"), 40) or None,
        "next_run_at": _text(raw.get("next_run_at"), 40) or None,
        "exit_status": _exit_code(raw.get("exit_status")),
    }


def _parse_sync(raw) -> dict:
    if not isinstance(raw, dict):
        raise BeaconError("sync 不是物件")
    round_raw = raw.get("round")
    if not isinstance(round_raw, dict):
        round_raw = {}
    feed_raw = raw.get("feed")
    feed = None
    if isinstance(feed_raw, dict) and _epoch(feed_raw.get("epoch")) is not None:
        round_id = feed_raw.get("round")
        feed = {
            "round": round_id if isinstance(round_id, str) and _ROUND_ID.fullmatch(round_id) else None,
            "epoch": _epoch(feed_raw.get("epoch")),
            "result": _choice(feed_raw.get("result"), FEED_RESULTS, "rsync_failed"),
            "new_files": _count(feed_raw.get("new_files")),
            "push": _choice(feed_raw.get("push"), PUSH_RESULTS, "failed"),
        }
    return {
        "source": _choice(raw.get("source"), SOURCES, "nas"),
        "inbox_push": raw.get("inbox_push") is True,
        "timer_active": _flag(raw.get("timer_active")),
        "round": {
            "state": _choice(round_raw.get("state"), ROUND_STATES, "unknown"),
            "started_epoch": _epoch(round_raw.get("started_epoch")),
            "updated_epoch": _epoch(round_raw.get("updated_epoch")),
            "rc": _exit_code(round_raw.get("rc")),
        },
        "feed": feed,
        "heartbeat_epoch": _epoch(raw.get("heartbeat_epoch")),
        "pending_replays": _count(raw.get("pending_replays")),
    }


def parse(data: bytes) -> dict:
    """bucket 裡的位元組 → 驗過的信標。整份看不懂拋 ``BeaconError``；個別欄位不合的換成安全的預設值。"""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BeaconError("不是合法的 JSON") from exc
    if not isinstance(raw, dict) or raw.get("v") != SCHEMA_VERSION:
        raise BeaconError(f"版本不符（這一端只認 v{SCHEMA_VERSION}）")
    published = _epoch(raw.get("published_epoch"))
    if published is None:
        raise BeaconError("缺 published_epoch")
    services = raw.get("services")
    parsed_services = None
    if isinstance(services, list):
        parsed_services = [s for s in map(_parse_service, services[:MAX_SERVICES]) if s is not None]
    return {
        "v": SCHEMA_VERSION,
        "name": _text(raw.get("name"), 40),
        "host": _text(raw.get("host")),
        "published_epoch": published,
        "sync": _parse_sync(raw.get("sync")),
        "services": parsed_services,
        "services_error": _text(raw.get("services_error")) or None,
    }


def evaluate(beacon: dict, *, age_seconds: float, stale_minutes: int, feed_stale_hours: int) -> list[str]:
    """驗過的信標 → 成立的告警（依 ``ALERT_STATES`` 的順序；空＝正常）。規則見模組 docstring。"""
    if age_seconds > stale_minutes * 60:
        return ["stale"]
    sync = beacon["sync"]
    if sync["source"] != "nas":
        return []
    found: set[str] = set()
    if sync["timer_active"] is False:
        found.add("timer_inactive")
    feed = sync["feed"]
    if feed is None or max(0, beacon["published_epoch"] - feed["epoch"]) + age_seconds > feed_stale_hours * 3600:
        found.add("sync_stalled")
    elif feed["result"] != "ok":
        found.add("source_failed")
    if not sync["inbox_push"]:
        found.add("push_disabled")
    elif sync["pending_replays"] > 0 or (feed is not None and feed["push"] == "failed"):
        found.add("push_failed")
    if sync["round"]["state"] == "aborted":
        found.add("round_aborted")
    return [s for s in ALERT_STATES if s in found]


@dataclass(frozen=True)
class Reading:
    """一次監看的結論。``detail`` 給管理端點；``/healthz/feed`` 只用 ``state``。"""

    name: str
    state: str
    reasons: tuple[str, ...] = ()
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    age_seconds: int | None = None
    beacon: dict | None = None
    error: str | None = None

    def _at(self, epoch: int | None) -> str | None:
        """發布端時鐘的 epoch → 監看端時間軸上的 ISO 時間（只取它與 published_epoch 的差）。"""
        if epoch is None or self.beacon is None or self.published_at is None:
            return None
        return (self.published_at - timedelta(seconds=self.beacon["published_epoch"] - epoch)).isoformat()

    def detail(self, *, stale_minutes: int, feed_stale_hours: int) -> dict:
        out: dict = {
            "name": self.name, "state": self.state, "reasons": list(self.reasons),
            "checked_at": self.checked_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "age_seconds": self.age_seconds, "error": self.error,
            "stale_after_minutes": stale_minutes, "feed_stale_after_hours": feed_stale_hours,
            "host": None, "sync": None, "services": None, "services_error": None,
        }
        if self.beacon is None:
            return out
        sync, feed, rnd = self.beacon["sync"], self.beacon["sync"]["feed"], self.beacon["sync"]["round"]
        out["host"] = self.beacon["host"] or None
        out["sync"] = {
            "source": sync["source"], "inbox_push": sync["inbox_push"], "timer_active": sync["timer_active"],
            "round_state": rnd["state"], "round_started_at": self._at(rnd["started_epoch"]), "round_rc": rnd["rc"],
            "feed_round": feed["round"] if feed else None, "feed_at": self._at(feed["epoch"]) if feed else None,
            "feed_result": feed["result"] if feed else None, "new_files": feed["new_files"] if feed else None,
            "push": feed["push"] if feed else None, "pending_replays": sync["pending_replays"],
            "last_success_at": self._at(sync["heartbeat_epoch"]),
        }
        out["services"] = self.beacon["services"]
        out["services_error"] = self.beacon["services_error"]
        return out


def read(storage: ObjectStorage, name: str, *, stale_minutes: int, feed_stale_hours: int,
         now: datetime | None = None) -> Reading:
    """取回並判讀一個信標（同步、會打 R2；web 要包在執行緒裡）。永不拋例外：讀不到就是 ``unknown``。"""
    now = now or datetime.now(timezone.utc)
    if not name:
        return Reading(name="", state=STATE_DISABLED, checked_at=now)
    if not storage.enabled:
        return Reading(name=name, state=STATE_UNKNOWN, checked_at=now,
                       error="物件儲存未啟用（OBJECT_STORAGE_MODE=local）")
    try:
        data, modified = storage.get_status_object(object_key(name), max_bytes=MAX_BYTES)
    except ObjectNotFound:
        return Reading(name=name, state="missing", reasons=("missing",), checked_at=now)
    except ObjectStorageError as exc:
        return Reading(name=name, state=STATE_UNKNOWN, checked_at=now, error=f"R2 讀取失敗：{type(exc).__name__}")
    age = max(0, int((now - modified).total_seconds()))
    try:
        beacon = parse(data)
    except BeaconError as exc:
        return Reading(name=name, state="invalid", reasons=("invalid",), checked_at=now, published_at=modified,
                       age_seconds=age, error=str(exc))
    reasons = evaluate(beacon, age_seconds=age, stale_minutes=stale_minutes, feed_stale_hours=feed_stale_hours)
    return Reading(name=name, state=reasons[0] if reasons else STATE_OK, reasons=tuple(reasons), checked_at=now,
                   published_at=modified, age_seconds=age, beacon=beacon)
