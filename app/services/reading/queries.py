"""研報閱讀頁純 SQL 取數層（named param、無字串拼接注入；風格對齊 radar/queries.py）。

六個讀取面向，全部以 report_id / file_hash 為鍵、讀取時零 LLM：

- fetch_doc：閱讀頁骨架（含 full_text 原文，正典化交給呼叫端）
- fetch_takeaways：重點摘錄（含 text_sha256 供呼叫端驗章）
- fetch_signals：結構化訊號（jsonb 以 ::text 取出後 json.loads，重用 radar 的 parse）
- fetch_instrument_names：訊號標的的公司名（report_signal 只存代號，名稱另查）
- fetch_similar：相似研報（全篇均勻取樣 probe → 逐 probe 最近鄰 → 廣度加權）
- fetch_chunk_content：單一 chunk 原文（供 anchor.locate_chunk 錨回正典文字）

**bind 參數禁忌**：SQLAlchemy 的 text() 以 regex 掃 `:name`，其負向前瞻 `(?!:)` 會讓
「參數名緊接 ::」的寫法回溯成短名（`:markets::text[]` → 綁到不存在的 `market`，冒號
原樣進 PG）→ 生產 500。轉型一律寫 `CAST(:x AS text[])`，不可寫 `:x::text[]`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.radar.types import SIGNAL_SELECT_SQL, Signal, parse_signal_row

# 可展示狀態：pending/rejected 不給讀者看（見 reading/schemas.py 模組 docstring：
# DB 的 extraction_status 記錄「批次做了什麼」，與讀者視角的 *_state 刻意分離）
VALID_STATUSES = ["valid", "partial"]

# 相似研報查詢的 HNSW 掃描深度。`c.report_id <> :rid` 是索引掃描「之後」才 recheck 的
# 條件：本篇自己的 chunk 與 probe 幾乎零距離，必定佔滿 top-k，預設 ef_search=40 會讓
# 外篇根本擠不進候選集。拉高到 100 留足餘裕。
SIMILAR_EF_SEARCH = 100

# **拉高 ef_search 只是把候選集變大，沒有解決「過濾掉之後不夠了就不再往下掃」。**
# `c.report_id <> :rid` 正是 pgvector 所謂 post-filter：HNSW 先取 ef_search 個最近鄰，
# 再套 WHERE。長報告有數百個 chunk，全都比外篇更近——過濾後剩下的可能遠少於 LIMIT，
# 而沒有 iterative_scan 時掃描就停在那裡，**不會報錯、只會靜默少回幾篇**（正是這支查詢
# 那三條「錯了不會報錯、只會變雜訊」不變量的第四條）。
#
# `relaxed_order` 而非 `strict_order`：與 `store.search_chunks_meta` 一致，且本查詢的
# 排序鍵本來就不是原始距離（`_SIMILAR_SQL` 用的是跨 probe 的廣度加權），嚴格距離序
# 對它沒有意義，換來的只是更慢。
#
# 需要 pgvector ≥ 0.8——0.7 環境會直接 `unrecognized configuration parameter` ⇒
# 每次開閱讀頁 500。`assert_pgvector_version()` 在啟動時就擋下這種環境。
SIMILAR_ITERATIVE_SCAN = "relaxed_order"


@dataclass
class DocRow:
    """research_report 一列（閱讀頁需要的欄位子集）。full_text 為未清理的原始抽取文字。"""

    report_id: str
    file_hash: str
    file_name: str
    # 報告內部標題（scripts/generate_titles.py 產出）。None＝尚未產生，呈現層回退檔名。
    title: Optional[str]
    file_path: Optional[str]
    source_object_key: Optional[str]
    market: Optional[str]
    source: Optional[str]
    report_date: Optional[date]
    report_type: Optional[str]
    summary: Optional[str]
    instrument_types: list[str]
    stock_targets: list[str]
    futures_targets: list[str]
    full_text: Optional[str]


@dataclass
class TakeawayRow:
    """report_takeaway 一列。

    刻意**不是** reading/schemas.py 的 Takeaway：本列帶 text_sha256，是契約模型沒有
    的欄位。offset 驗章（擷取當時的正典文字 sha vs 當前的）屬於呈現政策，交由端點
    處理；此層只忠實取數。
    """

    ordinal: int
    claim: str
    quote: Optional[str]
    quote_start: Optional[int]
    quote_end: Optional[int]
    anchor_method: Optional[str]
    text_sha256: str


@dataclass
class SimilarRow:
    """一篇相似研報。matched_probes/total_probes 供前端顯示「9/12 段相符」。"""

    file_hash: str
    file_name: str
    title: Optional[str]
    market: Optional[str]
    source: Optional[str]
    report_date: Optional[date]
    summary: Optional[str]
    matched_probes: int
    total_probes: int
    score: float


_DOC_SQL = text(
    "SELECT id::text, file_hash, file_name, title, file_path, market, source, "
    "       report_date, report_type, summary, instrument_types, "
    "       stock_targets, futures_targets, full_text, source_object_key "
    "FROM research.research_report "
    "WHERE file_hash = :file_hash AND is_research IS NOT FALSE"
)


async def fetch_doc(session: AsyncSession, file_hash: str) -> Optional[DocRow]:
    """依 file_hash 取單篇研報；查無或非研究檔 → None。

    file_hash（非 report_id）是閱讀頁的網址鍵：重新 ingest 會換新 report_id，
    分享出去的連結必須存活。
    """
    row = (await session.execute(_DOC_SQL, {"file_hash": file_hash})).first()
    if row is None:
        return None
    return DocRow(
        report_id=row[0],
        file_hash=row[1],
        file_name=row[2],
        title=row[3],
        file_path=row[4],
        source_object_key=row[14] if len(row) > 14 else None,
        market=row[5],
        source=row[6],
        report_date=row[7],
        report_type=row[8],
        summary=row[9],
        instrument_types=list(row[10]) if row[10] else [],
        stock_targets=list(row[11]) if row[11] else [],
        futures_targets=list(row[12]) if row[12] else [],
        full_text=row[13],
    )


_TAKEAWAYS_SQL = text(
    "SELECT ordinal, claim, quote, quote_start, quote_end, anchor_method, text_sha256 "
    "FROM research.report_takeaway "
    "WHERE report_id = :report_id AND extraction_status = ANY(:statuses) "
    "ORDER BY ordinal"
)


async def fetch_takeaways(
    session: AsyncSession, report_id: str, *, statuses=VALID_STATUSES
) -> list[TakeawayRow]:
    """依 report_id 取可展示的重點摘錄，已依 ordinal 排序。"""
    rows = (
        await session.execute(
            _TAKEAWAYS_SQL, {"report_id": report_id, "statuses": list(statuses)}
        )
    ).all()
    return [
        TakeawayRow(
            ordinal=int(r[0]),
            claim=r[1],
            quote=r[2],
            quote_start=r[3],
            quote_end=r[4],
            anchor_method=r[5],
            text_sha256=r[6],
        )
        for r in rows
    ]


_SIGNALS_SQL = text(
    f"SELECT {SIGNAL_SELECT_SQL} "
    "FROM research.report_signal s "
    "JOIN research.research_report r ON r.id = s.report_id "
    "WHERE s.report_id = :report_id AND s.extraction_status = ANY(:statuses) "
    "ORDER BY s.market, s.instrument_code"
)


async def fetch_signals(
    session: AsyncSession, report_id: str, *, statuses=VALID_STATUSES
) -> list[Signal]:
    """依 report_id 取該篇的結構化訊號（只有部分研報有，空是常態不是錯誤）。

    jsonb 欄位由 SIGNAL_SELECT_COLUMNS 以 ::text 取出、parse_signal_row 解析，
    與觀點雷達共用同一組解析邏輯（避免兩套 jsonb 行為漂移）。
    """
    rows = (
        await session.execute(
            _SIGNALS_SQL, {"report_id": report_id, "statuses": list(statuses)}
        )
    ).all()
    return [parse_signal_row(r) for r in rows]


# report_signal 只存標的代號，沒有名稱欄位。全語料唯一的名稱來源是
# research_report.company_name（filename.py 從檔名解析而來），取「最新一份以該代號為
# stock_code 的報告」——**與雷達的 _COVERAGE_SQL／_catalog_cte 逐字同語意**，否則同一
# 檔標的在閱讀頁與雷達會叫不同名字，而那種分岔沒有任何錯誤訊息。
#
# 名稱是漸進補的：檔名解析不出公司名的報告 company_name 為 NULL，**查無名稱是常態不是
# 錯誤**（呈現層回退成只顯示代號，比照 title → file_name）。
#
# 轉型一律 CAST(:x AS text[])：`:markets::text[]` 會讓 compiler 回溯成短名而參數綁不上
# （PR #89 的生產 500，見檔頭「bind 參數禁忌」）。
_INSTRUMENT_NAMES_SQL = text(
    """
    SELECT DISTINCT ON (r.market, r.stock_code)
           r.market, r.stock_code, r.company_name
      FROM research.research_report r
     WHERE (r.market, r.stock_code) IN (
             SELECT m, c
               FROM unnest(CAST(:markets AS text[]), CAST(:codes AS text[])) AS t(m, c))
       AND r.company_name IS NOT NULL
       AND r.is_research IS NOT FALSE
     ORDER BY r.market, r.stock_code,
              r.report_date DESC NULLS LAST, r.created_at DESC, r.id
    """
)


async def fetch_instrument_names(
    session: AsyncSession, keys: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """(market, instrument_code) → 公司名。查無名稱的鍵不進 dict（呼叫端回退代號）。

    **keys 為空時完全不打 DB**：多數研報沒有訊號，那些閱讀頁不該為了這個欄位
    多付一次 roundtrip。

    空白字串在此就收成「沒有名稱」：SQL 端刻意只濾 NULL（與雷達逐字一致），呈現層拿到
    全空白的名稱會渲染出一塊看不見卻佔位的元素，比沒有名稱更糟。
    """
    if not keys:
        return {}
    rows = (
        await session.execute(
            _INSTRUMENT_NAMES_SQL,
            {"markets": [m for m, _ in keys], "codes": [c for _, c in keys]},
        )
    ).all()
    out: dict[tuple[str, str], str] = {}
    for market, code, name in rows:
        cleaned = (name or "").strip()
        if cleaned:
            out[(market, code)] = cleaned
    return out


_CHUNK_CONTENT_SQL = text(
    "SELECT content FROM research.report_chunk "
    "WHERE report_id = CAST(:rid AS uuid) AND chunk_index = :ci"
)


async def fetch_chunk_content(
    session: AsyncSession, report_id: str, chunk_index: int
) -> Optional[str]:
    """取單一 chunk 的原文；查無 → None。

    原供閱讀頁「跳到檢索命中那一段」（該互動已於 2026-08-03 隨文字檢視移除，SPA 不再
    帶 `?chunk=`，故此函式目前只有 /text 端點與測試會走）：呼叫端把回傳字串交給
    anchor.locate_chunk 錨回正典文字。**查無不是錯誤**（連結可能來自已重新 ingest 的
    舊檢索結果）—— 呼叫端據此不回 offset，頁面照常。

    轉型用 `CAST(:rid AS uuid)`（見模組 docstring 的 bind 參數禁忌）。
    """
    row = (
        await session.execute(_CHUNK_CONTENT_SQL, {"rid": report_id, "ci": chunk_index})
    ).first()
    return row[0] if row else None


# ── 相似研報 ────────────────────────────────────────────────────────────
#
# **為什麼 probe 要全篇均勻取樣，而不是取前 N 塊**：研報開頭幾乎都是封面、目錄、
# 免責聲明樣板。拿前 N 塊當 probe＝拿樣板當查詢，召回的只會是「同樣有樣板的報告」，
# 與內容相似度無關。故沿 chunk_index 取 probe_n 個「均勻鋪滿全篇」的位置：
#   rn = floor(i * n / min(probe_n, n))，  i ∈ [0, probe_n)
# （n＝可嵌入 chunk 數）——不論長短報告都橫跨整篇。
#
# **不可用整數除法模數（rn % (n / probe_n)）**：當 n ∈ [probe_n, 2*probe_n)（幾頁的
# 短研報最常見）時 n / probe_n 被整數截成 1，每一塊都命中，再被 LIMIT 砍成「前
# probe_n 塊」＝正好退化回封面/目錄/免責樣板。唯有 n ≥ 2*probe_n 才真的均勻——
# 對半數語料而言旗艦「相似研報」等於拿樣板在比，且無聲（不報錯、只是召回變雜訊）。
#
# **為什麼排序公式是 Σ(1 - best_dist) 而不是 min(dist)**：券商研報的法律免責聲明
# 幾乎一模一樣，會產生近乎相同的 chunk。若只用 min(dist) 排序，任兩篇研報都會因為
# 那一塊樣板而看起來「極度相似」，榜單全是雜訊。
#   score = Σ(1 - best_dist)（每個 probe 先取該篇的最佳距離，再跨 probe 加總）
# 同時獎勵「相符的段落夠多」（廣度）與「相符得夠近」（貼近度）：一塊樣板只能貢獻
# 一個 probe 的分數，蓋不過真正多段相符的報告。
#   min_probes >= 2 再補一刀：單一樣板塊無法把一篇推上榜。
#
# best CTE 的 DISTINCT ON (pe, report_id) 是「每個 probe 對每篇只計一次最佳距離」，
# 否則同一篇的多個 chunk 會對同一 probe 灌票。
_SIMILAR_SQL = text(
    """
    WITH ranked AS (
        SELECT c.embedding,
               row_number() OVER (ORDER BY c.chunk_index) - 1 AS rn
        FROM research.report_chunk c
        WHERE c.report_id = :rid AND c.embedding IS NOT NULL
    ), tot AS (
        SELECT count(*) AS n FROM ranked
    ), probe AS (
        -- 沿 chunk_index 均勻取最多 :probe_n 個位置（見上方註解的取樣理由與整數除法陷阱）
        SELECT r.embedding
        FROM ranked r
        JOIN (
            SELECT DISTINCT
                   floor(gs * t.n::numeric / LEAST(:probe_n, GREATEST(t.n, 1)))::int AS rn
            FROM tot t, generate_series(0, :probe_n - 1) AS gs
            WHERE t.n > 0
        ) pick ON pick.rn = r.rn
    ), hit AS (
        SELECT p.embedding AS pe, n.report_id, n.dist
        FROM probe p CROSS JOIN LATERAL (
            SELECT c.report_id, c.embedding <=> p.embedding AS dist
            FROM research.report_chunk c
            WHERE c.report_id <> :rid
            ORDER BY c.embedding <=> p.embedding
            LIMIT :per_probe
        ) n
        WHERE n.dist <= :max_dist
    ), best AS (
        SELECT DISTINCT ON (pe, report_id) pe, report_id, dist
        FROM hit ORDER BY pe, report_id, dist
    ), agg AS (
        SELECT report_id, count(*) AS matched_probes,
               sum(1 - dist) AS score, min(dist) AS best_dist
        FROM best GROUP BY report_id
    )
    SELECT r.file_hash, r.file_name, r.market, r.source, r.report_date, r.summary,
           a.matched_probes, a.score, (SELECT count(*) FROM probe) AS total_probes,
           r.title
    FROM agg a JOIN research.research_report r ON r.id = a.report_id
    WHERE r.is_research IS NOT FALSE AND a.matched_probes >= :min_probes
    ORDER BY a.score DESC, a.best_dist ASC, r.report_date DESC NULLS LAST, r.file_name
    LIMIT :limit
    """
)


async def fetch_similar(
    session: AsyncSession,
    report_id: str,
    probe_n: int = 12,
    per_probe: int = 20,
    max_dist: float = 0.45,
    min_probes: int = 2,
    limit: int = 6,
) -> list[SimilarRow]:
    """找與本篇內容相似的其他研報（見上方 _SIMILAR_SQL 的取樣與排序理由）。

    total_probes 為實際取到的 probe 數（可能少於 probe_n：短報告的 chunk 數不足），
    隨每列一起回傳，供前端顯示「9/12 段相符」。無命中時回空 list。
    """
    # SET LOCAL 只在本交易有效；常數為模組 int/字面，非外部輸入（PG 的 SET 不吃 bind）
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {SIMILAR_EF_SEARCH}"))
    await session.execute(
        text(f"SET LOCAL hnsw.iterative_scan = {SIMILAR_ITERATIVE_SCAN}")
    )
    rows = (
        await session.execute(
            _SIMILAR_SQL,
            {
                "rid": report_id,
                "probe_n": probe_n,
                "per_probe": per_probe,
                "max_dist": max_dist,
                "min_probes": min_probes,
                "limit": limit,
            },
        )
    ).all()
    return [
        SimilarRow(
            file_hash=r[0],
            file_name=r[1],
            # title 刻意 append 在既有欄位之後：本列以位移解包，插在中段會讓每個
            # 索引無聲位移一格（見 rows.ChunkRow 的同款教訓）。
            title=r[9],
            market=r[2],
            source=r[3],
            report_date=r[4],
            summary=r[5],
            matched_probes=int(r[6] or 0),
            total_probes=int(r[8] or 0),
            score=float(r[7] or 0.0),
        )
        for r in rows
    ]
