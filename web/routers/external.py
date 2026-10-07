"""對外 API（/external/v1/*）：以 `Authorization: Bearer <api_key>` 認證的研報搜尋與短效原檔連結。

認證、限流與每日額度在 `web/external_auth.py`；用戶端與 entitlement 的規則在
`app/services/api_clients.py`、`app/services/entitlement.py`，呼叫一律經 `deps.api_clients`。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
