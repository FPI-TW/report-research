"""維運狀態（/api/admin/ops/*，唯讀）：服務清單與狀態、單一服務狀態、最近日誌。整組限管理員＋`ops.read`。

web 不碰 systemctl／journalctl／docker，只經 `web/ops_client.py` 問維運代理（`ops_agent/`，Unix socket、
固定白名單）。代理只認 Service Catalog（`deploy/ops/services.*.toml`）裡的服務與 action；名稱不在 catalog
回 404。代理不可用回 503 `ops_agent_unavailable`（fail-open：只影響這三條，web 其他功能照常）。

restart／run-now 是 P7，不在這裡。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ValidationError

from web import authz, ops_client
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

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
