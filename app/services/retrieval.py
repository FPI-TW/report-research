"""混合檢索：dense（BGE-M3 cosine）+ 字面（pg_trgm LIKE）雙路召回與融合排序。

融合策略：tier 硬性分層 + 加分後的分數排序——
  tier 2：片段正規化後包含完整查詢片語（如 "ai伺服器"）        bonus 0.25
  tier 1：包含全部查詢詞（≥2 詞，如同時含 "ai" 與 "伺服器"）   bonus 0.15
  tier 0：其他                                                bonus 0.05 × 覆蓋率
  fused = min(0.999, dense_sim + bonus)；排序鍵 = (tier, fused) DESC。
tier 是硬保證：字面全中永遠排在純語意命中之前，不靠 bonus 大小賭。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.store import search_chunks_lexical, search_chunks_meta
from app.services.textnorm import norm_for_match

logger = logging.getLogger(__name__)

W_PHRASE = 0.25
W_ALL = 0.15
W_PARTIAL = 0.05
# 問答/研報共用的 tier 契約：hybrid_search 產出、select_reports 門檻消費
# （TIER_ALL_TERMS 以上＝字面命中一律放行）。與搜尋頁 BAND_WIDTH 無關，禁止合流。
TIER_SEMANTIC = 0
TIER_ALL_TERMS = 1
TIER_PHRASE = 2
DENSE_SCAN_MIN = 120
LEX_LIMIT = 200
LEX_CAP = 2000

# 連續英數段 / 連續 CJK 段（已 NFKC 正規化，全形字母會先轉半形）
_RE_RUN = re.compile(r"[a-z0-9]+|[㐀-䶿一-鿿]+")
_LIKE_ESC = str.maketrans({"%": r"\%", "_": r"\_", "\\": r"\\"})


def extract_terms(q: str) -> tuple[str, list[str]]:
    """查詢 → (正規化片語, 查詢詞列表)。

    "AI伺服器" / "AI 伺服器" / "ＡＩ伺服器" → ("ai伺服器", ["ai", "伺服器"])
    """
    phrase = norm_for_match(q)
    terms = list(dict.fromkeys(_RE_RUN.findall(phrase)))
    return phrase, terms


def classify_match(phrase: str, terms: list[str], content: str) -> tuple[int, float]:
    """content 相對 (phrase, terms) 的字面命中分層與融合加成 → (tier, bonus)。

    hybrid_search 逐候選呼叫；多查詢合併後以原始主題重算 tier 亦共用此判定
    （retrieval_pipeline._retier_to_question），確保兩處 phrase/all-terms 尺度不漂移。
    """
    nc = norm_for_match(content)
    hit_phrase = bool(phrase) and phrase in nc
    coverage = (sum(t in nc for t in terms) / len(terms)) if terms else 0.0
    if hit_phrase:
        return TIER_PHRASE, W_PHRASE
    if coverage == 1.0 and len(terms) >= 2:
        return TIER_ALL_TERMS, W_ALL
    return TIER_SEMANTIC, W_PARTIAL * coverage


async def hybrid_search(
    session: AsyncSession,
    q: str,
    query_embedding: list[float],
    *,
    k: int = 10,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
    dense_scan: Optional[int] = None,
    lex_limit: Optional[int] = None,
    lex_cap: Optional[int] = None,
    lex_per_report: bool = False,
    lex_unlimited: bool = False,
    stats: dict | None = None,
) -> list[tuple[int, float, tuple]]:
    """雙路召回 + 去重 + 融合排序。

    回傳 [(tier, fused_score, row), ...]，依 (tier, fused) 由高到低排序。
    row 結構同 store._meta_columns + distance（首欄 chunk_id）。

    `stats` 給定時填入字面路的召回遙測：`lex_hits`（受 cap 限制後的候選列數）、
    `lex_cap`（本次使用的 cap）、`lex_truncated`（候選是否已被 cap 截斷）。
    **刻意用「傳入的 dict 由本函式填寫」而不是多回一個值**：hybrid_search 的回傳型別
    被四個生產呼叫端與數十個測試 fake 依賴，改成 tuple 會讓每個 fake 都得跟著改（本
    專案已有三次「fake 簽章漂移 → TypeError 被吞 → 靜默走錯路徑」的紀錄）。遙測是可
    選的旁路資訊，不該讓主契約為它變形。
    """
    phrase, terms = extract_terms(q)
    filters = dict(
        market=market,
        instrument_type=instrument_type,
        relates_stock=relates_stock,
        relates_futures=relates_futures,
        report_type=report_type,
    )
    scan = dense_scan if dense_scan is not None else max(DENSE_SCAN_MIN, k * 8)
    dense_rows = await search_chunks_meta(session, query_embedding, scan=scan, **filters)
    lex_rows = []
    lex_hits = 0
    cap = lex_cap if lex_cap is not None else LEX_CAP
    if terms:
        patterns = ["%" + t.translate(_LIKE_ESC) + "%" for t in terms]
        lex_row_limit = None if lex_unlimited else (
            lex_limit if lex_limit is not None else LEX_LIMIT
        )
        lex_rows, lex_hits = await search_chunks_lexical(
            session,
            query_embedding,
            patterns,
            limit=lex_row_limit,
            cap=cap,
            per_report=lex_per_report,
            **filters,
        )
    if stats is not None:
        stats["lex_hits"] = lex_hits
        stats["lex_cap"] = cap
        # >= 而非 ==：cap 是 SQL LIMIT，理論上不會超過，但用 >= 讓「cap 改小後拿到舊
        # 計數」這類意外落在保守側（寧可誤報截斷，不可漏報）。
        stats["lex_truncated"] = bool(terms) and lex_hits >= cap

    seen: set[str] = set()
    scored: list[tuple[int, float, tuple]] = []
    skipped_null_distance = 0
    for row in list(lex_rows) + list(dense_rows):
        chunk_id = row.chunk_id
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        if row.distance is None:
            # 縱深防禦第二層。`embedding` 允許 NULL，而 `NULL <=> vector` 回 NULL ⇒
            # 下面那行 `float(None)` 會 TypeError ⇒ **整個查詢 500**。第一層是兩條
            # SQL 的 `embedding IS NOT NULL`（store.py），這裡接住任何繞過它的路徑
            # （新查詢、手改 SQL、未來的 UNION 分支）。
            # 刻意「跳過並記數」而非拋例外：少一列候選是可接受的降級，整頁 500 不是。
            # 但**必須留下訊號**——靜默跳過會讓「ingest 中途被砍」變成永遠查不出的
            # 召回缺口。計數走 stats（進 qa_log）與 logger 各一份。
            skipped_null_distance += 1
            continue
        dense_sim = 1.0 - float(row.distance)
        tier, bonus = classify_match(phrase, terms, row.content)
        fused = min(0.999, round(dense_sim + bonus, 4))
        scored.append((tier, fused, row))

    if skipped_null_distance:
        logger.warning(
            "檢索跳過 %s 列 embedding IS NULL 的 chunk——ingest 不完整，"
            "請跑 `make db-audit` 確認範圍",
            skipped_null_distance,
        )
    if stats is not None:
        stats["null_embedding_skipped"] = skipped_null_distance

    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return scored


BAND_WIDTH = 0.05
# 檢索分頁專用召回深度（與問答路徑的 k*8 脫鉤；見 hybrid_search 選用參數）
DENSE_SCAN_SEARCH = 600
LEX_LIMIT_SEARCH = 1000
LEX_CAP_SEARCH = 8000


@dataclass
class RankedReport:
    """一篇報告的聚合結果：代表性 tier/分數取最佳 chunk，passages 依 chunk 順序累積。"""

    report_id: str
    tier: int
    best_score: float
    report_date: object  # datetime.date | None
    meta_row: tuple
    passages: list = field(default_factory=list)  # list[tuple[float, tuple]]
    match_count: int = 0


def _date_ordinal(d) -> int:
    """日期 → 序數；None → 0（在反向排序中最小，故殿後）。"""
    return d.toordinal() if d is not None else 0


def rank_reports(scored, *, sort: str = "relevance") -> list[RankedReport]:
    """把 (tier, fused, row) chunk 清單分組成報告並排序。

    scored 已依 (tier, fused) 由高到低排序，故每篇首見 chunk 即其最佳 tier/分數。
    - relevance：(tier, band, 日期, fused) 由高到低——相關度分層內最新優先（band=0.05）。
    - date_desc：全召回報告依日期新→舊（None 殿後）。
    - date_asc：全召回報告依日期舊→新（None 殿後）。
    report_id 作為最終 tiebreak，確保分頁切片穩定、不跨頁重複。
    """
    groups: dict[str, RankedReport] = {}
    for tier, fused, row in scored:
        rid = row.report_id
        g = groups.get(rid)
        if g is None:
            g = RankedReport(
                report_id=rid,
                tier=tier,
                best_score=fused,
                report_date=row.report_date,
                meta_row=row,
            )
            groups[rid] = g
        g.match_count += 1
        g.passages.append((fused, row))

    reports = list(groups.values())
    if sort == "date_desc":
        reports.sort(
            key=lambda g: (_date_ordinal(g.report_date), g.best_score, g.report_id),
            reverse=True,
        )
    elif sort == "date_asc":
        reports.sort(
            key=lambda g: (
                g.report_date is None,  # False(0) 在前、None(True=1) 殿後
                _date_ordinal(g.report_date),
                -g.best_score,
                g.report_id,
            )
        )
    else:  # relevance：相關度分層內最新優先
        reports.sort(
            key=lambda g: (
                g.tier,
                int(g.best_score / BAND_WIDTH + 1e-9),  # +eps 避開浮點邊界誤判
                _date_ordinal(g.report_date),
                g.best_score,
                g.report_id,
            ),
            reverse=True,
        )
    return reports
