"""混合檢索：dense（BGE-M3 cosine）+ 字面（pg_trgm LIKE）雙路召回與融合排序。

融合策略：tier 硬性分層 + 加分後的分數排序——
  tier 2：片段正規化後包含完整查詢片語（如 "ai伺服器"）        bonus 0.25
  tier 1：包含全部查詢詞（≥2 詞，如同時含 "ai" 與 "伺服器"）   bonus 0.15
  tier 0：其他                                                bonus 0.05 × 覆蓋率
  fused = min(0.999, dense_sim + bonus)；排序鍵 = (tier, fused) DESC。
tier 是硬保證：字面全中永遠排在純語意命中之前，不靠 bonus 大小賭。
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.store import search_chunks_lexical, search_chunks_meta
from app.services.textnorm import norm_for_match

W_PHRASE = 0.25
W_ALL = 0.15
W_PARTIAL = 0.05
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


async def hybrid_search(
    session: AsyncSession,
    q: str,
    query_embedding: list[float],
    *,
    k: int,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
) -> list[tuple[int, float, tuple]]:
    """雙路召回 + 去重 + 融合排序。

    回傳 [(tier, fused_score, row), ...]，依 (tier, fused) 由高到低排序。
    row 結構同 store._meta_columns + distance（首欄 chunk_id）。
    """
    phrase, terms = extract_terms(q)
    filters = dict(
        market=market,
        instrument_type=instrument_type,
        relates_stock=relates_stock,
        relates_futures=relates_futures,
        report_type=report_type,
    )
    dense_rows = await search_chunks_meta(
        session, query_embedding, scan=max(DENSE_SCAN_MIN, k * 8), **filters
    )
    lex_rows = []
    if terms:
        patterns = ["%" + t.translate(_LIKE_ESC) + "%" for t in terms]
        lex_rows = await search_chunks_lexical(
            session, query_embedding, patterns, limit=LEX_LIMIT, cap=LEX_CAP, **filters
        )

    seen: set[str] = set()
    scored: list[tuple[int, float, tuple]] = []
    for row in list(lex_rows) + list(dense_rows):
        chunk_id = row[0]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        dense_sim = 1.0 - float(row[-1])
        nc = norm_for_match(row[-2])  # content
        hit_phrase = bool(phrase) and phrase in nc
        coverage = (sum(t in nc for t in terms) / len(terms)) if terms else 0.0
        hit_all = coverage == 1.0 and len(terms) >= 2
        if hit_phrase:
            tier, bonus = 2, W_PHRASE
        elif hit_all:
            tier, bonus = 1, W_ALL
        else:
            tier, bonus = 0, W_PARTIAL * coverage
        fused = min(0.999, round(dense_sim + bonus, 4))
        scored.append((tier, fused, row))

    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return scored
