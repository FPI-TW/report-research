"""查詢測試網頁後端。

BGE-M3 模型在啟動時載入並常駐記憶體，查詢只需 embed + pgvector 檢索。
啟動：uv run uvicorn web.server:app --host 0.0.0.0 --port 8097
"""

from __future__ import annotations

import asyncio
import glob as _glob
import hashlib
import logging
import os
import re
import sys
import time
from collections import Counter
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
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.env_loader import load_env_file  # noqa: E402

load_env_file(Path(__file__).resolve().parents[1] / ".env")

from web import deps  # noqa: E402

from app.services.answer import (  # noqa: E402
    OFF_TOPIC_MESSAGES,
    delete_conversation,
    get_conversation,
    history_item,
    list_conversations,
    record_feedback,
)
from app.config import get_settings  # noqa: E402
from app.services.filename import source_display  # noqa: E402
from app.services.retrieval import (  # noqa: E402
    DENSE_SCAN_SEARCH,
    LEX_CAP_SEARCH,
)
from app.services.reading.anchor import locate_chunk  # noqa: E402
from app.services.reading.schemas import (  # noqa: E402
    EpsEstimate,
    ReadingDoc,
    ReadingText,
    Signal,
    SimilarReport,
    SimilarResponse,
    Takeaway,
    ThesisDim,
)
from app.services.store import list_reports  # noqa: E402
from app.services.tagging import MARKETS, MARKET_DISPLAY  # noqa: E402
from app.services.textnorm import clean_extracted, clean_text  # noqa: E402
from web import auth  # noqa: E402

# 渲染一律經 report 的分派層（依 REPORT_RENDERER 選 typst/weasyprint，失敗回退）；
# 直接 import pdf.render_report_pdf 會讓 PDF 重建繞過分派、永遠是 WeasyPrint 版。
from app.services.report import (  # noqa: E402
    fetch_report_doc,
    generate_report,
    render_report_pdf,
    write_report_pdf,
)
from app.services.radar import (  # noqa: E402
    build_broker_history,
    build_events_page,
    build_instrument_slim,
    build_overview,
)
from app.services.radar.schemas import (  # noqa: E402
    ApiErrorResponse,
    BrokerHistoryResponse,
    Market,
    RadarEventsResponse,
    RadarInstrumentItem,
    RadarInstrumentsResponse,
    RadarOverviewResponse,
    Window,
)

# 跨組共用符號一律以 deps.X 存取；此處別名僅為既有測試的 `from web.server import ...`。
STATIC_DIR = deps.STATIC_DIR
SPA_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
logger = logging.getLogger(__name__)
SEARCH_QUERY_MAX_CHARS = 500
ASK_QUESTION_MAX_CHARS = 2000
DB_STATS_CACHE_TTL_SECONDS = 5.0
_DB_STATS_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}


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


class Passage(BaseModel):
    score: float  # 1 - cosine distance，越高越相關
    chunk_index: int
    content: str


class ReportResult(BaseModel):
    rank: int
    report_id: str
    file_hash: str  # 閱讀頁連結鍵（/app/report/:hash）
    file_name: str
    market: str | None
    source: str | None
    summary: str | None
    report_date: str | None
    report_type: str | None
    instrument_types: list[str] | None
    relates_stock: bool | None
    relates_futures: bool | None
    stock_targets: list[str] | None
    futures_targets: list[str] | None
    best_score: float
    match_count: int
    passages: list[Passage]


class MarketFacet(BaseModel):
    market: str
    count: int


class SearchResponse(BaseModel):
    query: str
    market: str | None
    total: int
    # 命中集合的市場組成，於切頁前對 ranked 全量計算。
    # 注意：ranked 已套用 market 篩選，故選定市場時本欄只會有該市場——
    # 要得知其他市場的命中數需再跑一次未篩選的檢索，成本翻倍，故不做。
    market_facets: list[MarketFacet] = []
    results: list[ReportResult]


class AskRequest(BaseModel):
    question: str = Field(..., max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    relates_stock: bool | None = None
    relates_futures: bool | None = None
    report_type: str | None = None
    k: int = 8
    regenerate_of: str | None = None
    edit_of: str | None = None
    request_id: str | None = None


class FeedbackRequest(BaseModel):
    qa_id: str
    value: str  # 'like' | 'dislike'


class ReportListItem(BaseModel):
    report_id: str
    file_hash: str  # 閱讀頁連結鍵（/app/report/:hash）
    file_name: str
    market: str | None
    source: str | None
    summary: str | None
    report_date: str | None
    report_type: str | None
    instrument_types: list[str] | None
    relates_stock: bool | None
    relates_futures: bool | None
    stock_targets: list[str] | None
    futures_targets: list[str] | None


class ReportListResponse(BaseModel):
    total: int
    offset: int
    items: list[ReportListItem]


async def _fetch_db_stats_snapshot() -> dict:
    async with deps.SessionFactory() as session:
        total_reports = (
            await session.execute(text("SELECT count(*) FROM research.research_report"))
        ).scalar_one()
        total_chunks = (
            await session.execute(text("SELECT count(*) FROM research.report_chunk"))
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT market, count(*) c FROM research.research_report "
                    "GROUP BY market ORDER BY c DESC"
                )
            )
        ).all()
        instr_rows = (
            await session.execute(
                text(
                    "SELECT unnest(instrument_types) it, count(*) c "
                    "FROM research.research_report "
                    "WHERE instrument_types IS NOT NULL "
                    "GROUP BY it ORDER BY c DESC"
                )
            )
        ).all()
        type_rows = (
            await session.execute(
                text(
                    "SELECT report_type, count(*) c "
                    "FROM research.research_report "
                    "WHERE report_type IS NOT NULL "
                    "GROUP BY report_type ORDER BY c DESC"
                )
            )
        ).all()
        s_done, s_total = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE summary IS NOT NULL), count(*) "
                    "FROM research.research_report "
                    "WHERE full_text IS NOT NULL AND is_research IS NOT FALSE"
                )
            )
        ).first()
    return {
        "total_reports": total_reports,
        "total_chunks": total_chunks,
        "markets": [{"market": m, "count": c} for m, c in rows],
        "instrument_types": [{"type": t, "count": c} for t, c in instr_rows],
        "report_types": [{"type": t, "count": c} for t, c in type_rows],
        "summary_done": int(s_done),
        "summary_total": int(s_total),
    }


