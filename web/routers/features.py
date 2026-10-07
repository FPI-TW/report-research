"""目前使用者的功能旗標有效值（`GET /api/features`；Admin v2 Flags lane）。

任何登入的使用者都能讀**自己的**有效值（`feature_flags.snapshot(user)`）；不掛 require_admin（管理員 TOTP
強制不影響它）。回應只有 `{key: bool}`：不含環境變數上限、覆寫、作用域名單——一般使用者不需要知道誰被開了什麼。
不在 `/api/admin` 底下，所以不在產生的 admin client 裡，前端手寫 zod（`frontend/src/lib/useFeatures.ts`）。

DB 讀不到時 `snapshot` 退回 registry 預設（上限照樣 AND），這裡照常 200。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services import feature_flags
from app.services.accounts import User
from web import authz

router = APIRouter(dependencies=[Depends(authz.current_user)])


class FeaturesResponse(BaseModel):
    features: dict[str, bool]


@router.get("/api/features", response_model=FeaturesResponse)
async def get_features(user: User = Depends(authz.current_user)):
    """目前使用者的每個登記旗標實際值（環境變數上限 AND DB 覆寫，套用角色／使用者作用域）。"""
    state = await feature_flags.snapshot(user)
    return FeaturesResponse(features={key: s.effective for key, s in state.items()})
