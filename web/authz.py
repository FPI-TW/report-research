"""授權：取目前使用者、限管理員、要求 scope／super admin／已提升權限（FastAPI dependency）。

認證（你是誰）在 `web/server.py` 的 `require_login` middleware：它驗完 session 後把
`accounts.User` 放進 `request.state.user`。這裡只做授權（你能不能做這件事），而且是
**唯一**的授權判斷點——前端的 route guard 只是不顯示沒權限的頁面，真正擋人的是這裡。

scope 的詞彙與「誰有哪些 scope」在 `app/services/accounts.py`（`effective_scopes`）。

用法：
    router = APIRouter(dependencies=[Depends(authz.require_admin)])          # 整組限管理員
    @router.get(..., dependencies=[Depends(authz.require_scope("audit.read"))])
    @router.put(..., dependencies=[Depends(authz.require_super), Depends(authz.require_elevated)])
    async def handler(user: accounts.User = Depends(authz.current_user)): ...

`tests/test_authz.py` 結構性檢查：每一條 /api/admin/*、/api/review/* 路由的 dependency 樹裡
都要有 `require_admin`，而且至少有一個帶 `__scope__` 的 dependency（`require_scope` 或 `require_super`）。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.services.accounts import ALL_SCOPES, User
from web.errors import AppError


def current_user(request: Request) -> User:
    """middleware 放進來的使用者。拿不到＝路由被掛在認證白名單裡卻要身分，是程式錯誤。"""
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="未登入")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理員權限")
    return user


def require_scope(*scopes: str):
    """回一個 dependency：必須是管理員，而且擁有全部列出的 scope。未知的 scope 在 import 期就報錯。"""
    needed = frozenset(scopes)
    unknown = needed - ALL_SCOPES
    if not needed or unknown:
        raise ValueError(f"require_scope：未知或空的 scope {sorted(unknown) or '(空)'}")

    def dependency(user: User = Depends(require_admin)) -> User:
        if not user.has_scopes(needed):
            raise AppError(403, "missing_scope", f"需要權限：{'、'.join(sorted(needed - user.scopes))}")
        return user

    dependency.__scope__ = needed  # type: ignore[attr-defined]
    dependency.__name__ = "require_scope_" + "_".join(sorted(s.replace(".", "_") for s in needed))
    return dependency


def require_super(user: User = Depends(require_admin)) -> User:
    if not user.is_super:
        raise AppError(403, "super_required", "需要 super admin 權限")
    return user


require_super.__scope__ = frozenset({"super"})  # type: ignore[attr-defined]


def require_elevated(user: User = Depends(current_user)) -> User:
    """近 10 分鐘內在這個 session 重新驗證過密碼（POST /api/admin/elevate）。"""
    if not user.is_elevated:
        raise AppError(403, "elevation_required", "這項操作需要重新驗證密碼")
    return user
