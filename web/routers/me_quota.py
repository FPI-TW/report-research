"""自己的配額用量（`/api/me/quota`；Admin v2 Quota lane）——Wave 0 只建空 router，端點由 lane 補。

任何登入的使用者都能看**自己的**次數（只有次數、沒有主題）；不掛 require_admin（管理員 TOTP 強制不影響它）。
與 `/api/admin/*` 分開，所以不在產生的 admin client 裡，前端手寫 zod。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.current_user)])
