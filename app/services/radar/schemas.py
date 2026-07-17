"""觀點雷達 HTTP 回應 pydantic（API enum 契約；前端 zod 需逐字鏡像）。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Direction = Literal["up", "down", "flat", "incomparable", "none"]
RatingNorm = Literal["buy", "overweight", "neutral", "underweight", "sell", "unknown"]
DimKey = Literal["outlook", "catalyst", "risk", "valuation"]
DimLabel = Literal["strengthen", "weaken", "diverging", "stable", "insufficient"]
CoverageState = Literal["ok", "partial", "pending_extraction", "window_empty"]
Window = Literal["30", "90", "180", "all"]


class ReportLink(BaseModel):
    report_id: str
    file_name: Optional[str] = None
    report_date: Optional[str] = None
    broker: Optional[str] = None
    broker_display: Optional[str] = None


class ChangeItem(BaseModel):
    field: Literal["rating", "target_price", "eps", "thesis"]
    dimension: Optional[str] = None
    label: str
    direction: Direction
    prev_value: Optional[str] = None
    curr_value: Optional[str] = None
    pct_change: Optional[float] = None
    comparable: bool
    incomparable_reason: Optional[str] = None


class RatingBucketCount(BaseModel):
    rating: RatingNorm
    count: int


class RatingConsensus(BaseModel):
    distribution: list[RatingBucketCount]  # 五級細分布（不含 unknown）
    bullish: int
    neutral: int
    bearish: int
    unknown: int
    total_rated: int  # 計入分布家數（不含 unknown）
    median_rating: Optional[RatingNorm] = None  # 中位立場（加權中位，偶數跨級取較偏多者）
    upgrades: int
    downgrades: int
    unchanged: int


class TargetGroup(BaseModel):
    currency: str
    median: float
    q1: float
    q3: float
    low: float
    high: float
    count: int
    revision_pct: Optional[float] = None
    revision_direction: Direction = "none"


class TargetConsensus(BaseModel):
    primary_currency: Optional[str] = None
    groups: list[TargetGroup]
    note: Optional[str] = None


class EpsGroup(BaseModel):
    fiscal_year: Optional[int] = None
    period: Optional[str] = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    median: float
    count: int
    revision_pct: Optional[float] = None
    revision_direction: Direction = "none"


class EpsConsensus(BaseModel):
    primary: Optional[EpsGroup] = None
    groups: list[EpsGroup]


class ThesisDimension(BaseModel):
    dimension: DimKey
    dimension_display: str
    label: DimLabel
    label_display: str
    brokers_strengthen: int
    brokers_weaken: int
    brokers_comparable: int
    coverage_note: Optional[str] = None
    sample_summary: Optional[str] = None


class EventCard(BaseModel):
    broker: Optional[str] = None
    broker_display: Optional[str] = None
    report_date: str
    headline: str
    changes: list[ChangeItem]
    evidence: list[str]
    report_link: ReportLink


class BrokerSummary(BaseModel):
    broker: Optional[str] = None
    broker_display: Optional[str] = None
    latest_rating: RatingNorm
    latest_rating_raw: Optional[str] = None
    latest_target_price: Optional[float] = None
    latest_target_currency: Optional[str] = None
    latest_eps_value: Optional[float] = None
    latest_eps_fy: Optional[int] = None
    latest_report_date: str
    report_link: ReportLink
    recent_change_label: Optional[str] = None
    recent_change_direction: Direction = "none"
    stale: bool
    has_history: bool


class Coverage(BaseModel):
    state: CoverageState
    brokers_total: int
    brokers_extracted: int
    brokers_in_consensus: int
    reports_available: int
    note: str


class RadarOverviewResponse(BaseModel):
    market: str
    market_display: Optional[str] = None
    instrument_code: str
    instrument_name: Optional[str] = None
    window: Window
    as_of: Optional[str] = None
    coverage: Coverage
    rating: Optional[RatingConsensus] = None
    target_price: Optional[TargetConsensus] = None
    eps: Optional[EpsConsensus] = None
    thesis: list[ThesisDimension]  # 永遠 4 格
    recent_events: list[EventCard]
    recent_events_total: int
    brokers: list[BrokerSummary]
    notes: list[str]


# ── Endpoint B：單券商歷程 ──


class ThesisCell(BaseModel):
    dimension: DimKey
    dimension_display: str
    stance: Optional[str] = None
    summary: Optional[str] = None
    evidence: Optional[str] = None


class BrokerSnapshot(BaseModel):
    report_id: str
    report_date: str
    in_window: bool
    rating: RatingNorm
    rating_raw: Optional[str] = None
    target_price: Optional[float] = None
    target_currency: Optional[str] = None
    eps: list[EpsGroup]
    thesis: list[ThesisCell]
    extraction_status: str
    report_link: ReportLink


class SnapshotDiff(BaseModel):
    from_report_date: Optional[str] = None
    to_report_date: str
    changes: list[ChangeItem]
    has_prior_comparable: bool
    note: Optional[str] = None


class BrokerHistoryResponse(BaseModel):
    market: str
    instrument_code: str
    broker: Optional[str] = None
    broker_display: Optional[str] = None
    window: Window
    as_of: Optional[str] = None
    current_rating: RatingNorm
    report_count: int
    snapshots: list[BrokerSnapshot]
    diffs: list[SnapshotDiff]
    coverage_state: CoverageState


# ── Endpoint C：標的目錄（獨立頁選標的）──


class InstrumentStance(BaseModel):
    """卡片用精簡評等共識（overview RatingConsensus 的子集）。"""

    rating: RatingNorm  # 中位立場
    bullish: int
    neutral: int
    bearish: int
    total_rated: int
    distribution: list[RatingBucketCount]  # 五級（迷你分佈條）
    upgrades: int
    downgrades: int
    net_rating: int  # upgrades - downgrades


class InstrumentTargetBrief(BaseModel):
    """卡片用目標價中位（primary group）。"""

    currency: str
    median: float
    revision_pct: Optional[float] = None
    revision_direction: Direction = "none"


class InstrumentConsensus(BaseModel):
    """標的卡片的精簡共識預覽；無共識（尚未擷取/窗期空）時整體為 None。"""

    window: Window
    stance: InstrumentStance
    target: Optional[InstrumentTargetBrief] = None


class RadarInstrumentItem(BaseModel):
    market: str
    market_display: Optional[str] = None
    instrument_code: str
    instrument_name: Optional[str] = None
    broker_count: int
    report_count: int
    latest_report_date: Optional[str] = None
    coverage_state: CoverageState
    consensus: Optional[InstrumentConsensus] = None


class RadarInstrumentsResponse(BaseModel):
    total: int
    offset: int
    items: list[RadarInstrumentItem]