async def _db_stats_snapshot() -> dict:
    now = time.monotonic()
    cached = _DB_STATS_CACHE.get("data")
    expires_at = float(_DB_STATS_CACHE.get("expires_at", 0.0) or 0.0)
    if cached is not None and now < expires_at:
        return cached  # type: ignore[return-value]
    data = await _fetch_db_stats_snapshot()
    _DB_STATS_CACHE["data"] = data
    _DB_STATS_CACHE["expires_at"] = now + DB_STATS_CACHE_TTL_SECONDS
    return data


@app.get("/api/stats")
async def stats():
    snapshot = await _db_stats_snapshot()
    return {
        "total_reports": snapshot["total_reports"],
        "total_chunks": snapshot["total_chunks"],
        "markets": snapshot["markets"],
        "instrument_types": snapshot["instrument_types"],
        "report_types": snapshot["report_types"],
        "username": auth.ACCESS_USERNAME,
    }


# ───── 進度監控（/monitor 儀表板資料來源）─────
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_TAG_RE = re.compile(r"(\d+)/(\d+)\s+ok\+skip=(\d+)\s+fail=(\d+)")
_ING_RE = re.compile(r"ingested=(\d+)\s+chunks=(\d+)\s+fail=(\d+)")
_ORCH_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+===\s+resume\s+"
    r"(?P<state>start|done)(?:\s+\(pid=(?P<pid>\d+)\))?\s+===$"
)


def _latest_log(pattern: str) -> Path | None:
    files = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _tail_text(path: Path, nbytes: int = 65536) -> str:
    """讀檔尾數十 KB，避免長 log 整檔載入。"""
    try:
        with open(path, "rb") as fh:
            try:
                fh.seek(-nbytes, os.SEEK_END)
            except OSError:
                fh.seek(0)
            return fh.read().decode("utf-8", "ignore")
    except OSError:
        return ""


TAGS_DIR = DATA_DIR / "tags"


def _count_tag_files() -> int:
    """即時 tag 檔數：每完成一個標註就 +1，比 log（每 100 筆才印）連續細緻。"""
    try:
        return sum(1 for e in os.scandir(TAGS_DIR) if e.name.endswith(".json"))
    except OSError:
        return 0


def _tag_progress() -> dict | None:
    f = _latest_log("tag_run_*.log")
    if not f:
        return None
    tail = _tail_text(f)
    last = None
    for last in _TAG_RE.finditer(tail):
        pass
    m_total = re.search(r"candidates:\s*(\d+)", tail)
    total = int(m_total.group(1)) if m_total else (int(last.group(2)) if last else 0)
    if not total:
        return None
    # done 以「即時 tag 檔案數」為準（每秒數個、連續成長）；log 的掃描位置會把已標的重數
    done = min(_count_tag_files(), total)
    # 未標數＝缺 tag 檔者（標完即 0）。不取 log 的歷史 fail，避免事後手動補標後仍顯示舊值
    untagged = max(0, total - done)
    return {
        "done": done,
        "total": total,
        "fail": untagged,
        "pct": round(done / total * 100, 2) if total else 0.0,
    }


def _ingest_progress() -> dict | None:
    f = _latest_log("ingest_run_*.log")
    if not f:
        return None
    last = None
    for last in _ING_RE.finditer(_tail_text(f)):
        pass
    if last is None:
        return {"ingested": 0, "chunks": 0, "fail": 0}
    return {
        "ingested": int(last.group(1)),
        "chunks": int(last.group(2)),
        "fail": int(last.group(3)),
    }


def _proc_alive(needle: str) -> bool:
    """掃 /proc 比對 cmdline，判斷背景管線是否在跑（不依賴 docker / pgrep）。"""
    target = needle.encode()
    for p in _glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as fh:
                if target in fh.read().replace(b"\x00", b" "):
                    return True
        except OSError:
            continue
    return False


