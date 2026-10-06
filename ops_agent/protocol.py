"""協定：一行一個 JSON 的請求／回應，與參數邊界。web 端（`web/ops_client.py`）共用這裡的常數。

請求（UTF-8，一行，結尾 `\\n`，上限 `MAX_REQUEST_BYTES`）：

    {"v": 1, "id": "<呼叫端自訂，≤64 字元，可省略>", "env": "production",
     "op": "list" | "status" | "logs" | "restart" | "run", "service": "<catalog 名稱>", "params": {...},
     "actor": "<web 的使用者名稱，只寫進代理的 journal，可省略>"}

- `env` 必填：呼叫端宣告它要操作哪個環境，與代理載入的 catalog 不同就拒絕（`environment_mismatch`）。
  dev 的 web 誤連到 prod 的 socket（或反之）時在這裡被擋下，而不是默默讀到另一個環境。
- `list` 不帶 `service`；其餘必帶。`logs` 的 `params` 只收 `since`、`lines`；**`restart`／`run` 不收任何參數**
  （帶了就 `invalid_params`）：代理只執行 `systemctl start|restart --no-block -- <catalog 的 unit>`，
  呼叫端沒有任何管道影響 argv。

回應（一行）：

    {"v": 1, "id": ..., "env": "production", "ok": true,  "result": {...}}
    {"v": 1, "id": ..., "env": "production", "ok": false, "error": {"code": "...", "message": "..."}}

`error.code` 是穩定字串（`ERROR_CODES`）；`message` 給人看，不含指令輸出以外的內部細節。

action 與 op 的關係：catalog 每個服務列出允許的 action（`KNOWN_ACTIONS`）；`status`／`list` 需要
`status`，`logs` 需要 `logs`，`restart` 需要 `restart`，`run` 需要 `run`。catalog 寫了未知 action 就拒絕
載入，不是默默忽略。寫入類（`WRITE_ACTIONS`）另有代理端的硬性限制，**catalog 怎麼寫都放不開**：
- `restart` 只認 `RESTARTABLE_UNITS`（v1 只有 Web）；
- 任何寫入類都不碰 `FORBIDDEN_WRITE_TARGET` 命中的對象（PostgreSQL、nginx、cloudflared、docker、代理自己……），
  也不碰容器。
寫入類的互斥、鎖檔試探與執行在 `ops_agent/actions.py`。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

PROTOCOL_VERSION = 1

# 每個環境的 socket 路徑是固定的（不是旋鈕）：dev 與 prod 走不同 socket，代理以此判斷
# 「用 dev socket 載入 prod catalog」這類錯置並拒絕啟動（`catalog.validate_binding`）。
# `app/config.py` 的 `_OPS_SOCKETS` 必須逐字相同（tests/test_admin_ops_api.py 釘住）。
CANONICAL_SOCKETS: dict[str, str] = {
    "production": "/run/report-mark-ops/agent.sock",
    "development": "/run/report-mark-ops-dev/agent.sock",
}
ENVIRONMENTS = tuple(CANONICAL_SOCKETS)

KNOWN_ACTIONS: tuple[str, ...] = ("status", "logs", "restart", "run")
# 會改變主機狀態的 action（與同名 op）。互斥與硬性限制見 ops_agent/actions.py。
WRITE_ACTIONS: tuple[str, ...] = ("restart", "run")
# op → 需要的 action（list 是逐一查 status，對沒有 status action 的服務不查）。
OPS: dict[str, str] = {"list": "status", "status": "status", "logs": "logs", "restart": "restart", "run": "run"}
REQUEST_KEYS = frozenset({"v", "id", "env", "op", "service", "params", "actor"})

# v1 的 restart 只開放 Web（生產與開發各一個）。這是代理端的硬性白名單：catalog 被誤設成讓
# PostgreSQL／nginx 可 restart 時，catalog 載入會先拒絕（`catalog._parse_service`），即使繞過載入
# 直接給 Catalog 物件，代理執行前也會再擋一次（`actions.check_write_allowed`）。
RESTARTABLE_UNITS: frozenset[str] = frozenset({"report-mark-web.service", "report-mark-dev-web.service"})
# 任何寫入類 action 都不得指向的對象（不分大小寫，比對 unit 名稱）。PostgreSQL 的 restart 尤其禁止：
# web 與所有批次都在用，重啟等於整站中斷，而且容器重啟不經 systemd、polkit 管不到。
FORBIDDEN_WRITE_TARGET = re.compile(
    r"(?i)(postgres|pgvector|(^|[-_.@])pg([-_.@0-9]|$)|nginx|cloudflared|docker|containerd|ops-agent|"
    r"dbus|polkit|systemd-|ssh|cron)"
)
LOG_PARAM_KEYS = frozenset({"since", "lines"})

MAX_REQUEST_BYTES = 8 * 1024

DEFAULT_LOG_LINES = 200
DEFAULT_SINCE = "1h"
# 時間戳最多往未來容忍這麼多（兩端時鐘差）；超過視為參數錯誤。
FUTURE_SKEW = timedelta(minutes=5)

ERROR_CODES = frozenset({
    "bad_request",            # 不是 JSON、欄位不對、版本不符
    "request_too_large",
    "environment_mismatch",
    "forbidden_peer",         # SO_PEERCRED 的 uid 不在 catalog 允許清單
    "unknown_op",
    "unknown_service",
    "action_not_allowed",     # 服務存在，但 catalog 沒給這個 action
    "invalid_params",
    "command_failed",
    "command_timeout",
    "timeout",                # 整個請求超過 request_timeout
    "busy",                   # 同時處理中的請求已達上限
    "already_running",        # 寫入類：同一個 execution group 有工作在跑／排隊，或批次鎖被持有（不排隊）
    "lock_unavailable",       # 寫入類：catalog 指定的鎖檔無法試探（看不到、沒權限）——無法確認就不執行
    "response_too_large",
    "internal_error",
})

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
# 與 app/services/accounts.py 的帳號規則同一個字元集（文字、數字與 . _ @ -）；只用來寫 journal。
# web 端（`web/ops_client.py`）送之前也用它檢查，不合的就不送（不讓代理因此拒絕整個請求）。
ACTOR_RE = re.compile(r"[\w.@-]{1,64}")
_RELATIVE = re.compile(r"([1-9][0-9]{0,4})([smhd])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ProtocolError(Exception):
    """帶穩定 code 的拒絕。message 給人看。"""

    def __init__(self, code: str, message: str):
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def ok_response(req_id, env: str, result: dict) -> dict:
    return {"v": PROTOCOL_VERSION, "id": req_id, "env": env, "ok": True, "result": result}


def error_response(req_id, env: str, code: str, message: str) -> dict:
    return {"v": PROTOCOL_VERSION, "id": req_id, "env": env, "ok": False,
            "error": {"code": code, "message": message}}


def parse_request(line: bytes) -> dict:
    """驗證請求的形狀（不看 catalog）。回傳正規化後的 dict：v、id、env、op、service、params。"""
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("bad_request", "請求不是合法的 UTF-8 JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("bad_request", "請求必須是 JSON 物件")
    extra = set(obj) - REQUEST_KEYS
    if extra:
        raise ProtocolError("bad_request", f"不認得的欄位：{sorted(extra)}")
    if obj.get("v") != PROTOCOL_VERSION or isinstance(obj.get("v"), bool):
        raise ProtocolError("bad_request", f"協定版本必須是 {PROTOCOL_VERSION}")
    req_id = obj.get("id")
    if req_id is not None and not (isinstance(req_id, str) and _REQUEST_ID.fullmatch(req_id)):
        raise ProtocolError("bad_request", "id 必須是 1–64 個英數字或 ._:-")
    env = obj.get("env")
    if not isinstance(env, str) or env not in ENVIRONMENTS:
        raise ProtocolError("bad_request", f"env 必須是 {'／'.join(ENVIRONMENTS)}")
    op = obj.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise ProtocolError("unknown_op", f"不支援的操作：{op!r}")
    service = obj.get("service")
    if op == "list":
        if service is not None:
            raise ProtocolError("bad_request", "list 不帶 service")
    elif not isinstance(service, str) or not service:
        raise ProtocolError("bad_request", f"{op} 必須帶 service")
    params = obj.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError("bad_request", "params 必須是物件")
    if op != "logs" and params:
        raise ProtocolError("invalid_params", f"{op} 不收參數")
    actor = obj.get("actor")
    if actor is not None and not (isinstance(actor, str) and ACTOR_RE.fullmatch(actor)):
        raise ProtocolError("bad_request", "actor 必須是 1–64 個文字、數字或 ._@-")
    return {"v": PROTOCOL_VERSION, "id": req_id, "env": env, "op": op, "service": service, "params": params,
            "actor": actor}


def parse_lines(raw, max_lines: int) -> int:
    """行數：1..max_lines 的整數（布林不算整數）。省略時用 `DEFAULT_LOG_LINES`（不超過上限）。"""
    if raw is None:
        return min(DEFAULT_LOG_LINES, max_lines)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ProtocolError("invalid_params", "lines 必須是整數")
    if not 1 <= raw <= max_lines:
        raise ProtocolError("invalid_params", f"lines 必須介於 1..{max_lines}")
    return raw


def parse_since(raw, max_age: timedelta, now: datetime | None = None) -> datetime:
    """起始時間，回傳帶時區的 UTC datetime。

    兩種寫法：相對（`30s`／`15m`／`2h`／`1d`）或帶時區的 ISO 8601（`2026-10-06T09:00:00+08:00`）。
    不得早於 `now - max_age`、不得晚於 `now + FUTURE_SKEW`。**刻意不把原字串交給 journalctl**：
    它的 `--since` 認得 `yesterday`、`-1week` 之類寫法，原樣轉交等於把參數面開給呼叫端；這裡一律
    換成 epoch 秒再組參數。
    """
    now = now or datetime.now(timezone.utc)
    if raw is None:
        raw = DEFAULT_SINCE
    if not isinstance(raw, str) or len(raw) > 40:
        raise ProtocolError("invalid_params", "since 必須是 ≤40 字元的字串")
    m = _RELATIVE.fullmatch(raw)
    if m:
        delta = timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])
        if delta > max_age:
            raise ProtocolError("invalid_params", f"since 最多往前 {_fmt_age(max_age)}")
        return now - delta
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("invalid_params", "since 必須是相對時間（如 15m、2h、1d）或帶時區的 ISO 8601") from exc
    if when.tzinfo is None:
        raise ProtocolError("invalid_params", "since 的 ISO 8601 必須帶時區")
    when = when.astimezone(timezone.utc)
    if when < now - max_age:
        raise ProtocolError("invalid_params", f"since 最多往前 {_fmt_age(max_age)}")
    if when > now + FUTURE_SKEW:
        raise ProtocolError("invalid_params", "since 不可以是未來時間")
    return when


def _fmt_age(age: timedelta) -> str:
    secs = int(age.total_seconds())
    if secs % 86400 == 0:
        return f"{secs // 86400} 天"
    if secs % 3600 == 0:
        return f"{secs // 3600} 小時"
    return f"{secs} 秒"
