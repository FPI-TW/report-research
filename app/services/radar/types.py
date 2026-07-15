"""觀點雷達值型別 + jsonb→dataclass 解析（純函式，零 DB）。

Signal 對應 research.report_signal 一列（join research_report 取 file_name）。
jsonb 欄位由 queries.py 以 ::text 取出，這裡 json.loads，避免依賴 asyncpg codec 行為。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.services.signal_extract import THESIS_DIMENSIONS


@dataclass(frozen=True)
class EpsEstimate:
    fiscal_year: Optional[int]
    period: Optional[str]
    currency: Optional[str]
    unit: Optional[str]
    value: Optional[float]
    evidence: Optional[str]

    def group_key(self) -> tuple:
        """可比性分組鍵：同 (FY, period, currency, unit) 才可比較。"""
        return (self.fiscal_year, self.period, self.currency, self.unit)


@dataclass(frozen=True)
class DimensionStance:
    stance: Optional[str]
    summary: Optional[str]
    evidence: Optional[str]


@dataclass(frozen=True)
class Change:
    """單券商前後兩份訊號的一項差異（事件卡與時間線共用）。

    field: rating | target_price | eps | thesis
    direction: up | down | flat | incomparable | none
    comparable=False 時附 incomparable_reason（幣別/FY 不同/缺前值），不畫方向箭頭。
    """

    field: str
    dimension: Optional[str]  # thesis 維度 或 eps 分組標籤（如 "FY2026"）
    label: str
    direction: str
    prev_value: Optional[str]
    curr_value: Optional[str]
    pct_change: Optional[float]
    comparable: bool
    incomparable_reason: Optional[str] = None


@dataclass(frozen=True)
class Signal:
    id: str
    report_id: str
    market: str
    instrument_code: str
    broker: Optional[str]
    report_date: Optional[date]
    rating_raw: Optional[str]
    rating_normalized: str
    target_price: Optional[float]
    target_currency: Optional[str]
    target_horizon: Optional[str]
    target_price_evidence: Optional[str]
    eps: tuple[EpsEstimate, ...] = ()
    thesis: dict[str, DimensionStance] = field(default_factory=dict)
    extraction_status: str = "valid"
    file_name: Optional[str] = None


def _loads(value: object) -> object:
    """容錯 json.loads：接受 text（asyncpg ::text）或已解析物件；壞值 → None。"""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)  # numeric(Decimal) / int / str
    except (TypeError, ValueError):
        return None


def _parse_eps(raw: object) -> tuple[EpsEstimate, ...]:
    data = _loads(raw)
    if not isinstance(data, list):
        return ()
    out: list[EpsEstimate] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        fy = item.get("fiscal_year")
        out.append(
            EpsEstimate(
                fiscal_year=int(fy) if isinstance(fy, (int, float)) else None,
                period=item.get("period"),
                currency=item.get("currency"),
                unit=item.get("unit"),
                value=_to_float(item.get("value")),
                evidence=item.get("evidence"),
            )
        )
    return tuple(out)


def _parse_thesis(raw: object) -> dict[str, DimensionStance]:
    data = _loads(raw)
    if not isinstance(data, dict):
        return {}
    out: dict[str, DimensionStance] = {}
    for dim in THESIS_DIMENSIONS:
        cell = data.get(dim)
        if isinstance(cell, dict):
            out[dim] = DimensionStance(
                stance=cell.get("stance"),
                summary=cell.get("summary"),
                evidence=cell.get("evidence"),
            )
    return out


# queries.py 的 SELECT 欄位順序（parse_signal_row 依此位置解析，兩者必須一致）
SIGNAL_SELECT_COLUMNS = (
    "s.id::text, s.report_id::text, s.market, s.instrument_code, s.broker, "
    "s.report_date, s.rating_raw, s.rating_normalized, s.target_price, "
    "s.target_currency, s.target_horizon, s.target_price_evidence, "
    "s.eps_estimates::text, s.thesis_dimensions::text, s.extraction_status, "
    "r.file_name"
)


def parse_signal_row(row) -> Signal:
    """把 SIGNAL_SELECT_COLUMNS 順序的一列打包成 Signal（jsonb 已 ::text）。"""
    return Signal(
        id=row[0],
        report_id=row[1],
        market=row[2],
        instrument_code=row[3],
        broker=row[4],
        report_date=row[5],
        rating_raw=row[6],
        rating_normalized=row[7] or "unknown",
        target_price=_to_float(row[8]),
        target_currency=row[9],
        target_horizon=row[10],
        target_price_evidence=row[11],
        eps=_parse_eps(row[12]),
        thesis=_parse_thesis(row[13]),
        extraction_status=row[14] or "valid",
        file_name=row[15],
    )
