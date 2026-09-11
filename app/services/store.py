"""向量入庫：依 file_hash 去重 upsert（先刪後插），寫入報告 + 分塊向量。"""

from __future__ import annotations

import json
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
    source_object_key: Optional[str] = None
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
    # ── E1b 抽取層欄位（全部可省，舊呼叫端不動）──
    extractor: Optional[str] = None
    extraction_version: Optional[str] = None
    quality_score: Optional[float] = None
    quality_flags: Optional[dict] = None
    page_count: Optional[int] = None
    pages_failed: Optional[list[int]] = None
    needs_review: bool = False


# extraction_log.stopped_at 的封閉詞彙。**與 db/schema.sql 的 CHECK 逐字一致**
# （tests/test_extraction_log.py 釘住）；改一邊要一起改，而且 CHECK 在既有庫是 no-op，
# 得另外寫 ALTER（見 db/expected_constraints.txt 檔頭）。
STOPPED_AT = ("ingested", "skip_admin", "scanned", "not_research", "extract_error")


@dataclass
class ExtractionLogRow:
    file_hash: str
    file_name: str  # 單一檔名；同 hash 的其他檔名由 upsert 併進 file_names 陣列
    extractor: str
    extraction_version: str
    stopped_at: str
    page_count: Optional[int] = None
    pages_failed: Optional[list[int]] = None
    char_count: Optional[int] = None
    quality_score: Optional[float] = None
    quality_flags: Optional[dict] = None


def needs_review(quality_score: Optional[float], pages_failed: Optional[Sequence[int]], review_min: float) -> bool:
    """品質閘的判定：只標記不擋。分數低於門檻、或有任何頁級失敗，都算要人看。
    分數為 None（pypdf 路徑）不算低分——沒量到不是壞。"""
    if pages_failed:
        return True
    return quality_score is not None and quality_score < review_min


async def upsert_extraction_log(session: AsyncSession, row: ExtractionLogRow) -> None:
    """每個進過管線的 file_hash 寫一列；同 hash 再來時覆寫落點與品質欄位、**檔名累加不覆蓋**。

    呼叫端要自己 commit（多半與 research_report 同一個交易）。"""
    if row.stopped_at not in STOPPED_AT:
        raise ValueError(f"stopped_at 不在封閉詞彙內：{row.stopped_at!r}")
    await session.execute(
        text(
            """
            INSERT INTO research.extraction_log
                (file_hash, file_names, extractor, extraction_version, page_count, pages_failed,
                 char_count, quality_score, quality_flags, stopped_at, updated_at)
            VALUES
                (:file_hash, ARRAY[:file_name]::text[], :extractor, :extraction_version, :page_count,
                 CAST(:pages_failed AS int[]), :char_count, :quality_score,
                 CAST(:quality_flags AS jsonb), :stopped_at, now())
            ON CONFLICT (file_hash) DO UPDATE SET
                file_names = (
                    SELECT array_agg(DISTINCT n ORDER BY n)
                    FROM unnest(research.extraction_log.file_names || EXCLUDED.file_names) AS n
                ),
                extractor = EXCLUDED.extractor,
                extraction_version = EXCLUDED.extraction_version,
                page_count = EXCLUDED.page_count,
                pages_failed = EXCLUDED.pages_failed,
                char_count = EXCLUDED.char_count,
                quality_score = EXCLUDED.quality_score,
                quality_flags = EXCLUDED.quality_flags,
                stopped_at = EXCLUDED.stopped_at,
                updated_at = now()
            """
        ),
        {
            "file_hash": row.file_hash,
            "file_name": row.file_name,
            "extractor": row.extractor,
            "extraction_version": row.extraction_version,
            "page_count": row.page_count,
            "pages_failed": list(row.pages_failed) if row.pages_failed else None,
            "char_count": row.char_count,
            "quality_score": row.quality_score,
            "quality_flags": json.dumps(row.quality_flags or {}, ensure_ascii=False),
            "stopped_at": row.stopped_at,
        },
    )


