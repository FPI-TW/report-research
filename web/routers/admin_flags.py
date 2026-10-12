"""功能旗標管理（`/api/admin/flags*`；Admin v2 Flags lane）。

授權：router 層掛 `authz.require_admin`＋`ops.read`（看旗標、匯出）；寫入（`PUT`／`DELETE` 單一旗標、匯入）另加
`ops.operate`（可授予）＋`authz.require_elevated`（使用者定案 11）。規則與 SQL 在 `app/services/feature_flags.py`，
這裡只做 HTTP 轉換：每個實際變更在**同一筆交易**寫稽核 `flag.update`，commit 之後 `feature_flags.invalidate()`
讓本行程立即生效（定案 12）。

- 實際值＝環境變數上限 AND DB 覆寫：上限關閉時覆寫寫得進去但沒有作用（管理頁說明「需先在環境檔開啟」）。
- 未登記的 key：`PUT`／`DELETE` 回 404 `flag_not_found`；DB 裡多出來的列只在清單的 `ignored_keys` 列出，不生效。
- 匯出／匯入（定案 16，測試環境 → 正式環境的設定搬移）：匯出檔的使用者以帳號名稱表示；匯入預設 `dry_run=true`
  只回差異，套用時任何一個錯誤（未知 key、找不到的帳號、不合法的作用域）就整份不寫、回 422 `flag_import_invalid`。
  只做 API，不自動同步（步驟見 `docs/production_resilience.md`「功能開關與 staging 啟用矩陣」）。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.services import feature_flags
from app.services.accounts import User
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("ops.read"))])

_OPERATE = [Depends(authz.require_scope("ops.operate")), Depends(authz.require_elevated)]

Role = Literal["admin", "user"]


class FlagUserRef(BaseModel):
    id: str
    username: str | None = None  # 查不到（已刪帳）時為 null


class FlagOverrideView(BaseModel):
    enabled: bool
    allow_roles: list[Role] | None = None
    allow_users: list[FlagUserRef] | None = None
    note: str | None = None
    updated_by: str | None = None  # 帳號名稱
    updated_at: str | None = None


class FlagItem(BaseModel):
    key: str
    description: str
    ceiling_env: str
    ceiling: bool  # 環境變數上限
    default: bool  # registry 預設（沒有覆寫時的政策）
    override: FlagOverrideView | None = None
    # on＝對所有人開、off＝對所有人關、scoped＝只對作用域內的角色或使用者開
    effective: Literal["on", "off", "scoped"]
    effective_for_me: bool


class FlagListResponse(BaseModel):
    registry_version: str
    items: list[FlagItem]
    ignored_keys: list[str]  # DB 裡有、registry 沒登記（不生效）的 key


class FlagUpdateRequest(BaseModel):
    enabled: bool
    # 兩者都 null＝全站；任一非 null＝只對列出的角色／使用者（enabled 仍須為 true）。空清單由服務層拒絕。
    allow_roles: list[Role] | None = Field(None, max_length=2)
    allow_users: list[str] | None = Field(None, max_length=feature_flags.MAX_ALLOW_USERS)
    # 長度上限只擋巨大 payload；實際規則（最多 500 字）由服務層判，回 422 與中文原因。
    note: str | None = Field(None, max_length=2000)


class FlagExportOverride(BaseModel):
    enabled: bool
    allow_roles: list[str] | None = Field(None, max_length=8)
    allow_users: list[str] | None = Field(None, max_length=feature_flags.MAX_ALLOW_USERS)  # 帳號名稱
    note: str | None = Field(None, max_length=2000)


class FlagExportEntry(BaseModel):
    key: str = Field(..., max_length=128)
    override: FlagExportOverride | None = None  # null＝目標環境不要有覆寫（回到 registry 預設）


class FlagExportDocument(BaseModel):
    format: Literal["report-mark/feature-flags"]
    format_version: Literal[1]
    registry_version: str = Field(..., max_length=64)
    source_environment: str | None = Field(None, max_length=32)
    flags: list[FlagExportEntry] = Field(..., max_length=64)


class FlagImportRequest(BaseModel):
    dry_run: bool = True
    document: FlagExportDocument


class FlagImportChange(BaseModel):
    key: str
    action: Literal["create", "update", "delete", "unchanged"]
    before: FlagExportOverride | None = None
    after: FlagExportOverride | None = None


class FlagImportProblem(BaseModel):
    key: str | None = None
    code: str
    detail: str


class FlagImportResponse(BaseModel):
    dry_run: bool
    applied: bool
    registry_version: str
    document_registry_version: str | None = None
    registry_version_match: bool
    changes: list[FlagImportChange]
    errors: list[FlagImportProblem]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _override_view(o: feature_flags.StoredOverride | None, names: dict[str, str]) -> FlagOverrideView | None:
    if o is None:
        return None
    return FlagOverrideView(
        enabled=o.enabled,
        allow_roles=list(o.allow_roles) if o.allow_roles is not None else None,
        allow_users=[FlagUserRef(id=u, username=names.get(u)) for u in o.allow_users]
        if o.allow_users is not None else None,
        note=o.note,
        updated_by=names.get(o.updated_by) if o.updated_by else None,
        updated_at=_iso(o.updated_at),
    )


def _item(flag: feature_flags.AdminFlag, names: dict[str, str]) -> FlagItem:
    return FlagItem(
        key=flag.spec.key, description=flag.spec.description, ceiling_env=flag.spec.ceiling_env,
        ceiling=flag.ceiling, default=flag.spec.default, override=_override_view(flag.override, names),
        effective=flag.effective, effective_for_me=flag.effective_for_me,
    )


def _export_override(o: feature_flags.StoredOverride | None, names: dict[str, str]) -> FlagExportOverride | None:
    if o is None:
        return None
    return FlagExportOverride(
        enabled=o.enabled,
        allow_roles=list(o.allow_roles) if o.allow_roles is not None else None,
        allow_users=[names.get(u, u) for u in o.allow_users] if o.allow_users is not None else None,
        note=o.note,
    )


def _flag_error(exc: feature_flags.FlagError) -> AppError:
    if isinstance(exc, feature_flags.UnknownFlagError):
        return AppError(404, exc.code, str(exc))
    return AppError(422, exc.code, str(exc))


def _unavailable(exc: Exception) -> AppError:
    logger.error("功能旗標：讀寫 research.feature_flag 失敗：%r", exc)
    return AppError(503, "flags_unavailable", "功能旗標暫時無法讀取（資料庫連線失敗）；各功能維持預設值")


async def _item_for(key: str, me: User) -> FlagItem:
    async with deps.SessionFactory() as session:
        view = await feature_flags.admin_view(session, me)
    return _item(next(f for f in view.flags if f.spec.key == key), view.usernames)


def _plan_response(plan: feature_flags.ImportPlan, *, dry_run: bool, applied: bool) -> FlagImportResponse:
    return FlagImportResponse(
        dry_run=dry_run, applied=applied, registry_version=feature_flags.REGISTRY_VERSION,
        document_registry_version=plan.document_registry_version,
        registry_version_match=plan.registry_version_match,
        changes=[FlagImportChange(key=c.key, action=c.action, before=_export_override(c.before, plan.usernames),
                                  after=_export_override(c.after, plan.usernames)) for c in plan.changes],
        errors=[FlagImportProblem(key=e.key, code=e.code, detail=e.detail) for e in plan.errors],
    )


@router.get("/api/admin/flags", response_model=FlagListResponse)
async def list_flags(me: User = Depends(authz.current_user)):
    """每個登記旗標：環境變數上限、registry 預設、DB 覆寫（含作用域與註記）、對所有人的實際狀態與對我的實際值。"""
    try:
        async with deps.SessionFactory() as session:
            view = await feature_flags.admin_view(session, me)
    except Exception as exc:  # noqa: BLE001 — DB 不可用：說清楚，不假裝是預設值
        raise _unavailable(exc) from exc
    return FlagListResponse(
        registry_version=feature_flags.REGISTRY_VERSION,
        items=[_item(f, view.usernames) for f in view.flags],
        ignored_keys=view.ignored_keys,
    )


@router.get("/api/admin/flags/export", response_model=FlagExportDocument)
async def export_flags():
    """匯出目前的覆寫（JSON）：registry 版本、每個登記旗標的覆寫與作用域；使用者以帳號名稱表示。"""
    try:
        async with deps.SessionFactory() as session:
            doc = await feature_flags.export_document(session)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    return FlagExportDocument.model_validate(doc)


@router.post("/api/admin/flags/import", response_model=FlagImportResponse, dependencies=_OPERATE)
async def import_flags(body: FlagImportRequest, actor: User = Depends(authz.current_user)):
    """匯入匯出檔。`dry_run=true`（預設）只回差異與錯誤；`false` 在同一筆交易套用，每個變更各寫一筆 `flag.update`。

    有任何錯誤（未知 key、找不到的帳號名稱、不合法的作用域）時套用整份不寫，回 422 `flag_import_invalid`
    （另帶 `errors`）；dry-run 照樣 200、把錯誤列在 `errors`。registry 版本不同只提示（`registry_version_match`）。
    """
    document = body.document.model_dump()
    async with deps.SessionFactory() as session:
        if body.dry_run:
            plan = await feature_flags.plan_import(session, document)
            return _plan_response(plan, dry_run=True, applied=False)
        plan = await feature_flags.apply_import(session, document, actor_id=actor.id)
        if plan.errors:
            await session.rollback()
            raise AppError(422, "flag_import_invalid", "匯入檔有錯誤，沒有套用任何變更",
                           extra={"errors": [dataclasses.asdict(e) for e in plan.errors]})
        await session.commit()
    feature_flags.invalidate()
    changed = [c.key for c in plan.changes if c.action != "unchanged"]
    logger.info("管理操作 actor=%s action=flag.import changed=%s", actor.username, ",".join(changed) or "-")
    return _plan_response(plan, dry_run=False, applied=True)


@router.put("/api/admin/flags/{key}", response_model=FlagItem, dependencies=_OPERATE)
async def set_flag(key: str, body: FlagUpdateRequest, actor: User = Depends(authz.current_user)):
    """建立或更新一筆覆寫；與目前相同時不寫、不記稽核。上限關閉時照樣寫入，但實際值仍是關。"""
    async with deps.SessionFactory() as session:
        try:
            _before, _after, changed = await feature_flags.set_override(
                session, key, enabled=body.enabled, allow_roles=body.allow_roles, allow_users=body.allow_users,
                note=body.note, actor_id=actor.id,
            )
        except feature_flags.FlagError as exc:
            await session.rollback()
            raise _flag_error(exc) from exc
        await session.commit()
    feature_flags.invalidate()
    if changed:
        logger.info("管理操作 actor=%s action=flag.update target=%s", actor.username, key)
    return await _item_for(key, actor)


@router.delete("/api/admin/flags/{key}", response_model=FlagItem, dependencies=_OPERATE)
async def clear_flag(key: str, actor: User = Depends(authz.current_user)):
    """刪掉覆寫，回到 registry 預設；本來就沒有覆寫時什麼都不做。"""
    async with deps.SessionFactory() as session:
        try:
            _before, changed = await feature_flags.clear_override(session, key, actor_id=actor.id)
        except feature_flags.FlagError as exc:
            await session.rollback()
            raise _flag_error(exc) from exc
        await session.commit()
    feature_flags.invalidate()
    if changed:
        logger.info("管理操作 actor=%s action=flag.update target=%s cleared", actor.username, key)
    return await _item_for(key, actor)
