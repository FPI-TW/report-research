"""查詢測試網頁後端。

BGE-M3 模型在啟動時載入並常駐記憶體，查詢只需 embed + pgvector 檢索。
啟動：uv run uvicorn web.server:app --host 0.0.0.0 --port 8097
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

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

from app.services.answer import (  # noqa: E402
    OFF_TOPIC_MESSAGE,
    answer_question,
    delete_conversation,
    delete_qa,
    get_conversation,
    history_item,
    list_conversations,
    record_feedback,
)
from app.services.db import SessionFactory  # noqa: E402
from app.services.embed import embed_query_cached, embed_texts  # noqa: E402
from app.services.filename import source_display  # noqa: E402
from app.services.retrieval import (  # noqa: E402
    DENSE_SCAN_SEARCH,
    LEX_CAP_SEARCH,
    LEX_LIMIT_SEARCH,
    hybrid_search,
    rank_reports,
)
from app.services.store import list_reports  # noqa: E402
from app.services.tagging import MARKETS  # noqa: E402
from app.services.textnorm import clean_text  # noqa: E402
from web import auth  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


def _static_page(name: str) -> FileResponse:
    """回傳 HTML 殼，並標記 no-cache（瀏覽器每次重新向伺服器取用，改版後同事免強制重整即見新版）。

    註：route-level 的 FileResponse 不會對 If-None-Match/If-Modified-Since 做 304 短路，
    每次都回 200 全量 body（HTML 約數十 KB，區網成本可忽略）。/static 下的 css/js 由
    _NoCacheStatic（StaticFiles）服務，才有條件式 304 重新驗證。
    """
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


class _NoCacheStatic(StaticFiles):
    """/static 下的 css/js/圖片同樣以 no-cache 重新驗證，確保 tokens.css / utils.js 改版不卡舊版。"""

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


async def _warmup_embeddings() -> None:
    await asyncio.to_thread(embed_texts, ["warmup"])


def _log_warmup_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("embedding warmup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在背景暖機，避免啟動期間 socket 尚未 bind 導致外部完全無法連線。
    warmup_task = asyncio.create_task(_warmup_embeddings())
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


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in _AUTH_ALLOWLIST:
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


class SearchResponse(BaseModel):
    query: str
    market: str | None
    total: int
    results: list[ReportResult]


class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    relates_stock: bool | None = None
    relates_futures: bool | None = None
    report_type: str | None = None
    k: int = 8


class FeedbackRequest(BaseModel):
    qa_id: str
    value: str  # 'like' | 'dislike'


class ReportListItem(BaseModel):
    report_id: str
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


@app.get("/api/stats")
async def stats():
    async with SessionFactory() as session:
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
    return {
        "total_reports": total_reports,
        "total_chunks": total_chunks,
        "markets": [{"market": m, "count": c} for m, c in rows],
        "instrument_types": [{"type": t, "count": c} for t, c in instr_rows],
        "report_types": [{"type": t, "count": c} for t, c in type_rows],
        "username": auth.ACCESS_USERNAME,
    }


# ───── 進度監控（/monitor 儀表板資料來源）─────
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_TAG_RE = re.compile(r"(\d+)/(\d+)\s+ok\+skip=(\d+)\s+fail=(\d+)")
_ING_RE = re.compile(r"ingested=(\d+)\s+chunks=(\d+)\s+fail=(\d+)")


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
        "orchestrator": _orchestrator_last(),
    }


@app.get("/api/progress")
async def progress():
    async with SessionFactory() as session:
        reports = (
            await session.execute(text("SELECT count(*) FROM research.research_report"))
        ).scalar_one()
        chunks = (
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
        # 摘要進度直接由 DB 計數（精確、重啟後仍正確，免解析 log）；
        # total 須與 generate_summaries.py 的候選條件一致
        s_done, s_total = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE summary IS NOT NULL), count(*) "
                    "FROM research.research_report "
                    "WHERE full_text IS NOT NULL AND is_research IS NOT FALSE"
                )
            )
        ).first()
    runtime = await asyncio.to_thread(_gather_runtime)
    s_done, s_total = int(s_done), int(s_total)
    return {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "db": {
            "reports": reports,
            "chunks": chunks,
            "markets": [{"market": m, "count": c} for m, c in rows],
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
    return _static_page("monitor.html")


@app.get("/help")
async def help_page():
    return _static_page("help.html")


@app.get("/api/markets")
async def markets():
    return {"markets": MARKETS}


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
    async with SessionFactory() as session:
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
            rid, fn, m, src, rdate, rtype, itypes, rstock, rfut,
            stargets, ftargets, summary,
        ) in rows
    ]
    return ReportListResponse(total=total, offset=offset, items=items)


@app.get("/api/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
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
    qvec = await asyncio.to_thread(embed_query_cached, q)
    async with SessionFactory() as session:
        scored = await hybrid_search(
            session,
            q,
            qvec,
            market=mkt,
            instrument_type=instr,
            relates_stock=relates_stock or None,
            relates_futures=relates_futures or None,
            report_type=rtype,
            dense_scan=DENSE_SCAN_SEARCH,
            lex_limit=LEX_LIMIT_SEARCH,
            lex_cap=LEX_CAP_SEARCH,
            lex_per_report=True,
        )

    # 分組成「全部」召回報告 → 依 sort 排序 → 取 total → 切當頁
    ranked = rank_reports(scored, sort=sort)
    total = len(ranked)
    page = ranked[offset : offset + limit]

    results: list[ReportResult] = []
    for i, g in enumerate(page, start=offset + 1):  # 全域 rank，跨頁不重號
        (
            _chunk_id, rid, fn, m, src, summary, rdate, rtype_, itypes, rstock, rfut,
            stargets, ftargets, _cidx, _content, _dist,
        ) = g.meta_row
        ps: list[Passage] = []
        for sc, prow in g.passages[:passages]:
            cleaned = clean_text(prow[-2])  # content = row[-2]
            if cleaned:
                ps.append(
                    Passage(score=sc, chunk_index=int(prow[-3]), content=cleaned)
                )  # chunk_index = row[-3]
        results.append(
            ReportResult(
                rank=i,
                report_id=rid,
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
    return SearchResponse(query=q, market=mkt, total=total, results=results)


# ───── RAG 問答（Phase 1）：SSE 串流 ─────
# 每次提問會 spawn 一個 claude CLI 子程序（CPU-bound 機器），限制同時數避免區網多人同問雪崩。
_ASK_SEMAPHORE = asyncio.Semaphore(3)


def _sse(event: str, data: object) -> str:
    """組一個 SSE 事件框（event + json data）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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

    async def gen():
        async with _ASK_SEMAPHORE:
            try:
                async for event, payload in answer_question(
                    question, k=k, filters=filters, conversation_id=req.conversation_id
                ):
                    yield _sse(event, payload)
            except Exception:
                logger.exception("ask failed")
                yield _sse("error", {"detail": "問答服務發生錯誤"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE answer IS DISTINCT FROM :offtopic "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"offtopic": OFF_TOPIC_MESSAGE, "limit": limit},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]


@app.delete("/api/history/{qa_id}")
async def delete_history(qa_id: str):
    """刪除單筆問答歷史（使用者清除側欄某一列）。回 {"ok": bool}。"""
    ok = await delete_qa(qa_id)
    return {"ok": ok}


@app.post("/api/history/{qa_id}/delete")
async def delete_history_post(qa_id: str):
    """相容性刪除路由。

    某些外部代理/邊緣環境對 DELETE 支援不穩時，前端可回退到 POST alias。
    """
    ok = await delete_qa(qa_id)
    return {"ok": ok}


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
    async with SessionFactory() as session:
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
    async with SessionFactory() as session:
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
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), int(time.time())):
        return RedirectResponse("/", status_code=302)
    return _static_page("login.html")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    if not auth.login_allowed(request):
        return RedirectResponse("/login?error=insecure", status_code=303)
    now = int(time.time())
    ip = auth.client_ip(request)
    if auth.is_locked(ip, now):
        return RedirectResponse("/login?error=locked", status_code=303)
    if auth.check_credentials(username, password):
        auth.reset(ip)
        resp = RedirectResponse("/", status_code=303)
        auth.set_session_cookie(resp, now, secure=auth.request_is_secure(request))
        return resp
    auth.record_failure(ip, now)
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp


@app.get("/")
async def index():
    return _static_page("index.html")


app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")
