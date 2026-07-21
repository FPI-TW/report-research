"""查詢測試網頁後端。

BGE-M3 模型在啟動時載入並常駐記憶體，查詢只需 embed + pgvector 檢索。
啟動：uv run uvicorn web.server:app --host 0.0.0.0 --port 8097
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.env_loader import load_env_file  # noqa: E402

load_env_file(Path(__file__).resolve().parents[1] / ".env")

from web import deps  # noqa: E402
from web.routers import radar as radar_routes  # noqa: E402
from web.routers import reading as reading_routes  # noqa: E402
from web.routers import report_file as report_file_routes  # noqa: E402
from web.routers import monitor as monitor_routes  # noqa: E402
from web.routers import search as search_routes  # noqa: E402
from web.routers import qa_history as qa_history_routes  # noqa: E402
from web.routers import ask as ask_routes  # noqa: E402

from app.config import get_settings  # noqa: E402
from web import auth  # noqa: E402

# 渲染一律經 report 的分派層（依 REPORT_RENDERER 選 typst/weasyprint，失敗回退）；
# 直接 import pdf.render_report_pdf 會讓 PDF 重建繞過分派、永遠是 WeasyPrint 版。
from app.services.report import (  # noqa: E402
    fetch_report_doc,
    generate_report,
    render_report_pdf,
    write_report_pdf,
)

# 跨組共用符號一律以 deps.X 存取；此處別名僅為既有測試的 `from web.server import ...`。
STATIC_DIR = deps.STATIC_DIR
SPA_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
logger = logging.getLogger(__name__)


def _static_page(name: str) -> FileResponse:
    """回傳 HTML 殼，並標記 no-cache（瀏覽器每次重新向伺服器取用，改版後同事免強制重整即見新版）。

    註：route-level 的 FileResponse 不會對 If-None-Match/If-Modified-Since 做 304 短路，
    每次都回 200 全量 body（HTML 約數十 KB，區網成本可忽略）。/static 下的 css/js 由
    _NoCacheStatic（StaticFiles）服務，才有條件式 304 重新驗證。
    """
    return FileResponse(deps.STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


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


async def _warmup_models() -> None:
    """依序（非並行）暖機 embed 與 rerank 模型。

    必須依序：transformers 首次 import 是 lazy-module 初始化，embed（經 FlagEmbedding）
    與 rerank（直接 import）兩執行緒同時首次 import 會競態出
    ImportError: cannot import name 'is_torch_npu_available'，暖機每次開機全滅。
    rerank 冷載入實測 44-52s：不預載則首個帶 rerank 的請求把載入算進逾時預算而 fail-open。
    """
    try:
        await asyncio.to_thread(deps.embed_texts, ["warmup"])
    except Exception:
        # embed 暖機失敗不阻斷 rerank 暖機；embed 無熔斷、首個查詢會 lazy 重試。
        logger.exception("embedding warmup failed")
    _s = get_settings()
    if _s.ask_rerank_enabled or _s.report_rerank_enabled:
        await asyncio.to_thread(deps.rerank_warmup)  # 失敗由 rerank 模組熔斷處理，不拋


def _log_warmup_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("model warmup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在背景暖機，避免啟動期間 socket 尚未 bind 導致外部完全無法連線。
    warmup_task = asyncio.create_task(_warmup_models())
    warmup_task.add_done_callback(_log_warmup_result)
    app.state.embed_warmup_task = warmup_task
    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
            try:
                await warmup_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="研報市場標籤檢索", lifespan=lifespan)

# ───── 認證閘門(deny-by-default;白名單僅 /login)─────
_AUTH_ALLOWLIST = {"/login"}
_AUTH_PREFIX_ALLOWLIST = ("/app/assets/",)


def _auth_allowed(path: str) -> bool:
    return (
        path in _AUTH_ALLOWLIST
        or path == "/app/assets"
        or any(path.startswith(prefix) for prefix in _AUTH_PREFIX_ALLOWLIST)
    )


def _safe_next(raw: str | None) -> str:
    """只接受同源相對路徑：必須以單一 '/' 開頭，拒絕 //、/\\、schema URL、控制字元（含 CRLF）。否則回 '/'。"""
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    if "://" in raw:
        return "/"
    # 拒絕所有 C0 控制字元（含 CR/LF/TAB/NUL）與 DEL，使白名單自身完備（縱深防禦）
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        return "/"
    return raw


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if _auth_allowed(path):
        return await call_next(request)
    now = int(time.time())
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), now):
        response = await call_next(request)
        if path != "/logout":  # 登出會清 cookie,勿在此又刷新蓋回
            auth.set_session_cookie(
                response,
                now,
                secure=auth.request_is_secure(request),
            )
        return response
    if path.startswith("/api/"):
        return JSONResponse({"detail": "未登入"}, status_code=401)
    return RedirectResponse("/login", status_code=302)










# /api/stats 與 /api/progress 已拆至 web/routers/monitor.py
app.include_router(monitor_routes.router)




@app.get("/monitor")
async def monitor():
    # 舊 vanilla 監控頁已退場，導向 SPA 監控頁（保留舊路徑/書籤相容）
    return RedirectResponse("/app/monitor", status_code=302)


