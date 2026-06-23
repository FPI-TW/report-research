"""向量入庫：依 file_hash 去重 upsert（先刪後插），寫入報告 + 分塊向量。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ReportRow:
    file_hash: str
    file_name: str
    file_path: str
    market: Optional[str]
    is_research: Optional[bool]
    confidence: Optional[float]
    stock_code: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    report_date: Optional[date] = None
    report_type: Optional[str] = None
    language: Optional[str] = None
    instrument_types: Optional[list[str]] = None
    relates_stock: Optional[bool] = None
    relates_futures: Optional[bool] = None
    stock_targets: Optional[list[str]] = None
    futures_targets: Optional[list[str]] = None
    full_text: Optional[str] = None


def _vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def upsert_report(
    session: AsyncSession,
    report: ReportRow,
    chunks: list[str],
    embeddings: list[list[float]],
) -> str:
    """寫入單一報告與其分塊；同 file_hash 既有資料先刪除。回傳 report id。"""
    # 去重：刪掉舊報告（chunk 由 FK ON DELETE CASCADE 連帶刪）
    await session.execute(
        text("DELETE FROM research.research_report WHERE file_hash = :h"),
        {"h": report.file_hash},
    )

    report_id = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO research.research_report
                (id, file_hash, file_name, file_path, market, is_research,
                 confidence, stock_code, company_name, source, report_date,
                 report_type, language, instrument_types, relates_stock,
                 relates_futures, stock_targets, futures_targets, full_text)
            VALUES
                (:id, :file_hash, :file_name, :file_path, :market, :is_research,
                 :confidence, :stock_code, :company_name, :source, :report_date,
                 :report_type, :language, CAST(:instrument_types AS text[]),
                 :relates_stock, :relates_futures,
                 CAST(:stock_targets AS text[]), CAST(:futures_targets AS text[]),
                 :full_text)
            """
        ),
        {"id": report_id, **report.__dict__},
    )

    chunk_rows = []
    for idx, (content, emb) in enumerate(zip(chunks, embeddings)):
        chunk_rows.append(
            {
                "id": str(uuid.uuid4()),
                "report_id": report_id,
                "chunk_index": idx,
                "content": content,
                "embedding": _vec_literal(emb),
            }
        )
    if chunk_rows:
        await session.execute(
            text(
                """
                INSERT INTO research.report_chunk
                    (id, report_id, chunk_index, content, embedding)
                VALUES
                    (:id, :report_id, :chunk_index, :content,
                     CAST(:embedding AS vector))
                """
            ),
            chunk_rows,
        )

    await session.commit()
    return report_id


async def report_exists(session: AsyncSession, file_hash: str) -> bool:
    row = await session.execute(
        text("SELECT 1 FROM research.research_report WHERE file_hash = :h"),
        {"h": file_hash},
    )
    return row.first() is not None


async def search_chunks(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int = 10,
    market: Optional[str] = None,
):
    """cosine 最近鄰，回傳 (file_name, market, content, distance)。"""
    sql = """
        SELECT r.file_name, r.market, c.content,
               c.embedding <=> CAST(:q AS vector) AS distance
        FROM research.report_chunk c
        JOIN research.research_report r ON r.id = c.report_id
        {where}
        ORDER BY c.embedding <=> CAST(:q AS vector)
        LIMIT :k
    """
    params: dict = {"q": _vec_literal(query_embedding), "k": top_k}
    where = ""
    if market:
        where = "WHERE r.market = :market"
        params["market"] = market
    rows = await session.execute(text(sql.format(where=where)), params)
    return rows.all()


# 瀏覽模式排序白名單：key 由前端傳入，value 直接拼進 ORDER BY，
# 故僅能取自此 dict（未知值回退預設），嚴禁把原始字串串進 SQL。
# 保留 file_name 作 tiebreaker，offset 分頁順序才穩定、不跨頁重複。
_BROWSE_SORT: dict[str, str] = {
    "date_desc": "report_date DESC NULLS LAST, file_name",
    "date_asc": "report_date ASC NULLS LAST, file_name",
}


