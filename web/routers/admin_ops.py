"""維運（/api/admin/ops/*）：唯讀的服務清單與狀態、單一服務狀態、最近日誌（管理員＋`ops.read`），
以及寫入類的 restart／run（管理員＋另外授予的 `ops.operate`＋10 分鐘內重新驗證過密碼）。

web 不碰 systemctl／journalctl／docker，只經 `web/ops_client.py` 問維運代理（`ops_agent/`，Unix socket、
固定白名單）。代理只認 Service Catalog（`deploy/ops/services.*.toml`）裡的服務與 action；名稱不在 catalog
回 404。代理不可用回 503 `ops_agent_unavailable`（fail-open：只影響維運這幾條，web 其他功能照常）。

寫入類（規則在 `ops_agent/actions.py`，這裡不重複判斷）：
- v1 只有 Web 的 restart 與既有 oneshot 的 run；PostgreSQL、nginx、cloudflared 永遠唯讀（代理回
  `action_not_allowed` → 403）。同 execution group 有工作在跑、或 LLM 批次鎖被持有 → 409 `already_running`，
  不排隊。請求不收 body 也不收 query：代理執行的 argv 完全由 catalog 決定。
- 成功回 202：restart 是「已接受、`execute_after_ms` 後才執行」（重啟 Web 會中斷這次請求），run 是「job 已排進
  systemd」。前端輪詢 `GET /api/admin/ops/services/{name}`，`systemd.invocation_id` 與回應的
  `previous_invocation_id` 不同時就是新的一輪。
- 每次嘗試（成功、被代理拒絕、代理不可用）都以 `accounts.record_ops_action` 寫 `admin_audit_log`。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ValidationError

from app.services.accounts import User
from web import authz, deps, ops_client
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))
# 順序有意義：先檢查 scope（沒授權的人不必被要求重新驗證密碼），再檢查 elevated。
_OPS_OPERATE = [Depends(authz.require_scope("ops.operate")), Depends(authz.require_elevated)]

Summary = Literal["running", "idle", "failed", "transitioning", "not_found", "unknown"]
Kind = Literal["systemd", "container"]
Tier = Literal["critical", "important", "supporting"]
# 與 ops_agent/protocol.py 的 KNOWN_ACTIONS 逐字一致（tests/test_admin_ops_api.py 釘住）。
Action = Literal["status", "logs", "restart", "run"]

# 與 ops_agent/catalog.py 的服務名稱規則一致；不合的直接 422，不必問代理。
ServiceName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,39}$", max_length=40)]


class OpsSystemdState(BaseModel):
    load_state: str | None = None
    active_state: str | None = None
    sub_state: str | None = None
    result: str | None = None
    type: str | None = None
    unit_file_state: str | None = None
    exec_main_code: str | None = None
    exec_main_status: int | None = None
    main_pid: int | None = None
    n_restarts: int | None = None
    exec_main_start_at: str | None = None
    exec_main_exit_at: str | None = None
    active_enter_at: str | None = None
    state_change_at: str | None = None
    invocation_id: str | None = None


class OpsTimerState(BaseModel):
    unit: str
    load_state: str | None = None
    active_state: str | None = None
    next_elapse_at: str | None = None
    last_trigger_at: str | None = None


class OpsContainerState(BaseModel):
    status: str | None = None
    running: bool | None = None
    paused: bool | None = None
    restarting: bool | None = None
    oom_killed: bool | None = None
    dead: bool | None = None
    exit_code: int | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    health: str | None = None
    failing_streak: int | None = None
    restart_count: int | None = None
    image: str | None = None


class OpsServiceStatus(BaseModel):
    name: str
    kind: Kind
    tier: Tier
    target: str
    timer: str | None = None
    actions: list[Action]
    group: str | None = None
    description: str = ""
    summary: Summary
    error: str | None = None
    systemd: OpsSystemdState | None = None
    container: OpsContainerState | None = None
    timer_state: OpsTimerState | None = None


class OpsServiceListResponse(BaseModel):
    environment: str
    host: str
    checked_at: str
    items: list[OpsServiceStatus]


class OpsServiceDetail(OpsServiceStatus):
    checked_at: str


class OpsActionResponse(BaseModel):
    """寫入類操作已交給代理：restart＝scheduled（`execute_after_ms` 後執行），run＝queued（job 已排進 systemd）。"""

    name: str
    kind: Kind
    tier: Tier
    target: str
    timer: str | None = None
    actions: list[Action]
    group: str | None = None
    description: str = ""
    action: Literal["restart", "run"]
    state: Literal["scheduled", "queued"]
    previous_invocation_id: str | None = None
    previous_active_enter_at: str | None = None
    previous_exec_main_start_at: str | None = None
    execute_after_ms: int
    accepted_at: str
    checked_at: str


class OpsLogsResponse(BaseModel):
    name: str
    kind: Kind
    tier: Tier
    target: str
    since: str
    lines: int
    truncated: bool
    entries: list[str]
    checked_at: str


# ── 輔助函式一律放在所有 @router.* 裝飾器之上（夾在中間會讓端點回 422）──────────

_ERRORS = {
    "unknown_service": (404, "ops_service_not_found"),
    "action_not_allowed": (403, "ops_action_not_allowed"),
    "invalid_params": (400, "invalid_params"),
    "command_timeout": (504, "ops_timeout"),
    "timeout": (504, "ops_timeout"),
    "command_failed": (502, "ops_command_failed"),
    "busy": (503, "ops_agent_busy"),
}


# 寫入類的錯誤對照：與唯讀那組分開，code 依 Admin v1 的拍板（409 already_running、403 action_not_allowed）。
_WRITE_ERRORS = {
    **_ERRORS,
    "action_not_allowed": (403, "action_not_allowed"),
    "already_running": (409, "already_running"),
    "lock_unavailable": (503, "ops_lock_unavailable"),
}


async def _record(user: User, action: str, service: str, environment: str, result: str, target=None,
                  invocation_id=None) -> None:
    """稽核寫不進去不改變回應（操作已經在 systemd 那端發生或已被拒絕），但一定留下 ERROR 日誌。"""
    try:
        await deps.accounts.record_ops_action(actor_id=user.id, action=action, service=service,
                                              environment=environment, result=result, target=target,
                                              invocation_id=invocation_id)
    except Exception:  # noqa: BLE001 - 稽核失敗只能記日誌，不能讓已發生的操作看起來像沒發生
        logger.exception("維運操作稽核寫入失敗 action=%s service=%s result=%s", action, service, result)


async def _operate(action: str, name: str, user: User) -> OpsActionResponse:
    client = ops_client.default_client()
    env = client.environment or "unknown"
    try:
        result = await client.request(action, name, actor=user.username)
    except ops_client.OpsAgentUnavailable as exc:
        logger.warning("維運代理不可用 op=%s service=%s：%s", action, name, exc)
        await _record(user, action, name, env, "ops_agent_unavailable")
        raise AppError(503, "ops_agent_unavailable", f"維運代理不可用：{exc}") from exc
    except ops_client.OpsAgentError as exc:
        status, code = _WRITE_ERRORS.get(exc.code, (502, "ops_agent_error"))
        logger.warning("維運操作被拒 op=%s service=%s actor=%s code=%s", action, name, user.username, exc.code)
        await _record(user, action, name, env, exc.code)
        raise AppError(status, code, exc.message) from exc
    try:
        resp = OpsActionResponse.model_validate(result)
    except ValidationError as exc:
        # 代理說 ok 但格式不符：操作可能已經發生，稽核記成 accepted_unverified。
        logger.warning("維運代理回應格式不符 op=%s service=%s：%s", action, name, exc.errors()[:3])
        await _record(user, action, name, env, "accepted_unverified")
        raise AppError(502, "ops_agent_error", "維運代理的回應格式不符（操作可能已送出，請看狀態）") from exc
    logger.warning("維運操作 op=%s service=%s actor=%s state=%s", action, name, user.username, resp.state)
    await _record(user, action, name, env, resp.state, resp.target, resp.previous_invocation_id)
    return resp


async def _ask(model: type[BaseModel], op: str, service: str | None = None, params: dict | None = None):
    try:
        result = await ops_client.default_client().request(op, service, params)
    except ops_client.OpsAgentUnavailable as exc:
        logger.warning("維運代理不可用 op=%s service=%s：%s", op, service, exc)
        raise AppError(503, "ops_agent_unavailable", f"維運代理不可用：{exc}") from exc
    except ops_client.OpsAgentError as exc:
        status, code = _ERRORS.get(exc.code, (502, "ops_agent_error"))
        raise AppError(status, code, exc.message) from exc
    try:
        return model.model_validate(result)
    except ValidationError as exc:
        logger.warning("維運代理回應格式不符 op=%s service=%s：%s", op, service, exc.errors()[:3])
        raise AppError(502, "ops_agent_error", "維運代理的回應格式不符") from exc


@router.get("/api/admin/ops/services", response_model=OpsServiceListResponse, dependencies=[_OPS_READ])
async def list_ops_services():
    """catalog 裡全部服務與目前狀態（一次 systemctl show＋一次 docker inspect）。"""
    return await _ask(OpsServiceListResponse, "list")


@router.get("/api/admin/ops/services/{name}", response_model=OpsServiceDetail, dependencies=[_OPS_READ])
async def get_ops_service(name: ServiceName):
    return await _ask(OpsServiceDetail, "status", name)


@router.get("/api/admin/ops/services/{name}/logs", response_model=OpsLogsResponse, dependencies=[_OPS_READ])
async def get_ops_service_logs(
    name: ServiceName,
    since: str = Query("1h", max_length=40, pattern=r"^[0-9A-Za-z:+.\-]+$"),
    lines: int = Query(200, ge=1, le=1000),
):
    """最近日誌：`since` 是相對時間（`15m`、`2h`、`1d`，最多 7 天）或帶時區的 ISO 8601；`lines` 1–1000。"""
    return await _ask(OpsLogsResponse, "logs", name, {"since": since, "lines": lines})


@router.post("/api/admin/ops/services/{name}/restart", response_model=OpsActionResponse, status_code=202,
             dependencies=_OPS_OPERATE)
async def restart_ops_service(name: ServiceName, user: User = Depends(authz.current_user)):
    """重啟服務（v1 只有 Web）。202＝代理已接受、`execute_after_ms` 後才執行；輪詢狀態看 `invocation_id`。"""
    return await _operate("restart", name, user)


@router.post("/api/admin/ops/services/{name}/run", response_model=OpsActionResponse, status_code=202,
             dependencies=_OPS_OPERATE)
async def run_ops_service(name: ServiceName, user: User = Depends(authz.current_user)):
    """立即執行一次 oneshot（等同 timer 觸發，不收參數）。202＝job 已排進 systemd；同 group 在跑回 409。"""
    return await _operate("run", name, user)
