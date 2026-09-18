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
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# dev_mode **必須排在下面那行 load_env_file() 之前被 import**：它在 import 期把
# DEV_NO_AUTH 的值快照下來，而 load_env_file 會把 repo 根 .env 灌進 os.environ。
# 順序反過來的話，把 DEV_NO_AUTH=1 寫進 .env 就等於永久關掉這個對外站台的登入——
# 而且沒有任何錯誤訊息。詳見 web/dev_mode.py 的模組 docstring（放行的三個條件）。
# tests/test_dev_mode.py 靜態釘住這個順序。
from web import dev_mode  # noqa: E402
from web.env_loader import load_env_file  # noqa: E402

load_env_file(Path(__file__).resolve().parents[1] / ".env")

# 必須在載入 .env 之後（要讀 LOG_LEVEL）、且在任何 app.services.* 之前：那些模組在
# import 期就 getLogger，而沒有這一行的話 root 沒有 handler、effective level 是
# WARNING，於是所有 logger.info 在呼叫點被丟棄（qa_timing 分段耗時遙測寫了好幾個
# 里程碑，生產 journald 近 14 天 0 筆）。詳見 app/logging_setup.py。
from app.logging_setup import configure_logging  # noqa: E402

configure_logging()

from app.config import get_settings  # noqa: E402
from app.services import db  # noqa: E402
from web import (
    auth,  # noqa: E402
    concurrency,  # noqa: E402
    deps,  # noqa: E402
)
from web.routers import ask as ask_routes  # noqa: E402
from web.routers import auth_pages as auth_pages_routes  # noqa: E402
from web.routers import brief as brief_routes  # noqa: E402
from web.routers import health as health_routes  # noqa: E402
from web.routers import monitor as monitor_routes  # noqa: E402
from web.routers import qa_history as qa_history_routes  # noqa: E402
from web.routers import radar as radar_routes  # noqa: E402
from web.routers import reading as reading_routes  # noqa: E402
from web.routers import report_file as report_file_routes  # noqa: E402
from web.routers import search as search_routes  # noqa: E402
from web.routers import spa as spa_routes  # noqa: E402

# 跨組共用符號一律以 deps.X 存取；此處別名僅為既有測試的 `from web.server import ...`。
STATIC_DIR = deps.STATIC_DIR
logger = logging.getLogger(__name__)


async def _warmup_models() -> None:
    """依序（非並行）暖機 embed 與 rerank 模型。

    必須依序：transformers 首次 import 是 lazy-module 初始化，embed（經 FlagEmbedding）
    與 rerank（直接 import）兩執行緒同時首次 import 會競態出
    ImportError: cannot import name 'is_torch_npu_available'，暖機每次開機全滅。
    rerank 冷載入實測 44-52s：不預載則首個帶 rerank 的請求把載入算進逾時預算而 fail-open。
    """
    # 開發捷徑：BGE-M3 ＋ reranker 冷載入合計約一分鐘，而 `make serve` 沒有
    # `--reload`，所以改一行 Python 就要再等一次。**預設是暖機**——生產不受影響，
    # 且刻意用 os.environ 而非 config：這是「這次啟動」的一次性選擇，不是部署設定
    # （寫進 .env 會讓某次 debug 的旗標永久留在生產機上，那正是 unit 漂掉的方式）。
    if os.environ.get("SKIP_WARMUP") == "1":
        logger.warning("SKIP_WARMUP=1：跳過模型暖機（首個查詢會 lazy 載入，慢但可用）")
        return
    try:
        await asyncio.to_thread(deps.embed_texts, ["warmup"])
    except Exception:
        # embed 暖機失敗不阻斷 rerank 暖機；embed 無熔斷、首個查詢會 lazy 重試。
        logger.exception("embedding warmup failed")
    _s = get_settings()
    if _s.ask_rerank_enabled:
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
    # 併發上限與背景 run 登錄表都是 per-process 狀態，多 worker 會讓上限翻倍、模型
    # 記憶體翻倍、研報重連隨機 404。偵測得到就拒絕啟動（fail-closed），偵測不到就
    # 放行——判準與已知缺口見 web/concurrency.py。
    workers = concurrency.assert_single_worker()
    # 認證關掉這件事一定要在啟動時說出來（開著卻沒人知道是這種旗標最常見的失事方式）。
    dev_mode.log_banner()
    # 用 warning 而非 info 不是因為它是警告，是因為本 repo 從未初始化 logging，
    # root logger 走 logging.lastResort（level=WARNING）——info 會直接進黑洞。
    # 把「有效上限」印出來，是為了讓「上限是多少」不必再靠讀原始碼推。
    logger.warning(
        "併發設定：workers=%s；%s",
        workers if workers is not None else "未偵測到（假定單一行程）",
        "；".join(g.describe() for g in concurrency.registered_gates()) or "無閘門",
    )
    # pgvector 版本：太舊會讓每次檢索與每次開閱讀頁都 500（`hnsw.iterative_scan`
    # 在 0.8 以前不存在）。fail-closed，但 DB 連不上時放行——那是 /healthz 的職責
    # （回 503），不該在這裡升級成「App 起不來」。
    pgvector_version = await db.assert_pgvector_version()
    logger.warning(
        "pgvector 版本：%s",
        pgvector_version or "查不到（DB 不可用，交由 /healthz 回報）",
    )
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

