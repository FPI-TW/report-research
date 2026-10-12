"""自己的配額用量（`/api/me/quota`；Admin v2 Quota lane）。服務層在 `app/services/quota.py`（經 `deps.quota`）。

任何登入的使用者都能看**自己的**次數（只有次數、沒有主題）；不掛 require_admin（管理員 TOTP 強制不影響它）。
一般使用者只回 ask（匯出與上傳是管理功能），管理員回 ask／export／upload。`enforced` 是對這個人而言正式阻擋
是否生效（兩道開關）；影子模式下 `over` 是「超出上限但照常放行」的次數。
與 `/api/admin/*` 分開，所以不在產生的 admin client 裡，前端要用時手寫 zod。
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services.accounts import User
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.current_user)])


class MyQuotaItem(BaseModel):
    kind: Literal["ask", "export", "upload"]
    used: int
    over: int
    limit: int | None = None  # null＝不限
    remaining: int | None = None


class MyQuota(BaseModel):
    day: str
    timezone: str
    resets_in_seconds: int
    enforced: bool
    items: list[MyQuotaItem]


@router.get("/api/me/quota", response_model=MyQuota)
async def get_my_quota(user: User = Depends(authz.current_user)):
    """今天（台北時間）自己的各類次數與上限。"""
    try:
        data = await deps.quota.me_usage(user)
    except Exception as exc:  # noqa: BLE001 — 查詢失敗只影響這個顯示，回 503 讓前端說人話
        logger.warning("配額：查詢自己的用量失敗", exc_info=True)
        raise AppError(503, "quota_unavailable", "配額用量暫時無法查詢") from exc
    return MyQuota(**data)
