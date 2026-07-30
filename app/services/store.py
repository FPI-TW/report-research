"""向量入庫：依 file_hash 去重 upsert（先刪後插），寫入報告 + 分塊向量。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rows import ChunkRow


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

    回傳 (total, rows)；rows 欄位：(report_id, file_hash, file_name, title, market,
    source, report_date, report_type, instrument_types, relates_stock,
    relates_futures, stock_targets, futures_targets, summary)。
    呼叫端以位移解包，欄序即契約。
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
            SELECT id::text, file_hash, file_name, title, market, source, report_date,
                   report_type, instrument_types, relates_stock, relates_futures,
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

    新增欄位（summary、file_hash…）必須插在中段，不可放 content 之後：欄序須與
    `rows.ChunkRow` 逐欄對齊（`_make()` 純靠位置打包），且消費端可能寫死索引 ——
    見 `scripts/eval_retrieval.py` 曾寫死 `_RID, _CONTENT = 1, 14` 而被 file_hash
    插欄無聲指錯欄的實例。錯位不會拋錯，只會靜默給錯值。
    """
    a = chunk_alias
    return f"""{a}.id::text, r.id::text, r.file_hash, r.file_name, r.title,
               r.market, r.source,
               r.summary, r.report_date, r.report_type, r.instrument_types,
               r.relates_stock, r.relates_futures, r.stock_targets, r.futures_targets,
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

    回傳列：(chunk_id, report_id, file_hash, file_name, title, market, source,
             summary, report_date, report_type, instrument_types, relates_stock,
             relates_futures, stock_targets, futures_targets, chunk_index,
             content, distance)，
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
    return [ChunkRow._make(r) for r in rows.all()]


def _lexical_sql(
    num_patterns: int,
    extra_conds: list[str],
    per_report: bool,
    limit: Optional[int] = 200,
) -> str:
    """組 lexical 召回 SQL。per_report=True 時每報告只取最近距離 chunk（DISTINCT ON）。

    per_report=False 時結構與原查詢完全一致（問答路徑沿用，不可變更語意）。

    末欄 `lex_hits` ＝**受 cap 限制後**實際取到的候選列數，用來讓 `LIMIT :cap` 的截斷
    可觀測（`lex_hits == cap` 即已截斷）。刻意寫成「對已 cap 的 CTE 下 scalar
    subquery」，而不是在 CTE 裡放 `count(*) OVER ()`：PG 的視窗函式在 LIMIT **之前**
    計算，放進 CTE 會強迫掃完全部命中列才算得出計數——那正好摧毀 `LIMIT` 提早結束
    掃描的能力（熱門詞可能是數十萬列）。對已 materialize 的 ≤cap 列再數一次幾乎免費。

    **刻意不加 ORDER BY 到 cap 之前。** 現況「取哪 cap 列」由 heap 物理順序決定
    （而且 `synchronize_seqscans` 預設 on，併發 seq scan 會從任意 block 起掃，同一
    查詢在不同時刻本來就可能拿到不同的 cap 列）。加 `ORDER BY c.id` 會強迫掃完全部
    命中列再取前 cap＝把「快而不完整」換成「慢且仍不完整」，是淨損失。唯一有語義的
    排序鍵是報告新近度，而那要 join `research_report.report_date`，成本更高。先用
    `lex_hits` 量出「實際被截斷的查詢佔比」再決定，不要照架構檢視報告直接加排序。
    """
    # `c.embedding IS NOT NULL` 是**防 500**，不是效能過濾：`embedding` 允許 NULL，
    # 而 `NULL <=> vector` 回 NULL，呼叫端 `retrieval.hybrid_search` 對每一列做
    # `float(row.distance)` ⇒ `float(None)` TypeError ⇒ 整個查詢 500。字面路（不像
    # dense 路那樣經 HNSW 索引，索引本身就不含 NULL）是唯一能把這種列撈出來的路徑。
    # 2026-07-30 實測生產 0 列 embedding IS NULL——所以這是**潛在**而非現行故障，但
    # ingest 中途被砍、或未來加入「先寫 chunk 後補嵌入」的流程就會踩到。
    # 放在 CTE 內（不是最外層）：讓 NULL 列連 `:cap` 名額都不佔。
    conds = (
        [f"c.content_norm LIKE :t{i}" for i in range(num_patterns)]
        + ["c.embedding IS NOT NULL"]
        + extra_conds
    )
    where = " AND ".join(conds)
    final_limit = "\n        LIMIT :limit" if limit is not None else ""
    if per_report:
        # 計數來源是 lex_base（cap 那一層），不是 DISTINCT ON 之後的 lex——後者已按
        # report_id 去重，數出來的是報告數、答不了「cap 有沒有咬到」。
        return f"""
        WITH lex_base AS MATERIALIZED (
            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding
            FROM research.report_chunk c
            JOIN research.research_report r ON r.id = c.report_id
            WHERE {where}
            LIMIT :cap
        ),
        lex_ranked AS MATERIALIZED (
            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding,
                   c.embedding <=> CAST(:q AS vector) AS distance
            FROM lex_base c
        ),
        lex AS MATERIALIZED (
            SELECT DISTINCT ON (c.report_id)
                   c.id, c.report_id, c.chunk_index, c.content, c.embedding, c.distance
            FROM lex_ranked c
            ORDER BY c.report_id, c.distance
        )
        SELECT {_meta_columns("l")},
               l.distance,
               (SELECT count(*) FROM lex_base) AS lex_hits
        FROM lex l
        JOIN research.research_report r ON r.id = l.report_id
        ORDER BY l.distance{final_limit}
    """
    return f"""
        WITH lex AS MATERIALIZED (
            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding
            FROM research.report_chunk c
            JOIN research.research_report r ON r.id = c.report_id
            WHERE {where}
            LIMIT :cap
        )
        SELECT {_meta_columns("l")},
               l.embedding <=> CAST(:q AS vector) AS distance,
               (SELECT count(*) FROM lex) AS lex_hits
        FROM lex l
        JOIN research.research_report r ON r.id = l.report_id
        ORDER BY distance{final_limit}
    """


async def search_chunks_lexical(
    session: AsyncSession,
    query_embedding: list[float],
    term_patterns: list[str],
    limit: Optional[int] = 200,
    cap: int = 2000,
    *,
    per_report: bool = False,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
) -> tuple[list[ChunkRow], int]:
    """字面比對路：content_norm 同時含全部 LIKE pattern 的片段，依向量距離排序。

    回 `(rows, lex_hits)`。rows 的結構與 search_chunks_meta 相同；`lex_hits` ＝受
    `cap` 限制後實際取到的候選列數，`lex_hits == cap` 即代表**候選被 cap 截斷**
    （呼叫端據此做遙測，見 retrieval.hybrid_search 的 stats）。

    **lex_hits 刻意獨立回傳，不併進 `_meta_columns` / `ChunkRow`**：那組欄位是位置式
    契約（見 `_meta_columns` docstring 的事故紀錄），插欄不會拋錯、只會讓寫死索引的
    消費端靜默指到錯欄。它在 SQL 裡是 ChunkRow 之後的末欄，故用 `ChunkRow._fields`
    推導切點，不寫死數字。

    MATERIALIZED CTE 先過濾（走 trgm GIN 索引），再對最多 cap 列算精確距離，避免
    planner 因 ORDER BY 走 HNSW。

    per_report=True 時，每篇報告只回最近的 chunk（DISTINCT ON c.report_id）；
    `limit=None` 可省略最終 lexical 報告數上限；預設 False＝現況，不變動語意。
    """
    if not term_patterns:
        return [], 0
    params: dict = {"q": _vec_literal(query_embedding), "cap": cap}
    if limit is not None:
        params["limit"] = limit
    for i, pat in enumerate(term_patterns):
        params[f"t{i}"] = pat
    extra = _meta_filters(
        params, market, instrument_type, relates_stock, relates_futures, report_type
    )
    sql = _lexical_sql(len(term_patterns), extra, per_report, limit=limit)
    result = await session.execute(text(sql), params)
    # 明確轉 tuple 再切片：SQLAlchemy Row 的 slice/int 存取語意隨版本變動過，而這裡切錯
    # 一欄不會拋錯、只會讓每一列的欄位整體位移（正是 _meta_columns docstring 的事故）。
    raw = [tuple(r) for r in result.all()]
    width = len(ChunkRow._fields)
    # lex_hits 是同一個 scalar subquery，每列同值；零列＝零命中（cap 不可能咬到）。
    lex_hits = int(raw[0][width]) if raw else 0
    return [ChunkRow._make(r[:width]) for r in raw], lex_hits


def _parse_vec_text(s: str) -> list[float]:
    """pgvector '[f1,f2,...]' 文字 → list[float]（純函式，可獨測）。"""
    body = s.strip().strip("[]").strip()
    if not body:
        return []
    return [float(x) for x in body.split(",")]


async def fetch_chunk_embeddings(
    session: AsyncSession, chunk_ids: Sequence[str]
) -> dict[str, list[float]]:
    """按 chunk_id 批次取已持久化 embedding（1024 維）；空輸入回 {}，查無的 id 缺鍵。

    embedding 不得加進 _meta_columns / ChunkRow（位置存取契約），故獨立批次查詢。
    """
    if not chunk_ids:
        return {}
    stmt = text(
        "SELECT id::text, embedding::text"
        " FROM research.report_chunk WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = await session.execute(stmt, {"ids": list(chunk_ids)})
    return {row[0]: _parse_vec_text(row[1]) for row in rows if row[1] is not None}
