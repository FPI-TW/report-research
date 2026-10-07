"""管理後台的對外 API 用戶端（/api/admin/api-clients*）。整組限管理員＋`api_clients.manage`。

規則、驗證與稽核都在 `app/services/api_clients.py`，這裡只做 HTTP 轉換；呼叫一律經
`deps.api_clients`（測試的替換點）。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin)])
