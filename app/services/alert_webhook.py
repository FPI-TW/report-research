"""選配的通用 webhook 告警：批次失敗、LLM 批次斷路器觸發、上傳積壓。

**預設關**：沒設 `ALERT_WEBHOOK_URL` 就什麼都不送。

給沒有 Prometheus Alertmanager、只想「出事時往聊天室或工單系統丟一則」的部署方。與廷豐既有的
`REPORT_MARK_ALERT_WEBHOOK`（Slack，`deploy/systemd/report-mark-alert.sh`、`scripts/incident_handler.sh`、
上傳 worker 的感染通知）**並存、互不取代**：兩個變數各管各的，設了哪個就送哪個。

## 事件（`EVENTS`）與觸發點

| 事件 | 觸發點 |
|---|---|
| `batch_failed` | 容器排程的每個工作經 `deploy/container/run_job.py` 包一層，非零退出（撞鎖的 75 除外）就送 |
| `llm_breaker_tripped` | 批次斷路器跳脫、寫下 `data/.llm_breaker` 的那一刻（`scripts/_claude_cli.py` 的 `_Breaker`）|
| `upload_backlog` | web 定期查（`ALERT_CHECK_INTERVAL_SECONDS`）：最舊一筆處理中的上傳超過門檻分鐘數 |

同一個事件（事件＋鍵，例如 `batch_failed:backup`）在 `ALERT_WEBHOOK_COOLDOWN_MINUTES` 內只送一次（狀態檔
`data/.alert_webhook_state.json`，各行程共用、flock 互斥）。只送「出事」，不送「恢復」：恢復與趨勢看 `/metrics`。

## 送出

`POST` JSON（`Content-Type: application/json`）：`source`、`event`、`severity`、`summary`、`details`、`host`、`at`，
另帶 `text`（一行可讀的摘要）——Slack／Teams／Mattermost／Google Chat 的 incoming webhook 都認 `text`，
不必另寫轉接。`details` 只有計數、時間與工作名，**不帶日誌內容、檔名、問答或任何使用者資料**。

URL 是祕密：只從環境變數讀（不進 `Settings`、不進 argv——這裡在行程內用 httpx 送，不經 curl），任何日誌與回傳值
都不含 URL；失敗只說類別（`failed:http_502`、`failed:ConnectTimeout`）。送不出去一律 fail-open：告警不能讓批次或
web 失敗。這是**執行期外連**，寫在 `docs/CONTAINER_DEPLOYMENT.md` 的零外連清單。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

URL_ENV = "ALERT_WEBHOOK_URL"
STATE_FILE_ENV = "ALERT_WEBHOOK_STATE_FILE"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_FILE = REPO_ROOT / "data" / ".alert_webhook_state.json"
TIMEOUT_S = 5.0
SOURCE = "report-mark"

EVENT_BATCH_FAILED = "batch_failed"
EVENT_LLM_BREAKER = "llm_breaker_tripped"
EVENT_UPLOAD_BACKLOG = "upload_backlog"
EVENTS = (EVENT_BATCH_FAILED, EVENT_LLM_BREAKER, EVENT_UPLOAD_BACKLOG)
SEVERITIES = ("warning", "critical")

_URL_RE = re.compile(r"^https?://[^\s\"\\]+$")
_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}$")


def webhook_url() -> str:
    return (os.environ.get(URL_ENV) or "").strip()


def enabled() -> bool:
    return bool(webhook_url())


def state_path() -> Path:
    return Path(os.environ.get(STATE_FILE_ENV) or DEFAULT_STATE_FILE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def payload(event: str, summary: str, *, severity: str, details: dict | None, now: datetime) -> dict:
    return {
        "source": SOURCE,
        "event": event,
        "severity": severity,
        "summary": summary,
        "text": f"[{SOURCE}] {event}：{summary}",
        "details": details or {},
        "host": socket.gethostname(),
        "at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


@contextlib.contextmanager
def _locked_state(path: Path):
    """讀寫冷卻狀態（flock 互斥）。開不了檔就給一份空的、寫回時忽略——冷卻失效最多是多送一次。"""
    import fcntl

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
    except OSError:
        yield {}
        return
    with fh:
        state: dict = {}
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            loaded = json.loads(fh.read() or "{}")
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            state = {}
        yield state
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(state, ensure_ascii=False, sort_keys=True))
            fh.flush()
        except OSError:
            logger.warning("webhook 冷卻狀態檔寫入失敗（%s）", path)


def _in_cooldown(state: dict, slot: str, now: datetime, cooldown_s: float) -> bool:
    last = state.get(slot)
    if not isinstance(last, (int, float)):
        return False
    return 0 <= now.timestamp() - float(last) < cooldown_s


def _post(url: str, body: dict, transport=None) -> str:
    import httpx

    try:
        with httpx.Client(timeout=TIMEOUT_S, transport=transport) as client:
            resp = client.post(url, json=body)
    except Exception as exc:  # noqa: BLE001 — 例外字串可能含 URL，只回類別
        return f"failed:{type(exc).__name__}"
    if 200 <= resp.status_code < 300:
        return "sent"
    return f"failed:http_{resp.status_code}"


def notify(event: str, summary: str, *, key: str = "", severity: str = "warning", details: dict | None = None,
           now: datetime | None = None, transport=None, cooldown_minutes: int | None = None) -> str:
    """送一則告警。回 `disabled`／`bad_url`／`cooldown`／`sent`／`failed:<類別>`；永不拋例外。"""
    try:
        url = webhook_url()
        if not url:
            return "disabled"
        if not _URL_RE.match(url):
            logger.error("%s 不是合法 URL（須以 http:// 或 https:// 開頭、不含空白與引號），未送出", URL_ENV)
            return "bad_url"
        if event not in EVENTS:
            raise ValueError(f"未知的告警事件：{event}")
        if severity not in SEVERITIES:
            severity = "warning"
        if not _KEY_RE.match(key):
            key = ""
        now = now or _now()
        if cooldown_minutes is None:
            from app.config import get_settings

            cooldown_minutes = get_settings().alert_webhook_cooldown_minutes
        slot = f"{event}:{key}"
        with _locked_state(state_path()) as state:
            if _in_cooldown(state, slot, now, cooldown_minutes * 60.0):
                return "cooldown"
            result = _post(url, payload(event, summary, severity=severity, details=details, now=now), transport)
            if result == "sent":
                state[slot] = now.timestamp()
        if result != "sent":
            logger.warning("webhook 告警 %s 未送達：%s", slot, result)
        return result
    except Exception as exc:  # noqa: BLE001 — 告警不能讓呼叫端失敗
        logger.warning("webhook 告警失敗（%s）", type(exc).__name__)
        return f"failed:{type(exc).__name__}"


# ── 上傳積壓的週期檢查（web 行程） ─────────────────────────────────────────


async def check_upload_backlog(session_factory, *, threshold_minutes: int, now: datetime | None = None,
                               transport=None) -> str:
    """查一次上傳佇列；最舊一筆處理中的上傳超過門檻就送 `upload_backlog`。回 `ok`／notify 的結果。"""
    from app.services import monitoring_signals, uploads

    async with session_factory() as session:
        q = await monitoring_signals.upload_queue(session)
    oldest = q["oldest_in_flight_age_s"]
    if oldest is None or oldest < threshold_minutes * 60:
        return "ok"
    in_flight = sum(q["counts"][s] for s in uploads.IN_FLIGHT_STATES)
    details: dict[str, Any] = {"in_flight": in_flight, "oldest_minutes": int(oldest // 60),
                               "threshold_minutes": threshold_minutes,
                               "by_state": {s: q["counts"][s] for s in uploads.IN_FLIGHT_STATES}}
    summary = (f"研報上傳積壓：{in_flight} 筆處理中，最舊一筆已等 {int(oldest // 60)} 分鐘"
               f"（門檻 {threshold_minutes} 分）")
    return await asyncio.to_thread(notify, EVENT_UPLOAD_BACKLOG, summary, details=details, now=now,
                                   transport=transport)


async def _watch(interval_s: float, threshold_minutes: int) -> None:
    from app.services import db

    while True:
        await asyncio.sleep(interval_s)
        try:
            await check_upload_backlog(db.SessionFactory, threshold_minutes=threshold_minutes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — DB 暫時不可用：下一輪再查
            logger.warning("webhook 上傳積壓檢查失敗（%s）", type(exc).__name__)


def start_watch() -> asyncio.Task | None:
    """web lifespan 呼叫：設了 URL 且門檻 > 0 才啟動背景檢查，回 task（關機時取消）。"""
    from app.config import get_settings

    s = get_settings()
    if not enabled() or s.alert_upload_backlog_minutes <= 0:
        return None
    logger.warning("通用 webhook 告警已啟用：上傳積壓每 %d 秒檢查一次（門檻 %d 分）",
                   s.alert_check_interval_seconds, s.alert_upload_backlog_minutes)
    return asyncio.create_task(_watch(float(s.alert_check_interval_seconds), s.alert_upload_backlog_minutes))
