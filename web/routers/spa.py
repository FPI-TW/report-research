# web/routers/spa.py
"""SPA 與靜態資產服務：/、/monitor、/help（導向 SPA）、/app 深連結殼，與
/app/assets、/static 兩個 StaticFiles Mount。純服務、無業務邏輯。

從 web/server.py 拆出（收尾）。**整組（路由＋Mount＋掛載順序）刻意留在同一模組**：
Mount 只能掛在 app 上（APIRouter 不支援），而 /app/assets 的 Mount 必須註冊在
catch-all /app/{spa_path} 之前，否則每個 Vite 雜湊資產都會落到 catch-all、回 index.html
（HTTP 200＋text/html，SPA 整頁白掉）。把 Mount 與 catch-all 拆到兩個檔會讓這條順序
依賴跨檔而脆弱（見 tests/test_pre_split_guards.py 的守門），故以 configure(app) 在單一
模組內自帶正確順序，由 server.py 呼叫。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from web import deps

SPA_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

router = APIRouter()


class _NoCacheStatic(StaticFiles):
    """/static 下的 css/js/圖片同樣以 no-cache 重新驗證，確保 tokens.css / utils.js 改版不卡舊版。"""

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


class _ImmutableStatic(StaticFiles):
    """Vite 內容雜湊資產（/app/assets/*）長快取：hash 變則 URL 變，故可 immutable。"""

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        return resp


@router.get("/")
async def index():
    # 舊 vanilla 首頁已退場，根路徑導向 SPA 檢索頁
    return RedirectResponse("/app/search", status_code=302)


@router.get("/monitor")
async def monitor():
    # 舊 vanilla 監控頁已退場，導向 SPA 監控頁（保留舊路徑/書籤相容）
    return RedirectResponse("/app/monitor", status_code=302)


@router.get("/help")
async def help_page():
    # 舊 vanilla 說明頁已退場，導向 SPA 說明頁
    return RedirectResponse("/app/help", status_code=302)


@router.get("/app")
@router.get("/app/{spa_path:path}")
async def spa_shell(spa_path: str = ""):
    """SPA shell：所有 /app/* 深連結回同一份 index.html，交給 client 端路由。"""
    index_file = SPA_DIST / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=503, detail="SPA 尚未 build（frontend/dist 不存在）")
    return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


def configure(app) -> None:
    """把 SPA/靜態服務掛到 app：先掛 /app/assets Mount，再納入本 router（含 catch-all），
    最後掛 /static。順序即安全——assets Mount 必須贏過 /app/{spa_path}。"""
    if (SPA_DIST / "assets").is_dir():
        app.mount(
            "/app/assets",
            _ImmutableStatic(directory=SPA_DIST / "assets", check_dir=False),
            name="spa-assets",
        )
    app.include_router(router)
    app.mount("/static", _NoCacheStatic(directory=deps.STATIC_DIR), name="static")