def _orchestrator_last() -> str | None:
    f = _latest_log("resume_orchestrator_*.log")
    if not f:
        return None
    lines = [ln for ln in _tail_text(f).splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _parse_orchestrator_entry(line: str | None) -> dict | None:
    raw = (line or "").strip()
    if not raw:
        return None
    m = _ORCH_RE.match(raw)
    if m:
        state = m.group("state")
        return {
            "raw": raw,
            "timestamp": m.group("ts"),
            "status": "running" if state == "start" else "done",
            "label": "編排器執行中" if state == "start" else "編排器已完成",
        }
    return {
        "raw": raw,
        "timestamp": None,
        "status": "unknown",
        "label": "編排器狀態",
    }


def _gather_runtime() -> dict:
    """同步蒐集（log 解析 + /proc 掃描），於 to_thread 中執行不阻塞事件迴圈。"""
    return {
        "tagging": _tag_progress(),
        "ingest": _ingest_progress(),
        "pipelines": {
            "web": True,
            "ingest": _proc_alive("ingest_all.py"),
            "tag": _proc_alive("tag_all_cli.py"),
            "summaries": _proc_alive("generate_summaries.py"),
        },
        "orchestrator": _parse_orchestrator_entry(_orchestrator_last()),
    }


@app.get("/api/progress")
async def progress():
    snapshot = await _db_stats_snapshot()
    runtime = await asyncio.to_thread(_gather_runtime)
    s_done = int(snapshot["summary_done"])
    s_total = int(snapshot["summary_total"])
    return {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "db": {
            "reports": snapshot["total_reports"],
            "chunks": snapshot["total_chunks"],
            "markets": snapshot["markets"],
        },
        "summary": {
            "done": s_done,
            "total": s_total,
            "remaining": max(0, s_total - s_done),
            "pct": round(s_done / s_total * 100, 2) if s_total else 0.0,
        },
        **runtime,
    }


@app.get("/monitor")
async def monitor():
    # 舊 vanilla 監控頁已退場，導向 SPA 監控頁（保留舊路徑/書籤相容）
    return RedirectResponse("/app/monitor", status_code=302)


@app.get("/help")
async def help_page():
    # 舊 vanilla 說明頁已退場，導向 SPA 說明頁
    return RedirectResponse("/app/help", status_code=302)


@app.get("/api/markets")
async def markets():
    return {"markets": MARKETS}


@app.get("/api/radar/instruments", response_model=RadarInstrumentsResponse)
async def radar_instruments(
    market: Market | None = Query(None, max_length=16),
    q: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    with_consensus: bool = Query(True),
):
    """觀點雷達「選標的」目錄：有可展示訊號的標的清單（獨立頁選單資料源）。

    with_consensus=True（預設）時，當頁每檔附精簡共識預覽（立場/分佈/淨變動/目標價），
    以單次批次查詢計算，避免逐檔 N+1。
    """
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        total, rows = await deps.list_radar_instruments(
            session, market=market, q=q, limit=limit, offset=offset
        )
        signals_by_key: dict[tuple[str, str], list] = {}
        if with_consensus and rows:
            keys = [(r.market, r.instrument_code) for r in rows]
            signals_by_key = await deps.fetch_signals_for_instruments(session, keys)
    consensus_by_key = {
        key: build_instrument_slim(sigs) for key, sigs in signals_by_key.items()
    }
    items = [
        RadarInstrumentItem(
            market=r.market,
            market_display=MARKET_DISPLAY.get(r.market),
            instrument_code=r.instrument_code,
            instrument_name=r.instrument_name,
            broker_count=r.broker_count,
            report_count=r.report_count,
            latest_report_date=r.latest_report_date.isoformat() if r.latest_report_date else None,
            coverage_state=r.coverage_state,
            consensus=consensus_by_key.get((r.market, r.instrument_code)),
        )
        for r in rows
    ]
    logger.info(
        "radar instruments total=%d q=%s market=%s consensus=%s elapsed_ms=%.1f",
        total, q, market, with_consensus, (time.monotonic() - t0) * 1000,
    )
    next_offset = offset + len(items)
    has_more = next_offset < total
    return RadarInstrumentsResponse(
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
        items=items,
    )


@app.get(
    "/api/instrument/{code:path}/radar/events",
    response_model=RadarEventsResponse,
    responses={404: {"model": ApiErrorResponse}},
)
async def instrument_radar_events(
    code: str,
    market: Market = Query(..., min_length=1, max_length=16),
    window: Window = Query("90"),
    limit: int = Query(12, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """與總覽同源的完整近期事件，提供穩定 offset 分頁。"""
    code = code.strip()
    if not code or len(code) > 16:
        raise HTTPException(status_code=422, detail="code 非法")
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        coverage = await deps.fetch_coverage_counts(session, market, code)
        if not coverage.has_reports:
            raise HTTPException(status_code=404, detail="instrument not found")
        signals = await deps.fetch_instrument_signals(session, market, code)
    resp = build_events_page(
        signals,
        market=market,
        code=code,
        window=window,
        limit=limit,
        offset=offset,
    )
    logger.info(
        "radar events code=%s market=%s window=%s total=%d offset=%d limit=%d elapsed_ms=%.1f",
        code, market, window, resp.total, offset, limit,
        (time.monotonic() - t0) * 1000,
    )
    return resp


@app.get("/api/instrument/{code:path}/radar", response_model=RadarOverviewResponse)
async def instrument_radar(
    code: str,
    market: Market = Query(..., min_length=1, max_length=16),
    window: Window = Query("90"),
):
    """跨券商總覽：共識快照 + 四維論點 + 近期事件 + 券商清單（讀取不呼叫 LLM）。"""
    code = code.strip()
    if not code or len(code) > 16:
        raise HTTPException(status_code=422, detail="code 非法")
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        coverage = await deps.fetch_coverage_counts(session, market, code)
        # 完全無研報 → 404；有研報但尚未擷取訊號 → 200 pending_extraction 空狀態
        if not coverage.has_reports:
            raise HTTPException(status_code=404, detail="instrument not found")
        signals = await deps.fetch_instrument_signals(session, market, code)
    resp = build_overview(signals, coverage, window=window)
    logger.info(
        "radar overview code=%s market=%s window=%s signals=%d state=%s events=%d elapsed_ms=%.1f",
        code, market, window, len(signals), resp.coverage.state,
        resp.recent_events_total, (time.monotonic() - t0) * 1000,
    )
    return resp


@app.get(
    "/api/instrument/{code:path}/radar/brokers/{broker:path}",
    response_model=BrokerHistoryResponse,
    responses={404: {"model": ApiErrorResponse}},
)
async def instrument_radar_broker(
    code: str,
    broker: str,
    market: Market = Query(..., min_length=1, max_length=16),
    window: Window = Query("90"),
):
    """單券商歷程（延遲載入，展開券商列才請求）：全歷程快照 + 相鄰差異。"""
    code, broker = code.strip(), broker.strip()
    if not code or len(code) > 16 or not broker or len(broker) > 64:
        raise HTTPException(status_code=422, detail="參數非法")
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        coverage = await deps.fetch_broker_coverage_counts(session, market, code, broker)
        if coverage.instrument_reports_available == 0:
            raise HTTPException(status_code=404, detail="instrument not found")
        if coverage.broker_reports_available == 0:
            raise HTTPException(status_code=404, detail="broker not found")
        signals = await deps.fetch_broker_signals(session, market, code, broker)
    resp = build_broker_history(
        signals, market=market, code=code, broker=broker, window=window
    )
    logger.info(
        "radar broker code=%s market=%s broker=%s window=%s snapshots=%d elapsed_ms=%.1f",
        code, market, broker, window, resp.report_count, (time.monotonic() - t0) * 1000,
    )
    return resp


# ───── 研報閱讀頁（/api/reading/*；讀取零 LLM）─────
#
# 契約見 app/services/reading/schemas.py（已凍結，前端 zod 逐字鏡像）。
# 既有的 /api/report/{report_id}/full 與 /file 是舊 modal 的資料源，與此處無關、不動。

_FILE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# /text 單次回傳上限。超過即截斷（truncated=True），但 text_sha256/text_chars 一律
# 是「完整正典文字」的值——見 _canonical_text 與 reading_text 的說明。
READING_TEXT_MAX_CHARS = 400_000


def _validate_file_hash(file_hash: str) -> None:
    """file_hash 是網址鍵，格式不符直接 422（不進 DB 查詢）。"""
    if not _FILE_HASH_RE.match(file_hash):
        raise HTTPException(status_code=422, detail="file_hash 非法")


def _canonical_text(full_text: str | None) -> tuple[str, str | None]:
    """回傳（正典文字, 其 sha256）。full_text 為 NULL/空 → ("", None)。

    **正典文字＝clean_extracted(full_text)，不是 full_text**：DB 存的是未清理的原始
    抽取文字（保留 PDF 抽字的 CJK 間空白與破碎換行），而 report_takeaway 的
    quote_start/quote_end 全部錨定於清理後的字串。詳見
    app/services/reading/anchor.py 模組 docstring 的「事實一」。

    **與 scripts/extract_takeaways.py 綁死**：那支批次以同樣的
    `sha256(clean_extracted(full_text))` 算出並寫入 report_takeaway.text_sha256，
    本函式算出的值要拿去和它比對驗章。兩邊任一側改了清理或編碼方式而另一側沒跟上，
    驗章會全篇失敗、跳轉靜默失效（不會拋錯）。要改就兩邊一起改。

    無全文不是錯誤：該篇只是沒有可讀文字（只能看 PDF），呼叫端據此回
    text_state="missing"。
    """
    canonical = clean_extracted(full_text) if full_text else ""
    if not canonical:
        return "", None
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _visible_chars(canonical: str) -> int:
    """/text 實際回給讀者的字元數（＝截斷後的長度）。

    截斷規則只有這一個定義處：骨架端點與 /text 都由此推導，兩邊才不會對「這條摘錄
    跳不跳得到」給出不同答案。
    """
    return min(len(canonical), READING_TEXT_MAX_CHARS)


def _reading_takeaways(rows, text_sha256: str | None, visible_chars: int) -> list[Takeaway]:
    """DB 摘錄列 → 契約 Takeaway，並在此驗章、套截斷。

    降級為不可跳（quote_start/quote_end/anchor_method 全 None，條目與引文照常顯示）
    有兩個獨立原因：

    1. **驗章不過**：每列的 text_sha256 是「擷取當時的正典文字」的 sha。與當前正典
       文字不符即代表全文已被重新 ingest 換過、offset 已漂移。
    2. **落在截斷範圍之外**：offset 對「完整正典文字」計算，但 /text 只回前
       READING_TEXT_MAX_CHARS 字。錨點超出這個範圍＝指向讀者手上根本沒有的文字。

    兩者都是「寧可不能跳，也不要跳到錯的地方」。第 2 點與 _chunk_anchor 的
    `anchor.end > visible_chars` 是同一條規則 —— 刻意在後端統一收回，而不是多發一個
    text_visible_chars 欄位讓前端各自判斷：可跳與否只該有一個真相來源，否則前端
    buildTextSegments（依 text.length 丟棄）與 isJumpable（只看 quote_start）會再次
    分岔，摘錄顯示為可點、點下去卻找不到錨點而靜默無事。
    """
    out: list[Takeaway] = []
    for r in rows:
        stale = text_sha256 is None or r.text_sha256 != text_sha256
        clipped = r.quote_end is None or r.quote_end > visible_chars
        drop = stale or clipped
        out.append(
            Takeaway(
                ordinal=r.ordinal,
                claim=r.claim,
                quote=r.quote,
                quote_start=None if drop else r.quote_start,
                quote_end=None if drop else r.quote_end,
                anchor_method=None if drop else r.anchor_method,
            )
        )
    return out


def _chunk_anchor(
    canonical: str, chunk_content: str | None, visible_chars: int
) -> tuple[int | None, int | None]:
    """把檢索命中的 chunk 錨回正典文字 → (start, end)；錨不到一律 (None, None)。

    **錨不到不是錯誤**：前端據此不高亮，頁面照常（也因此此處不拋 4xx）。錨定邏輯全在
    app/services/reading/anchor.py（實測 400/400 命中），前端不重造比對。

    **offset 一律對「完整正典文字」計算**（locate_chunk 的契約），但回傳給讀者的 text
    可能被截斷（見 reading_text 的截斷語意）。錨點落在截斷範圍之外＝指向讀者手上根本
    沒有的文字 → 收回為 None。寧可不高亮，也不要指到不存在的位置。
    """
    if not chunk_content:
        return None, None
    anchor = locate_chunk(canonical, chunk_content)
    if anchor is None or anchor.end > visible_chars:
        return None, None
    return anchor.start, anchor.end


def _reading_signals(rows) -> list[Signal]:
    """radar 的 Signal dataclass → 閱讀頁契約 Signal（欄位形狀刻意不同）。

    注意 fiscal_year：radar 存 int、契約要 str，此處轉型（契約已凍結，不改欄位型別）。
    """
    return [
        Signal(
            instrument_code=s.instrument_code,
            market=s.market,
            broker=s.broker,
            broker_display=source_display(s.broker),
            rating_raw=s.rating_raw,
            rating_normalized=s.rating_normalized,
            target_price=s.target_price,
            target_currency=s.target_currency,
            target_horizon=s.target_horizon,
            eps_estimates=[
                EpsEstimate(
                    fiscal_year=str(e.fiscal_year) if e.fiscal_year is not None else None,
                    period=e.period,
                    currency=e.currency,
                    unit=e.unit,
                    value=e.value,
                )
                for e in s.eps
            ],
            # radar 的 _parse_thesis 依 THESIS_DIMENSIONS 順序建 dict，故此處順序穩定
            thesis=[
                ThesisDim(
                    key=key, stance=dim.stance, summary=dim.summary, evidence=dim.evidence
                )
                for key, dim in s.thesis.items()
            ],
        )
        for s in rows
    ]


@app.get("/api/reading/{file_hash}", response_model=ReadingDoc)
async def reading_doc(file_hash: str):
    """閱讀頁骨架：metadata + 重點摘錄 + 訊號。**不含全文**（PDF 是預設檢視）。

    全文另走 /api/reading/{file_hash}/text，前端只在需要文字檢視時才取。
    """
    _validate_file_hash(file_hash)
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        if doc is None:
            raise HTTPException(status_code=404, detail="report not found")
        takeaway_rows = await deps.fetch_takeaways(session, doc.report_id)
        signal_rows = await deps.fetch_signals(session, doc.report_id)
    canonical, text_sha256 = _canonical_text(doc.full_text)
    signals = _reading_signals(signal_rows)
    return ReadingDoc(
        report_id=doc.report_id,
        file_hash=doc.file_hash,
        file_name=doc.file_name,
        market=doc.market,
        market_display=MARKET_DISPLAY.get(doc.market) if doc.market else None,
        source=doc.source,
        source_display=source_display(doc.source),
        report_date=doc.report_date.isoformat() if doc.report_date else None,
        report_type=doc.report_type,
        summary=doc.summary,
        instrument_types=doc.instrument_types,
        stock_targets=doc.stock_targets,
        futures_targets=doc.futures_targets,
        has_file=bool(doc.file_path) and os.path.isfile(doc.file_path),
        is_pdf=bool(doc.file_path) and doc.file_path.lower().endswith(".pdf"),
        text_state="ok" if canonical else "missing",
        text_chars=len(canonical),
        text_sha256=text_sha256,
        # visible_chars 與 /text 同源：落在截斷範圍外的錨點在此就收回，
        # 讀者不會看到一條「可點但點不到」的摘錄。
        takeaways=_reading_takeaways(takeaway_rows, text_sha256, _visible_chars(canonical)),
        # 全語料僅 0.68% 有訊號：空是常態不是錯誤，前端據此整區不進 DOM
        signals_state="available" if signals else "none",
        signals=signals,
    )


@app.get("/api/reading/{file_hash}/text", response_model=ReadingText)
async def reading_text(file_hash: str, chunk: int | None = Query(None, ge=0)):
    """正典文字（＝clean_extracted(full_text)）。所有 offset 都以此字串為準。

    **截斷語意**：text 超過 READING_TEXT_MAX_CHARS 時只回前綴並標 truncated=True，
    但 text_sha256 與 text_chars 仍是「完整正典文字」的值 —— takeaway 的錨點是對完整
    文字算出來的，回截斷版的 sha 會讓前端的驗章一律失敗、跳轉整個失效。
    超出截斷範圍的錨點一律由後端收回為 None（此處的 chunk_start/chunk_end 走
    _chunk_anchor，骨架端點的 takeaway offset 走 _reading_takeaways），前端不需要、
    也不應該自行判斷截斷。

    **?chunk=N**：檢索命中的 chunk_index。帶了就一併回該段在正典文字上的字元區間
    （chunk_start/chunk_end），供前端標出「你從檢索點進來的那一段」。chunk 不存在或
    錨不到 → 兩者為 None，回應仍是 200：**沒有命中位置不是錯誤**，頁面照常。
    """
    _validate_file_hash(file_hash)
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        # 同一個 session 內取完：出了 with 區塊 session 已關閉
        chunk_content = (
            await deps.fetch_chunk_content(session, doc.report_id, chunk)
            if doc is not None and chunk is not None
            else None
        )
    if doc is None:
        raise HTTPException(status_code=404, detail="report not found")
    canonical, text_sha256 = _canonical_text(doc.full_text)
    if not canonical or text_sha256 is None:
        # text_state="missing" 的那一篇：骨架回 200，這裡沒有文字可給
        raise HTTPException(status_code=404, detail="report text not available")
    visible = _visible_chars(canonical)
    truncated = len(canonical) > visible
    body = canonical[:visible]
    chunk_start, chunk_end = _chunk_anchor(canonical, chunk_content, visible)
    return ReadingText(
        file_hash=doc.file_hash,
        text=body,
        text_sha256=text_sha256,  # 完整正典文字的 sha，截斷後也不重算
        text_chars=len(canonical),  # 完整長度，非回傳字串長度
        truncated=truncated,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
    )


@app.get("/api/reading/{file_hash}/similar", response_model=SimilarResponse)
async def reading_similar(file_hash: str, limit: int = Query(6, ge=1, le=20)):
    """相似研報（全篇均勻取樣 probe + 廣度加權；理由見 reading/queries.py）。"""
    _validate_file_hash(file_hash)
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        if doc is None:
            raise HTTPException(status_code=404, detail="report not found")
        rows = await deps.fetch_similar(session, doc.report_id, limit=limit)
    logger.info(
        "reading similar file_hash=%s items=%d elapsed_ms=%.1f",
        file_hash, len(rows), (time.monotonic() - t0) * 1000,
    )
    return SimilarResponse(
        file_hash=file_hash,
        items=[
            SimilarReport(
                file_hash=r.file_hash,
                file_name=r.file_name,
                market=r.market,
                source=r.source,
                source_display=source_display(r.source),
                report_date=r.report_date.isoformat() if r.report_date else None,
                summary=r.summary,
                matched_probes=r.matched_probes,
                total_probes=r.total_probes,
            )
            for r in rows
        ],
    )


@app.get("/api/reports", response_model=ReportListResponse)
async def reports(
    market: str | None = Query(None),
    instrument_type: str | None = Query(None),
    relates_stock: bool | None = Query(None),
    relates_futures: bool | None = Query(None),
    report_type: str | None = Query(None),
    sort: str = Query("date_desc"),  # date_desc | date_asc
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """無關鍵字的瀏覽模式：依 sort 列出已導入報告。"""
    mkt = market if market and market != "全部" else None
    instr = instrument_type if instrument_type and instrument_type != "全部" else None
    rtype = report_type if report_type and report_type != "全部" else None
    async with deps.SessionFactory() as session:
        total, rows = await list_reports(
            session,
            market=mkt,
            instrument_type=instr,
            relates_stock=relates_stock or None,
            relates_futures=relates_futures or None,
            report_type=rtype,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    items = [
        ReportListItem(
            report_id=rid,
            file_hash=fhash,
            file_name=fn,
            market=m,
            source=source_display(src),
            summary=summary,
            report_date=rdate.isoformat() if rdate else None,
            report_type=rtype,
            instrument_types=list(itypes) if itypes else None,
            relates_stock=rstock,
            relates_futures=rfut,
            stock_targets=list(stargets) if stargets else None,
            futures_targets=list(ftargets) if ftargets else None,
        )
        for (
            rid, fhash, fn, m, src, rdate, rtype, itypes, rstock, rfut,
            stargets, ftargets, summary,
        ) in rows
    ]
    return ReportListResponse(total=total, offset=offset, items=items)


@app.get("/api/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=SEARCH_QUERY_MAX_CHARS),
    market: str | None = Query(None),
    instrument_type: str | None = Query(None),
    relates_stock: bool | None = Query(None),
    relates_futures: bool | None = Query(None),
    report_type: str | None = Query(None),
    sort: str = Query("relevance"),  # relevance | date_desc | date_asc
    limit: int = Query(50, ge=1, le=100),  # 回傳的「報告」頁大小
    offset: int = Query(0, ge=0),
    passages: int = Query(3, ge=1, le=6),  # 每篇保留的命中片段數
):
    mkt = market if market and market != "全部" else None
    instr = instrument_type if instrument_type and instrument_type != "全部" else None
    rtype = report_type if report_type and report_type != "全部" else None
    qvec = await asyncio.to_thread(deps.embed_query_cached, q)
    async with deps.SessionFactory() as session:
        scored = await deps.hybrid_search(
            session,
            q,
            qvec,
            market=mkt,
            instrument_type=instr,
            relates_stock=relates_stock or None,
            relates_futures=relates_futures or None,
            report_type=rtype,
            # 不傳 k：dense_scan 已覆蓋掃描深度；lexical 改由 cap 控候選，不再截斷最終報告數
            dense_scan=DENSE_SCAN_SEARCH,
            lex_cap=LEX_CAP_SEARCH,
            lex_per_report=True,
            lex_unlimited=True,
        )

    # 分組成「全部」召回報告 → 依 sort 排序 → 取 total → 切當頁
    ranked = deps.rank_reports(scored, sort=sort)
    total = len(ranked)
    # 色譜讀數：命中集合的市場組成。ranked 已全量在記憶體，額外成本僅一次計數。
    facet_counts = Counter(g.meta_row.market for g in ranked if g.meta_row.market)
    page = ranked[offset : offset + limit]

    results: list[ReportResult] = []
    for i, g in enumerate(page, start=offset + 1):  # 全域 rank，跨頁不重號
        mr = g.meta_row
        rid, fn, m, src, summary, rdate = (
            mr.report_id, mr.file_name, mr.market, mr.source, mr.summary, mr.report_date
        )
        rtype_ = mr.report_type
        itypes = mr.instrument_types
        rstock = mr.relates_stock
        rfut = mr.relates_futures
        stargets = mr.stock_targets
        ftargets = mr.futures_targets
        ps: list[Passage] = []
        for sc, prow in g.passages[:passages]:
            cleaned = clean_text(prow.content)
            if cleaned:
                ps.append(
                    Passage(score=sc, chunk_index=int(prow.chunk_index), content=cleaned)
                )
        results.append(
            ReportResult(
                rank=i,
                report_id=rid,
                file_hash=mr.file_hash,
                file_name=fn,
                market=m,
                source=source_display(src),
                summary=summary,
                report_date=rdate.isoformat() if rdate else None,
                report_type=rtype_,
                instrument_types=list(itypes) if itypes else None,
                relates_stock=rstock,
                relates_futures=rfut,
                stock_targets=list(stargets) if stargets else None,
                futures_targets=list(ftargets) if ftargets else None,
                best_score=g.best_score,
                match_count=g.match_count,
                passages=ps,
            )
        )
    return SearchResponse(
        query=q,
        market=mkt,
        total=total,
        market_facets=[
            MarketFacet(market=m, count=c) for m, c in facet_counts.most_common()
        ],
        results=results,
    )


# ───── RAG 問答（Phase 1）：SSE 串流 ─────
# 每次提問會 spawn 一個 claude CLI 子程序（CPU-bound 機器），限制同時數避免區網多人同問雪崩。
_ASK_SEMAPHORE = asyncio.Semaphore(3)

# 研報生成比問答重很多（長輸出 + PDF 排版），預設序列化避免區網多人同時生成拖垮機器。
_REPORT_SEMAPHORE = asyncio.Semaphore(int(os.getenv("REPORT_SEMAPHORE", "1")))


class ReportRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    qa_id: str | None = None


_sse = deps._sse
SSE_HEARTBEAT_INTERVAL = deps.SSE_HEARTBEAT_INTERVAL
_with_heartbeat = deps._with_heartbeat


@app.post("/api/ask")
async def ask(req: AskRequest):
    """RAG 問答：檢索 → 串流回答（帶 [n] 行內引用）。回 text/event-stream。

    事件序：sources（引用清單）→ 多筆 token（文字片段）→ done（實際引用的報告 id）。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    mkt = req.market if req.market and req.market != "全部" else None
    instr = (
        req.instrument_type
        if req.instrument_type and req.instrument_type != "全部"
        else None
    )
    rtype = req.report_type if req.report_type and req.report_type != "全部" else None
    filters = dict(
        market=mkt,
        instrument_type=instr,
        relates_stock=req.relates_stock or None,
        relates_futures=req.relates_futures or None,
        report_type=rtype,
    )
    k = max(1, min(req.k, 20))
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.edit_of is not None and not deps._valid_uuid(req.edit_of):
        raise HTTPException(status_code=400, detail="edit_of 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")

    async def gen():
        async with _ASK_SEMAPHORE:
            try:
                async for event, payload in deps.answer_question(
                    question,
                    k=k,
                    filters=filters,
                    conversation_id=req.conversation_id,
                    regenerate_of=req.regenerate_of,
                    edit_of=req.edit_of,
                    request_id=req.request_id,
                ):
                    yield deps._sse(event, payload)
            except Exception:
                logger.exception("ask failed")
                yield deps._sse("error", {"detail": "問答服務發生錯誤"})

    return StreamingResponse(
        deps._with_heartbeat(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_valid_uuid = deps._valid_uuid


class StopRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    partial_answer: str = Field(default="", max_length=20_000)
    sources: list[dict] | None = Field(default=None, max_length=100)
    ext_sources: list[dict] | None = Field(default=None, max_length=50)
    stages: list[str] | None = Field(default=None, max_length=10)
    regenerate_of: str | None = None
    request_id: str | None = None


@app.post("/api/ask/stop")
async def ask_stop(req: StopRequest):
    """使用者中斷串流時保存部分答案（stopped=true）。回 {qa_id}。"""
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")
    qa_id = await deps.log_stopped_qa(
        (req.question or "").strip(),
        req.partial_answer or "",
        conversation_id=req.conversation_id,
        sources=req.sources,
        ext_sources=req.ext_sources,
        stages=req.stages,
        regenerate_of=req.regenerate_of,
        request_id=req.request_id,
    )
    if qa_id is None:
        raise HTTPException(status_code=503, detail="停止的回答暫時無法保存")
    return {"qa_id": qa_id}


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


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    """記錄使用者對某次回答的讚/倒讚（qa_id 來自 /api/ask 的 done 事件）。"""
    if req.value not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="value 必須是 like 或 dislike")
    ok = await record_feedback(req.qa_id, req.value)
    return {"ok": ok}


@app.get("/api/history")
async def history(limit: int = Query(50, ge=1, le=200)):
    """最近的問答歷史（排除離題拒答）；唯讀，供前端「歷史」抽層。"""
    async with deps.SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE COALESCE(answer NOT IN :offtopics, TRUE) "
                    "AND active AND stopped IS NOT TRUE "
                    "ORDER BY created_at DESC LIMIT :limit"
                ).bindparams(bindparam("offtopics", expanding=True)),
                {"offtopics": list(OFF_TOPIC_MESSAGES), "limit": limit},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]


@app.delete("/api/history/{qa_id}")
async def delete_history(qa_id: str):
    """刪除單筆問答歷史（使用者清除側欄某一列）。回 {"ok": bool}。"""
    ok = await deps.delete_qa(qa_id)
    return {"ok": ok}


@app.post("/api/history/{qa_id}/delete")
async def delete_history_post(qa_id: str):
    """相容性刪除路由。

    某些外部代理/邊緣環境對 DELETE 支援不穩時，前端可回退到 POST alias。
    """
    ok = await deps.delete_qa(qa_id)
    return {"ok": ok}


@app.get("/api/qa/{root_qa_id}/versions")
async def qa_versions(root_qa_id: str):
    """某問題群組全部版本（供歷史 pager 回看）。"""
    if not deps._valid_uuid(root_qa_id):
        raise HTTPException(status_code=404, detail="not found")
    return await deps.list_qa_versions(root_qa_id)


@app.get("/api/conversations")
async def conversations(limit: int = Query(50, ge=1, le=200)):
    """對話串清單（首題非離題者）；唯讀，供側欄。"""
    return await list_conversations(limit)


@app.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    """單一對話全部輪次（由舊到新），供重開重現與續問。"""
    return await get_conversation(conversation_id)


@app.delete("/api/conversations/{conversation_id}")
async def conversation_delete(conversation_id: str):
    """刪整個對話串。回 {"ok": bool}。"""
    ok = await delete_conversation(conversation_id)
    return {"ok": ok}


@app.post("/api/conversations/{conversation_id}/delete")
async def conversation_delete_post(conversation_id: str):
    """相容性刪除路由（某些代理/邊緣對 DELETE 不穩時前端回退）。"""
    ok = await delete_conversation(conversation_id)
    return {"ok": ok}


async def _fetch_report(session, report_id: str):
    row = (
        await session.execute(
            text(
                "SELECT file_name, market, source, report_date, report_type, "
                "file_path, full_text, summary FROM research.research_report WHERE id = :id"
            ),
            {"id": report_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="report not found")
    return row


@app.get("/api/report/{report_id}/full")
async def report_full(report_id: str):
    """回傳單篇報告的 metadata 與原始檔狀態（供前端 modal 內嵌 PDF）。"""
    async with deps.SessionFactory() as session:
        fn, m, src, rdate, rtype, fpath, _, summary = await _fetch_report(
            session, report_id
        )
    return {
        "report_id": report_id,
        "file_name": fn,
        "market": m,
        "source": source_display(src),
        "summary": summary,
        "report_date": rdate.isoformat() if rdate else None,
        "report_type": rtype,
        "has_file": bool(fpath) and os.path.isfile(fpath),
    }


@app.get("/api/report/{report_id}/file")
async def report_file(report_id: str):
    """提供原始檔（PDF 內嵌、其他下載）。路徑由 DB 依 id 取得，無路徑注入。"""
    async with deps.SessionFactory() as session:
        row = await _fetch_report(session, report_id)
    fpath = row[5]
    if not fpath or not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="original file not found")
    name = os.path.basename(fpath)
    is_pdf = name.lower().endswith(".pdf")
    # PDF 用 inline 才能在 modal 的 iframe 內嵌渲染；其他（.docx）維持下載
    return FileResponse(
        fpath,
        media_type="application/pdf" if is_pdf else None,
        filename=name,
        content_disposition_type="inline" if is_pdf else "attachment",
    )


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