@app.get("/help")
async def help_page():
    # 舊 vanilla 說明頁已退場，導向 SPA 說明頁
    return RedirectResponse("/app/help", status_code=302)


# /api/search、/api/reports、/api/markets 已拆至 web/routers/search.py
app.include_router(search_routes.router)


# 觀點雷達 4 條路由已拆至 web/routers/radar.py（見下方 include_router）。
app.include_router(radar_routes.router)


# ───── 研報閱讀頁（/api/reading/*）已拆至 web/routers/reading.py ─────
app.include_router(reading_routes.router)







# 研報生成比問答重很多（長輸出 + PDF 排版），預設序列化避免區網多人同時生成拖垮機器。
_REPORT_SEMAPHORE = asyncio.Semaphore(int(os.getenv("REPORT_SEMAPHORE", "1")))


class ReportRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    qa_id: str | None = None


_sse = deps._sse
SSE_HEARTBEAT_INTERVAL = deps.SSE_HEARTBEAT_INTERVAL
_with_heartbeat = deps._with_heartbeat


# RAG 問答 /api/ask、/api/ask/stop 已拆至 web/routers/ask.py
app.include_router(ask_routes.router)


_valid_uuid = deps._valid_uuid






@app.post("/api/report")
async def report(req: ReportRequest):
    """深度研報生成：深度檢索 → 串流撰寫 → 渲染 PDF。回 text/event-stream。

    事件序：status(retrieving/writing/rendering) → sources → token… → done{download_url}。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    if req.qa_id is not None and not deps._valid_uuid(req.qa_id):
        raise HTTPException(status_code=400, detail="qa_id 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")

    async def gen():
        async with _REPORT_SEMAPHORE:
            try:
                async for event, payload in generate_report(
                    question, filters={},
                    conversation_id=req.conversation_id, qa_id=req.qa_id,
                ):
                    yield deps._sse(event, payload)
            except Exception:
                logger.exception("report failed")
                yield deps._sse("error", {"detail": "研報生成發生錯誤"})

    return StreamingResponse(
        deps._with_heartbeat(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/report-doc/{report_id}/pdf")
async def report_doc_pdf(report_id: str):
    """下載生成的研報 PDF；pdf_path 不存在時由 markdown 即時重建。"""
    if not deps._valid_uuid(report_id):
        raise HTTPException(status_code=404, detail="report not found")
    doc = await fetch_report_doc(report_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="report not found")
    path = doc.get("pdf_path")
    if not path or not os.path.isfile(path):
        pdf_bytes = await asyncio.to_thread(
            render_report_pdf, doc["markdown"], title=doc["title"],
            meta={"date": doc.get("date") or "", "question": doc.get("question")},
        )
        path = await asyncio.to_thread(write_report_pdf, report_id, pdf_bytes)
    return FileResponse(
        path, media_type="application/pdf",
        filename=f"report-{report_id[:8]}.pdf",
        headers={"Cache-Control": "no-cache"},
    )


# 問答歷史/回饋/對話串 9 條路由已拆至 web/routers/qa_history.py
app.include_router(qa_history_routes.router)


# 舊 modal 原始檔資料源（/api/report/{id}/full、/file）已拆至 web/routers/report_file.py
app.include_router(report_file_routes.router)



@app.get("/login")
async def login_page(request: Request):
    nxt = _safe_next(request.query_params.get("next"))
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), int(time.time())):
        return RedirectResponse(nxt, status_code=302)
    return _static_page("login.html")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    nxt = _safe_next(next)
    err_q = "&next=" + quote(nxt, safe="") if nxt != "/" else ""
    if not auth.login_allowed(request):
        return RedirectResponse(f"/login?error=insecure{err_q}", status_code=303)
    now = int(time.time())
    ip = auth.client_ip(request)
    if auth.is_locked(ip, now):
        return RedirectResponse(f"/login?error=locked{err_q}", status_code=303)
    if auth.check_credentials(username, password):
        auth.reset(ip)
        resp = RedirectResponse(nxt, status_code=303)
        auth.set_session_cookie(resp, now, secure=auth.request_is_secure(request))
        return resp
    auth.record_failure(ip, now)
    return RedirectResponse(f"/login?error=1{err_q}", status_code=303)


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp


@app.get("/")
async def index():
    # 舊 vanilla 首頁已退場，根路徑導向 SPA 檢索頁
    return RedirectResponse("/app/search", status_code=302)


# ───── SPA（/app 子路徑；shell + 雜湊資產，純服務無業務邏輯）─────
if (SPA_DIST / "assets").is_dir():
    app.mount(
        "/app/assets",
        _ImmutableStatic(directory=SPA_DIST / "assets", check_dir=False),
        name="spa-assets",
    )


@app.get("/app")
@app.get("/app/{spa_path:path}")
async def spa_shell(spa_path: str = ""):
    """SPA shell：所有 /app/* 深連結回同一份 index.html，交給 client 端路由。"""
    index = SPA_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="SPA 尚未 build（frontend/dist 不存在）")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


app.mount("/static", _NoCacheStatic(directory=deps.STATIC_DIR), name="static")