async def list_reports(
    session: AsyncSession,
    *,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
    sort: str = "date_desc",
    limit: int = 50,
    offset: int = 0,
):
    """瀏覽模式：依 sort（日期新→舊／舊→新）列出 metadata（無向量檢索）。

    回傳 (total, rows)；rows 欄位：(report_id, file_name, market, source,
    report_date, report_type, instrument_types, relates_stock, relates_futures,
    stock_targets, futures_targets, summary)。
    """
    conds: list[str] = []
    params: dict = {}
    if market:
        conds.append("market = :market")
        params["market"] = market
    if instrument_type:
        conds.append("instrument_types @> ARRAY[:it]::text[]")
        params["it"] = instrument_type
    if relates_stock:
        conds.append("relates_stock = true")
    if relates_futures:
        conds.append("relates_futures = true")
    if report_type:
        conds.append("report_type = :report_type")
        params["report_type"] = report_type
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    order = _BROWSE_SORT.get(sort, _BROWSE_SORT["date_desc"])
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM research.research_report {where}"), params
        )
    ).scalar_one()
    rows = await session.execute(
        text(
            f"""
            SELECT id::text, file_name, market, source, report_date, report_type,
                   instrument_types, relates_stock, relates_futures,
                   stock_targets, futures_targets, summary
            FROM research.research_report
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return total, rows.all()


def _meta_filters(
    params: dict,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
) -> list[str]:
    """組裝報告 metadata 過濾條件（dense / 字面雙路共用，確保過濾一致）。"""
    conds: list[str] = []
    if market:
        conds.append("r.market = :market")
        params["market"] = market
    if instrument_type:
        conds.append("r.instrument_types @> ARRAY[:it]::text[]")
        params["it"] = instrument_type
    if relates_stock:
        conds.append("r.relates_stock = true")
    if relates_futures:
        conds.append("r.relates_futures = true")
    if report_type:
        conds.append("r.report_type = :report_type")
        params["report_type"] = report_type
    return conds


def _meta_columns(chunk_alias: str) -> str:
    """dense / 字面雙路共用的 SELECT 欄位列表；首欄 chunk_id 供跨路去重。

    summary 必須插在中段（source 後），不可放 content 之後——retrieval.py 以位置
    存取 row[-2]=content、row[-1]=distance，append 到尾端會擠走 content。
    """
    a = chunk_alias
    return f"""{a}.id::text, r.id::text, r.file_name, r.market, r.source, r.summary,
               r.report_date, r.report_type, r.instrument_types, r.relates_stock,
               r.relates_futures, r.stock_targets, r.futures_targets,
               {a}.chunk_index, {a}.content"""


async def search_chunks_meta(
    session: AsyncSession,
    query_embedding: list[float],
    scan: int = 60,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
):
    """掃描前 scan 個最近鄰片段，連同報告 metadata 回傳（供伺服器分組）。

    回傳列：(chunk_id, report_id, file_name, market, source, summary, report_date,
             report_type, instrument_types, relates_stock, relates_futures,
             stock_targets, futures_targets, chunk_index, content, distance)，
    已依距離由近到遠排序（iterative scan 下為近似排序，呼叫端會重排）。
    """
    sql = """
        SELECT {cols},
               c.embedding <=> CAST(:q AS vector) AS distance
        FROM research.report_chunk c
        JOIN research.research_report r ON r.id = c.report_id
        {where}
        ORDER BY c.embedding <=> CAST(:q AS vector)
        LIMIT :scan
    """
    params: dict = {"q": _vec_literal(query_embedding), "scan": scan}
    conds = _meta_filters(
        params, market, instrument_type, relates_stock, relates_futures, report_type
    )
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    # ef_search 須 ≥ scan，否則 HNSW 最多只回 ef_search 列（預設 40 會默默截斷）；
    # iterative_scan 讓帶過濾的查詢持續掃到滿足 LIMIT 為止（pgvector 0.8+）
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {max(120, scan)}"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    rows = await session.execute(
        text(sql.format(cols=_meta_columns("c"), where=where)), params
    )
    return rows.all()


async def search_chunks_lexical(
    session: AsyncSession,
    query_embedding: list[float],
    term_patterns: list[str],
    limit: int = 200,
    cap: int = 2000,
    *,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
):
    """字面比對路：content_norm 同時含全部 LIKE pattern 的片段，依向量距離排序。

    回傳列結構與 search_chunks_meta 相同。MATERIALIZED CTE 先過濾（走 trgm GIN
    索引），再對最多 cap 列算精確距離，避免 planner 因 ORDER BY 走 HNSW。
    """
    if not term_patterns:
        return []
    params: dict = {"q": _vec_literal(query_embedding), "limit": limit, "cap": cap}
    conds = [f"c.content_norm LIKE :t{i}" for i in range(len(term_patterns))]
    for i, pat in enumerate(term_patterns):
        params[f"t{i}"] = pat
    conds += _meta_filters(
        params, market, instrument_type, relates_stock, relates_futures, report_type
    )
    sql = f"""
        WITH lex AS MATERIALIZED (
            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding
            FROM research.report_chunk c
            JOIN research.research_report r ON r.id = c.report_id
            WHERE {" AND ".join(conds)}
            LIMIT :cap
        )
        SELECT {_meta_columns("l")},
               l.embedding <=> CAST(:q AS vector) AS distance
        FROM lex l
        JOIN research.research_report r ON r.id = l.report_id
        ORDER BY distance
        LIMIT :limit
    """
    rows = await session.execute(text(sql), params)
    return rows.all()