async def replace_report_extraction(
    session: AsyncSession,
    report_id: str,
    *,
    full_text: str,
    language: Optional[str],
    chunks: list[str],
    embeddings: list[list[float]],
    fields: dict,
) -> None:
    """回填（E1d）：**原地**換掉一份既有研報的全文、chunk 與抽取欄位，report_id 不變。

    刻意不用 upsert_report——那條路徑是 DELETE 再 INSERT 新 id，report_takeaway／
    report_signal 以 FK CASCADE 掛在舊 id 上，會被連帶清空。這裡只動 report_chunk
    （重切重嵌）與 research_report 的欄位。呼叫端自己 commit（與重錨定同一個交易）。"""
    await session.execute(
        text("DELETE FROM research.report_chunk WHERE report_id = :rid"), {"rid": report_id}
    )
    if chunks:
        await session.execute(
            text(
                """
                INSERT INTO research.report_chunk (id, report_id, chunk_index, content, embedding)
                VALUES (:id, :report_id, :chunk_index, :content, CAST(:embedding AS vector))
                """
            ),
            [
                {
                    "id": str(uuid.uuid4()),
                    "report_id": report_id,
                    "chunk_index": i,
                    "content": c,
                    "embedding": _vec_literal(e),
                }
                for i, (c, e) in enumerate(zip(chunks, embeddings))
            ],
        )
    await session.execute(
        text(
            """
            UPDATE research.research_report SET
                full_text = :full_text,
                language = :language,
                extractor = :extractor,
                extraction_version = :extraction_version,
                quality_score = :quality_score,
                quality_flags = CAST(:quality_flags AS jsonb),
                page_count = :page_count,
                pages_failed = CAST(:pages_failed AS int[]),
                needs_review = :needs_review
            WHERE id = :rid
            """
        ),
        {
            "rid": report_id,
            "full_text": full_text,
            "language": language,
            "extractor": fields.get("extractor"),
            "extraction_version": fields.get("extraction_version"),
            "quality_score": fields.get("quality_score"),
            "quality_flags": json.dumps(fields.get("quality_flags") or {}, ensure_ascii=False),
            "page_count": fields.get("page_count"),
            "pages_failed": list(fields["pages_failed"]) if fields.get("pages_failed") else None,
            "needs_review": bool(fields.get("needs_review", False)),
        },
    )


async def mark_report_extraction(session: AsyncSession, report_id: str, fields: dict) -> None:
    """回填時新抽取器抽不出字：**保留舊全文與 chunk**，只更新版本與旗標，讓它不再被排進回填。"""
    await session.execute(
        text(
            """
            UPDATE research.research_report SET
                extractor = :extractor,
                extraction_version = :extraction_version,
                quality_flags = CAST(:quality_flags AS jsonb),
                needs_review = true
            WHERE id = :rid
            """
        ),
        {
            "rid": report_id,
            "extractor": fields.get("extractor"),
            "extraction_version": fields.get("extraction_version"),
            "quality_flags": json.dumps(fields.get("quality_flags") or {}, ensure_ascii=False),
        },
    )


