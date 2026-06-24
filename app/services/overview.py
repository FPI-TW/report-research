"""問答總覽路徑：枚舉/聚合題改走全語料分面統計。

detect_overview() 判定是否為枚舉/聚合題（純規則）；resolve_filters() 把中文條件
解析成 OverviewFilters；aggregate_facets() 對 research.research_report 跑分面聚合得
CorpusOverview；format_facts()/render_overview_text() 序列化。本模組不呼叫 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.filename import BROKER_MAP, SOURCE_DISPLAY, source_display
from app.services.tagging import INSTRUMENT_DISPLAY, MARKET_DISPLAY
from app.services.textnorm import norm_for_match

# 聚合/枚舉提示詞：命中任一即視為「總覽題」
_OVERVIEW_CUES = (
    "所有", "全部", "有哪些", "哪些", "列出", "清單", "列表", "多少篇",
    "幾篇", "幾份", "種類", "類型", "一覽", "統計", "都有什麼", "有什麼",
)
_OVERVIEW_CUES_NORM = tuple(norm_for_match(c) for c in _OVERVIEW_CUES)


def detect_overview(q: str) -> bool:
    """命中聚合提示詞即視為總覽題（純函式）。"""
    s = norm_for_match(q)
    return any(c in s for c in _OVERVIEW_CUES_NORM)
