# web/routers/radar.py
"""觀點雷達 API：選標的目錄、個股總覽、事件時間軸、單券商歷程。

從 web/server.py 拆出（第三步）。4 條路由都是純讀取、零 LLM，依賴的服務函式
（SessionFactory、fetch_*、list_radar_instruments）一律走 web.deps，故測試 patch
web.deps.X 即可涵蓋本模組——不需改測試。純 builder（build_*）與 schema 由來源
模組直接匯入（不經 mock）。
"""
import logging
import time

from fastapi import APIRouter, HTTPException, Query

from app.services.radar import (
    build_broker_history,
    build_events_page,
    build_instrument_slim,
    build_overview,
)
from app.services.radar.schemas import (
    ApiErrorResponse,
    BrokerHistoryResponse,
    Market,
    RadarEventsResponse,
    RadarInstrumentItem,
    RadarInstrumentsResponse,
    RadarOverviewResponse,
    Window,
)
from app.services.tagging import MARKET_DISPLAY
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/radar/instruments", response_model=RadarInstrumentsResponse)
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


@router.get(
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


@router.get("/api/instrument/{code:path}/radar", response_model=RadarOverviewResponse)
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


@router.get(
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
