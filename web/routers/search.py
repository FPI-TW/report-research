# web/routers/search.py
"""檢索 / 瀏覽 API：/api/search（混合檢索）、/api/reports（無關鍵字瀏覽）、
/api/markets（市場清單，前端篩選器資料源）。

從 web/server.py 拆出（第三步）。回應模型（Passage/ReportResult/MarketFacet/
SearchResponse/ReportListItem/ReportListResponse）只有這組用，故一併移入。

檢索/嵌入/排序（hybrid_search、embed_query_cached、rank_reports、SessionFactory）
走 web.deps，測試 patch web.deps.X 即涵蓋。DENSE_SCAN_SEARCH/LEX_CAP_SEARCH 與
SEARCH_QUERY_MAX_CHARS 是本組設定，定義/匯入於本模組。
"""
import asyncio
from collections import Counter

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.filename import source_display
from app.services.retrieval import DENSE_SCAN_SEARCH, LEX_CAP_SEARCH
from app.services.store import list_reports
from app.services.tagging import MARKETS
from app.services.textnorm import clean_text
from web import deps

router = APIRouter()

SEARCH_QUERY_MAX_CHARS = 500


class Passage(BaseModel):
    score: float  # 1 - cosine distance，越高越相關
    chunk_index: int
    content: str


class ReportResult(BaseModel):
    rank: int
    report_id: str
    file_hash: str  # 閱讀頁連結鍵（/app/report/:hash）
    file_name: str
    # 報告內部標題（顯示用）。None＝尚未產生，前端回退 file_name——批次是漸進補的，
    # 任何時點都會有一部分報告沒有標題，這是常態不是錯誤。
    title: str | None = None
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
    # 字面路候選是否已被 LEX_CAP_SEARCH 截斷。截斷時「取到哪 cap 列」由 heap 物理順序
    # 決定（`LIMIT :cap` 沒有 ORDER BY，而 synchronize_seqscans 預設 on），也就是同一
    # 個查詢在不同時刻可能回不同結果。旗標存在的目的是**先量出發生率**——加排序鍵會
    # 逼掃完全部命中列，是淨損失，見 store._lexical_sql 的說明。
    lexical_truncated: bool = False
    results: list[ReportResult]


class ReportListItem(BaseModel):
    report_id: str
    file_hash: str  # 閱讀頁連結鍵（/app/report/:hash）
    file_name: str
    title: str | None = None  # 同 ReportResult.title
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


@router.get("/api/markets")
async def markets():
    return {"markets": MARKETS}


@router.get("/api/reports", response_model=ReportListResponse)
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
            title=title,
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
            rid, fhash, fn, title, m, src, rdate, rtype, itypes, rstock, rfut,
            stargets, ftargets, summary,
        ) in rows
    ]
    return ReportListResponse(total=total, offset=offset, items=items)


@router.get("/api/search", response_model=SearchResponse)
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
    retrieval_stats: dict = {}
    async with deps.SessionFactory() as session:
        scored = await deps.hybrid_search(
            session,
            q,
            qvec,
            stats=retrieval_stats,
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
                title=mr.title,
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
        # 測試的 hybrid_search 替身不會填 stats，故一律 .get 帶預設（缺值＝未截斷）。
        lexical_truncated=bool(retrieval_stats.get("lex_truncated")),
        results=results,
    )