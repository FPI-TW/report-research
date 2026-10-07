"""管理後台的對外 API 用戶端（/api/admin/api-clients*）。整組限管理員＋`api_clients.manage`。

規則、驗證與稽核都在 `app/services/api_clients.py`，這裡只做 HTTP 轉換；呼叫一律經
`deps.api_clients`（測試的替換點）。服務層的 `ApiClientError` 依 code 對到狀態碼：
`invalid_input`→400、`not_found`→404、`name_taken`→409，訊息原樣當 detail 給前端顯示。

- 建立與輪替金鑰會產生新的原始金鑰，要已提升權限（`require_elevated`）。原始金鑰只出現在那兩個回應的
  `api_key` 一次（DB 只存 sha256），回應帶 `Cache-Control: no-store`；其餘回應一律不含金鑰或 hash。
- 授權範圍（entitlement）是完整取代，不是增量；`market` 必填，其他維度省略＝不限。
- 日誌只記誰對哪個用戶端做了什麼，絕不記金鑰；完整紀錄在稽核（服務層同交易寫入）。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.accounts import User
from app.services.api_clients import ApiClient, ApiClientError
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

# 與 api_clients.KEY_SCOPES／ENTITLEMENT_DIMENSIONS 逐字一致（tests/test_admin_api_clients.py 釘住）；
# OpenAPI 與產生的前端 client 靠它列舉。
ApiClientScope = Literal["search", "report.file"]
EntitlementDimension = Literal["market", "source", "report_type", "instrument_type"]

_MANAGE = Depends(authz.require_scope("api_clients.manage"))
_ELEVATED = Depends(authz.require_elevated)

# 長度上限只為擋巨大 payload；實際規則（名稱 1–100 字、備註 1000 字、每維度 200 個值、市場代碼）由服務層判，
# 回 400 與中文原因，而不是 pydantic 的 422 英文訊息。
_Value = Annotated[str, StringConstraints(max_length=1000)]
_Values = Annotated[list[_Value], Field(max_length=1000)]


class ApiClientEntitlements(BaseModel):
    """授權範圍：欄位即維度（`EntitlementDimension`）。`market` 必填；其他省略或 null＝不限。"""

    model_config = ConfigDict(extra="forbid")

    market: _Values
    source: _Values | None = None
    report_type: _Values | None = None
    instrument_type: _Values | None = None


class ApiClientItem(BaseModel):
    id: int
    name: str
    key_prefix: str  # 可公開的金鑰前綴（rmk_<prefix>_…），用來辨認是哪一把
    enabled: bool
    scopes: list[ApiClientScope]
    rate_limit_per_min: int
    daily_quota: int
    entitlements: ApiClientEntitlements
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    key_rotated_at: str | None = None
    last_used_at: str | None = None


class ApiClientListResponse(BaseModel):
    items: list[ApiClientItem]


class ApiClientWithKey(ApiClientItem):
    # 原始金鑰：只在建立與輪替的回應出現這一次，之後無法再取得。
    api_key: str


class CreateApiClientRequest(BaseModel):
    name: str = Field(..., max_length=1000)
    scopes: list[ApiClientScope] = Field(..., max_length=10)
    rate_limit_per_min: int
    daily_quota: int
    entitlements: ApiClientEntitlements
    note: str | None = Field(None, max_length=5000)


class UpdateApiClientRequest(BaseModel):
    # 全部省略＝沒有要變更（400）。note 傳空字串＝清除備註；省略或 null＝不變更。
    enabled: bool | None = None
    scopes: list[ApiClientScope] | None = Field(None, max_length=10)
    rate_limit_per_min: int | None = None
    daily_quota: int | None = None
    note: str | None = Field(None, max_length=5000)


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────

_ERROR_STATUS = {"invalid_input": 400, "not_found": 404, "name_taken": 409}


def _http_error(exc: ApiClientError) -> AppError:
    status = _ERROR_STATUS.get(exc.code)
    if status is None:
        return AppError(400, "bad_request", exc.message)
    return AppError(status, exc.code, exc.message)


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _entitlements_in(body: ApiClientEntitlements) -> dict[str, list[str]]:
    """省略／null 的維度不送（＝不限）；空清單照送，由服務層判（market 空清單＝400）。"""
    return {k: v for k, v in body.model_dump().items() if v is not None}


def _item_fields(c: ApiClient) -> dict:
    ents = c.entitlements
    return {
        "id": c.id, "name": c.name, "key_prefix": c.key_prefix, "enabled": c.enabled,
        "scopes": sorted(c.scopes), "rate_limit_per_min": c.rate_limit_per_min, "daily_quota": c.daily_quota,
        "entitlements": ApiClientEntitlements(
            market=list(ents.get("market", ())),
            **{d: list(ents[d]) for d in ("source", "report_type", "instrument_type") if d in ents},
        ),
        "note": c.note, "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
        "key_rotated_at": _iso(c.key_rotated_at), "last_used_at": _iso(c.last_used_at),
    }


def _item(c: ApiClient) -> ApiClientItem:
    return ApiClientItem(**_item_fields(c))


def _with_key(c: ApiClient, raw_key: str, response: Response) -> ApiClientWithKey:
    response.headers["Cache-Control"] = "no-store"
    return ApiClientWithKey(**_item_fields(c), api_key=raw_key)


@router.get("/api/admin/api-clients", response_model=ApiClientListResponse, dependencies=[_MANAGE])
async def list_api_clients():
    """全部 API 用戶端（含停用的），依建立順序。不含金鑰與 hash。"""
    return ApiClientListResponse(items=[_item(c) for c in await deps.api_clients.list_clients()])


@router.post("/api/admin/api-clients", response_model=ApiClientWithKey, status_code=201,
             dependencies=[_MANAGE, _ELEVATED])
async def create_api_client(body: CreateApiClientRequest, response: Response,
                            actor: User = Depends(authz.current_user)):
    """建立 API 用戶端並產生金鑰；回應的 `api_key` 只此一次（`Cache-Control: no-store`）。稽核同交易寫入。"""
    try:
        client, raw_key = await deps.api_clients.create_client(
            actor_id=actor.id, name=body.name, scopes=body.scopes, rate_limit_per_min=body.rate_limit_per_min,
            daily_quota=body.daily_quota, entitlements=_entitlements_in(body.entitlements), note=body.note,
        )
    except ApiClientError as exc:
        raise _http_error(exc) from exc
    logger.info("管理操作 actor=%s action=api_client.create target=%s prefix=%s",
                actor.username, client.id, client.key_prefix)
    return _with_key(client, raw_key, response)


@router.patch("/api/admin/api-clients/{client_id}", response_model=ApiClientItem, dependencies=[_MANAGE])
async def update_api_client(client_id: int, body: UpdateApiClientRequest, actor: User = Depends(authz.current_user)):
    """改啟用狀態、scopes、限流、每日額度、備註（省略的欄位不變更）。停用下一個請求就生效。"""
    try:
        client = await deps.api_clients.update_client(
            actor_id=actor.id, client_id=client_id, enabled=body.enabled, scopes=body.scopes,
            rate_limit_per_min=body.rate_limit_per_min, daily_quota=body.daily_quota, note=body.note,
        )
    except ApiClientError as exc:
        raise _http_error(exc) from exc
    logger.info("管理操作 actor=%s action=api_client.update target=%s", actor.username, client_id)
    return _item(client)


@router.put("/api/admin/api-clients/{client_id}/entitlements", response_model=ApiClientItem,
            dependencies=[_MANAGE])
async def replace_api_client_entitlements(client_id: int, body: ApiClientEntitlements,
                                          actor: User = Depends(authz.current_user)):
    """以完整清單取代授權範圍（不是增量）。`market` 必填非空；其他維度省略＝不限。"""
    try:
        client = await deps.api_clients.replace_entitlements(
            actor_id=actor.id, client_id=client_id, entitlements=_entitlements_in(body),
        )
    except ApiClientError as exc:
        raise _http_error(exc) from exc
    logger.info("管理操作 actor=%s action=api_client.entitlements target=%s", actor.username, client_id)
    return _item(client)


@router.post("/api/admin/api-clients/{client_id}/rotate", response_model=ApiClientWithKey,
             dependencies=[_MANAGE, _ELEVATED])
async def rotate_api_client_key(client_id: int, response: Response, actor: User = Depends(authz.current_user)):
    """換一把新金鑰，舊金鑰下一個請求就失效；回應的 `api_key` 只此一次（`Cache-Control: no-store`）。"""
    try:
        client, raw_key = await deps.api_clients.rotate_key(actor_id=actor.id, client_id=client_id)
    except ApiClientError as exc:
        raise _http_error(exc) from exc
    logger.info("管理操作 actor=%s action=api_client.rotate target=%s prefix=%s",
                actor.username, client_id, client.key_prefix)
    return _with_key(client, raw_key, response)
