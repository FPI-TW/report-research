"""目前使用者的功能旗標有效值（`GET /api/features`；Admin v2 Flags lane）——Wave 0 只建空 router，端點由 lane 補。

任何登入的使用者都能讀**自己的**有效值（`feature_flags.snapshot(user)`）；不掛 require_admin。不在
`/api/admin` 底下，所以不在產生的 admin client 裡，前端手寫 zod（`useFeatures`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.current_user)])
