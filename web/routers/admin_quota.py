"""個人配額管理（`/api/admin/quota*`；Admin v2 Quota lane）。服務層在 `app/services/quota.py`（經 `deps.quota`）。

授權：router 層掛 `authz.require_admin`＋`accounts.manage`；寫入另加 `authz.require_elevated`。設成不限
（`user_quota.daily_limit` NULL）與調整 super admin 的配額只有 super admin（規則寫在服務層，CLI 也受約束）；
同交易寫稽核 `quota.update`（detail 只有類別、模式、上限與先前值，不含理由全文與帳號名稱）。

- `GET /api/admin/quota`：每位使用者今天（台北時間）的 ask／export／upload 次數、上限、覆寫與「超出上限」次數，
  今天的線上 LLM 呼叫與 token；影子模式狀態。
- `GET /api/admin/quota/stats?days=14`：最近 N 天（1–90）每人每日需求的 P50／P95、最大值、超額人日與次數
  （給「兩週後是否正式阻擋」用）。
- `PUT /api/admin/quota/users/{user_id}/{kind}`：設定（`mode=limit`＋`daily_limit`、`mode=unlimited`）或清除
  （`mode=default`）個人覆寫。

配額值：`QUOTA_ASK_DAILY`（100）、`QUOTA_EXPORT_DAILY`（20）、`UPLOAD_DAILY_QUOTA`（30），先影子模式（`QUOTA_ENFORCE`
＋旗標 `quota.enforce` 同時開才正式阻擋，使用者定案 5）。計數在 `usage_counter`，不 COUNT qa_log。
單一使用者的數字只出現在這裡（只有次數、沒有主題）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from app.services.accounts import User
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("accounts.manage"))])

QuotaKind = Literal["ask", "export", "upload"]
QuotaMode = Literal["default", "limit", "unlimited"]
Role = Literal["admin", "user"]


class QuotaItem(BaseModel):
    kind: QuotaKind
    used: int
    over: int = Field(description="今天超出上限的次數（影子模式＝本來會擋；正式阻擋＝被擋下）。upload 恆為 0")
    limit: int | None = Field(description="這個人的上限；null＝不限")
    default_limit: int
    mode: QuotaMode
    remaining: int | None = None
    reason: str | None = None
    updated_at: datetime | None = None


class QuotaUserLlm(BaseModel):
    calls: int
    failures: int
    prompt_tokens: int
    completion_tokens: int


class QuotaUserRow(BaseModel):
    user_id: str
    username: str
    role: Role
    enabled: bool
    is_super: bool
    items: list[QuotaItem]
    llm: QuotaUserLlm


class QuotaDefaults(BaseModel):
    ask: int
    export: int
    upload: int


class QuotaEnforcement(BaseModel):
    env_ceiling: bool = Field(description="環境變數 QUOTA_ENFORCE（能力上限）")
    flag_enabled: bool = Field(description="DB 旗標 quota.enforce 的政策值（沒有覆寫＝registry 預設）")
    flag_scoped: bool = Field(description="旗標覆寫帶角色／使用者作用域（只對部分人生效）")
    flag_source: Literal["default", "db", "fallback"]
    effective: bool
    mode: Literal["shadow", "enforce"]


class QuotaOverToday(BaseModel):
    ask: int
    export: int


class QuotaOverview(BaseModel):
    day: str
    timezone: str
    resets_in_seconds: int
    defaults: QuotaDefaults
    enforcement: QuotaEnforcement
    over_today: QuotaOverToday
    users: list[QuotaUserRow]


class QuotaKindStats(BaseModel):
    kind: QuotaKind
    default_limit: int
    user_days: int = Field(description="有使用的人日數（當天這一類至少一次）")
    users: int
    p50: int | None = None
    p95: int | None = None
    max: int | None = None
    over_user_days: int
    over_events: int


class QuotaStats(BaseModel):
    since_day: str
    until_day: str
    days: int
    timezone: str
    kinds: list[QuotaKindStats]


class QuotaOverrideRequest(BaseModel):
    mode: QuotaMode
    daily_limit: int | None = Field(None, ge=0, le=100_000, description="mode=limit 時必填")
    reason: str | None = Field(None, max_length=500)


class QuotaOverrideResult(BaseModel):
    changed: bool
    item: QuotaItem


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _http_error(exc: Exception) -> AppError:
    q = deps.quota
    if isinstance(exc, q.QuotaTargetNotFound):
        return AppError(404, "not_found", str(exc))
    if isinstance(exc, q.QuotaPermissionDenied):
        return AppError(403, "super_required", str(exc))
    return AppError(400, "invalid_input", str(exc))


@router.get("/api/admin/quota", response_model=QuotaOverview)
async def get_quota_overview():
    """每位（未刪除）使用者今天的各類次數、上限與覆寫，今天的線上 LLM 呼叫；影子模式狀態與今天超額次數。"""
    return QuotaOverview(**await deps.quota.admin_overview())


@router.get("/api/admin/quota/stats", response_model=QuotaStats)
async def get_quota_stats(days: int = Query(14, ge=1, le=90, description="最近幾個台北日（含今天）")):
    """每人每日需求（含超出上限的嘗試）的 P50／P95：只統計有使用的人日。"""
    return QuotaStats(**await deps.quota.usage_stats(days))


@router.put("/api/admin/quota/users/{user_id}/{kind}", response_model=QuotaOverrideResult,
            dependencies=[Depends(authz.require_elevated)])
async def set_quota_override(
    body: QuotaOverrideRequest,
    user_id: str = Path(..., max_length=64),
    kind: QuotaKind = Path(...),
    actor: User = Depends(authz.current_user),
):
    """設定或清除個人覆寫。`unlimited` 與 super admin 帳號的覆寫只有 super admin 能改；沒有變動不寫稽核。"""
    if body.mode == "limit" and body.daily_limit is None:
        raise AppError(400, "invalid_input", "mode=limit 時必須給 daily_limit")
    try:
        result = await deps.quota.set_override(
            user_id, kind, mode=body.mode, daily_limit=body.daily_limit if body.mode == "limit" else None,
            reason=body.reason, actor_id=actor.id,
        )
    except deps.quota.QuotaError as exc:
        raise _http_error(exc) from exc
    if result["changed"]:
        logger.info("管理操作 actor=%s action=quota.update target=%s kind=%s mode=%s", actor.username, user_id,
                    kind, body.mode)
    return QuotaOverrideResult(**result)