async def reanchor_takeaways(session: AsyncSession, report_id: str, canonical: str) -> tuple[int, int]:
    """回填後把該研報既有摘錄的錨點對新正典文字重算（E1 共識第 3 條：不重跑 LLM）。

    `text_sha256` 一律換成新正典文字的 sha——它是「這些錨點是對哪一份文字算的」的驗章；
    錨不回的列 quote_start／anchor_method 置 NULL（後端本來就會收回這種錨點）。
    回傳 (總列數, 錨得回的列數)。"""
    import hashlib

    from app.services.reading.anchor import locate_quote

    rows = (
        await session.execute(
            text("SELECT id::text, quote FROM research.report_takeaway WHERE report_id = CAST(:rid AS uuid)"),
            {"rid": report_id},
        )
    ).all()
    sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    anchored = 0
    for tid, quote in rows:
        a = locate_quote(canonical, quote or "")
        if a is not None:
            anchored += 1
        await session.execute(
            text(
                """
                UPDATE research.report_takeaway
                SET quote_start = :qs, quote_end = :qe, anchor_method = :method, text_sha256 = :sha
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {
                "tid": tid,
                "qs": a.start if a else None,
                "qe": a.end if a else None,
                "method": a.method if a else None,
                "sha": sha,
            },
        )
    return len(rows), anchored


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
                (id, file_hash, file_name, file_path, source_object_key, market, is_research,
                 confidence, stock_code, company_name, source, report_date,
                 report_type, language, instrument_types, relates_stock,
                 relates_futures, stock_targets, futures_targets, full_text,
                 extractor, extraction_version, quality_score, quality_flags,
                 page_count, pages_failed, needs_review)
            VALUES
                (:id, :file_hash, :file_name, :file_path, :source_object_key, :market, :is_research,
                 :confidence, :stock_code, :company_name, :source, :report_date,
                 :report_type, :language, CAST(:instrument_types AS text[]),
                 :relates_stock, :relates_futures,
                 CAST(:stock_targets AS text[]), CAST(:futures_targets AS text[]),
                 :full_text,
                 :extractor, :extraction_version, :quality_score, CAST(:quality_flags AS jsonb),
                 :page_count, CAST(:pages_failed AS int[]), :needs_review)
            """
        ),
        {
            "id": report_id,
            **report.__dict__,
            "quality_flags": (
                json.dumps(report.quality_flags, ensure_ascii=False) if report.quality_flags is not None else None
            ),
            "pages_failed": list(report.pages_failed) if report.pages_failed else None,
        },
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


async def pick_title_lead_term(
    session: AsyncSession, candidates: list[str]
) -> str | None:
    """從候選詞裡挑一個「像標的名」的詞——依據是語料自己的研報標題，不是外部詞典。

    `retrieval.hybrid_search` 的字面路在純中文問句上必然落空（見
    `retrieval.cjk_affix_candidates` 的說明），落空後要挑一個較短的詞重探，問題是
    **怎麼分辨「兆勁」與「分析」**。用外部斷詞詞典要多一個相依、且對繁中與券商用語
    的覆蓋是未知數；本 repo 也已經有一次「手抄繁中名詞白名單在雙語上線後破功」的
    紀錄（見 faithfulness.py）。

    這裡改用語料本身：`generate_titles.py` 產出的 `title` 是主題在前的繁中句子
    （「兆勁法說重點摘要：…」「勝一：法人說明會重點摘要」），所以**「有研報標題以它
    開頭」就是一個決定性、隨語料自動更新的『這是標的名』訊號**。「分析」「怎麼樣」
    這類問句框架詞不會出現在標題開頭，自然被濾掉。

    排序是**命中篇數多者優先、同分取較短者**。兩個 tie-break 都是實測逼出來的
    （2026-08-21，15 題）：取最長會讓「台積電最新的營運展望如何」選到只命中 1 篇的
    「台積電最」而不是命中 106 篇的「台積電」，而「台驊控股法說會重點是什麼」的
    五個前綴同為 2 篇、取最長會選到「台驊控股法說」——那串在 chunk 內文裡根本不存在，
    重探一樣是零命中。

    查不到（全部候選都不是任何標題的開頭）回 None，呼叫端維持現況不重探。
    """
    if not candidates:
        return None
    row = (
        await session.execute(
            text(
                "SELECT a FROM unnest(CAST(:c AS text[])) AS a "
                "WHERE EXISTS (SELECT 1 FROM research.research_report r "
                "              WHERE r.title LIKE a || '%') "
                "ORDER BY (SELECT count(*) FROM research.research_report r "
                "          WHERE r.title LIKE a || '%') DESC, length(a) ASC "
                "LIMIT 1"
            ),
            {"c": list(candidates)},
        )
    ).first()
    return str(row[0]) if row else None


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
