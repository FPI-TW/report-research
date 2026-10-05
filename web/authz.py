"""授權：取目前使用者、限管理員（FastAPI dependency）。

認證（你是誰）在 `web/server.py` 的 `require_login` middleware：它驗完 session 後把
`accounts.User` 放進 `request.state.user`。這裡只做授權（你能不能做這件事），而且是
**唯一**的授權判斷點——前端的 route guard 只是不顯示沒權限的頁面，真正擋人的是這裡。

用法：
    router = APIRouter(dependencies=[Depends(authz.require_admin)])   # 整組限管理員
    async def handler(user: accounts.User = Depends(authz.current_user)): ...
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.services.accounts import User


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