# ───── 認證閘門(deny-by-default;白名單僅 /login 與 /healthz)─────
# /healthz 必須免認證：它存在的理由就是讓**外部**監控能分辨「DB 掛了」與「站台正常」。
# 登入路徑完全不碰 DB，所以 DB 掛掉時登入仍會成功——沒有這個豁免，探測只會拿到
# 302 導向 /login，與不存在的路由完全相同。回應內容刻意極簡（見 routers/health.py）。
_AUTH_ALLOWLIST = {"/login", "/healthz"}
_AUTH_PREFIX_ALLOWLIST = ("/app/assets/",)


def _auth_allowed(path: str) -> bool:
    return (
        path in _AUTH_ALLOWLIST
        or path == "/app/assets"
        or any(path.startswith(prefix) for prefix in _AUTH_PREFIX_ALLOWLIST)
    )


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if _auth_allowed(path):
        return await call_next(request)
    # 開發模式：本機直連且未經任何代理時免登入（三個條件見 web/dev_mode.py）。
    # 刻意不發 session cookie——放行是這一個請求的事，不留下可帶走的憑證。
    if dev_mode.bypass_allowed(request):
        return await call_next(request)
    now = int(time.time())
    session = auth.parse_token(request.cookies.get(auth.COOKIE_NAME), now)
    if session is not None:
        response = await call_next(request)
        if path != "/logout":  # 登出會清 cookie,勿在此又刷新蓋回
            # issued_at 必須沿用原 token 的簽發時刻:滑動續期只推遲 exp,重置 iat
            # 會讓 auth.MAX_ABSOLUTE_TTL 的絕對上限每次請求都歸零＝形同不存在。
            auth.set_session_cookie(
                response,
                now,
                secure=auth.request_is_secure(request),
                issued_at=session.issued_at,
            )
        return response
    if path.startswith("/api/"):
        return JSONResponse({"detail": "未登入"}, status_code=401)
    return RedirectResponse("/login", status_code=302)










# /healthz（免認證存活探測）——必須與 _AUTH_ALLOWLIST 的白名單成對存在，
# 只掛路由而沒放行等於它永遠回 302，與不存在的路由無從分辨（那正是它要解決的問題）。
app.include_router(health_routes.router)

# /api/stats 與 /api/progress 已拆至 web/routers/monitor.py
app.include_router(monitor_routes.router)




# /api/search、/api/reports、/api/markets 已拆至 web/routers/search.py
app.include_router(search_routes.router)


# 觀點雷達 4 條路由已拆至 web/routers/radar.py（見下方 include_router）。
app.include_router(radar_routes.router)


# ───── 研報閱讀頁（/api/reading/*）已拆至 web/routers/reading.py ─────
app.include_router(reading_routes.router)


# ───── 每日簡報（/api/brief/*）：純讀取、零 LLM ─────
app.include_router(brief_routes.router)











_sse = deps._sse
SSE_HEARTBEAT_INTERVAL = deps.SSE_HEARTBEAT_INTERVAL
_with_heartbeat = deps._with_heartbeat


# RAG 問答 /api/ask、/api/ask/stop 已拆至 web/routers/ask.py
app.include_router(ask_routes.router)


_valid_uuid = deps._valid_uuid










# 問答歷史/回饋/對話串 9 條路由已拆至 web/routers/qa_history.py
app.include_router(qa_history_routes.router)


# 舊 modal 原始檔資料源（/api/report/{id}/full、/file）已拆至 web/routers/report_file.py
app.include_router(report_file_routes.router)


# 登入流程頁面路由（middleware require_login 仍在本檔，見上）
app.include_router(auth_pages_routes.router)

# SPA / 靜態服務：configure 內含 /app/assets Mount → router（catch-all）→ /static 的
# 正確掛載順序，assets Mount 必須贏過 /app/{spa_path}（見 tests/test_pre_split_guards.py）。
spa_routes.configure(app)

# 既有測試以 from web.server import 取用的符號，於拆分後在此 re-export。
SPA_DIST = spa_routes.SPA_DIST
_safe_next = auth_pages_routes._safe_next




