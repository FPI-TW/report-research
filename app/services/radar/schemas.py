"""觀點雷達 HTTP 回應 pydantic（API enum 契約；前端 zod 需逐字鏡像）。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Direction = Literal["up", "down", "flat", "incomparable", "none"]
RatingNorm = Literal["buy", "overweight", "neutral", "underweight", "sell", "unknown"]
DimKey = Literal["outlook", "catalyst", "risk", "valuation"]
DimLabel = Literal["strengthen", "weaken", "diverging", "stable", "insufficient"]
CoverageState = Literal["ok", "partial", "pending_extraction", "window_empty"]
Market = Literal["TW", "US", "HK", "CN", "FX", "WTX", "MACRO", "GLOBAL", "CRYPTO"]
Window = Literal["30", "90", "180", "all"]
# 目錄排序鍵。值要與 queries.CATALOG_SORTS 的鍵**逐字相同**——那份 dict 才是真正進 SQL
# 的白名單，這裡只是把它抬到 API 契約層讓 FastAPI 先擋一次。
CatalogSort = Literal["latest", "reports", "brokers", "code"]
# 立場三桶。值取自 radar.scale.RATING_BUCKET 的輸出（bullish/neutral/bearish），
# 刻意不另造一套簡寫——同一個概念在後端出現兩種拼法遲早會對不起來。
StanceFilter = Literal["bullish", "neutral", "bearish"]


class ApiErrorResponse(BaseModel):
    detail: str


class ReportLink(BaseModel):
    report_id: str
    file_name: Optional[str] = None
    title: Optional[str] = None  # 報告內部標題（顯示用）；None＝前端回退 file_name
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
    reason_code: Optional[str] = None
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
    latest_eps_period: Optional[str] = None
    latest_eps_currency: Optional[str] = None
    latest_eps_unit: Optional[str] = None
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
    market: Market
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
    recent_events_has_more: bool = False
    recent_events_next_offset: Optional[int] = None
    brokers: list[BrokerSummary]
    notes: list[str]


# ── Endpoint B：完整事件分頁 ──


class RadarEventsResponse(BaseModel):
    market: Market
    instrument_code: str
    window: Window
    as_of: Optional[str]
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: Optional[int]
    items: list[EventCard]


# ── Endpoint C：單券商歷程 ──


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
    primary_eps: Optional[EpsGroup] = None
    thesis: list[ThesisCell]
    extraction_status: str
    report_link: ReportLink


class SnapshotDiff(BaseModel):
    from_report_id: Optional[str] = None
    from_report_date: Optional[str] = None
    to_report_date: str
    changes: list[ChangeItem]
    has_prior_report: bool = False
    has_prior_comparable: bool
    note: Optional[str] = None


class BrokerHistoryResponse(BaseModel):
    market: Market
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


# ── Endpoint D：標的目錄（獨立頁選標的）──


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
    """清單頁的目標價摘要：**只有方向，沒有任何數值**。

    先前是 `currency` ＋ `median` ＋ `revision_pct`。清單頁不呈現目標價的聚合
    （中位數／均值／區間／幅度），唯一的出口是連到總覽的各家目標價點圖——而
    「前端不 render」不是防線：留在 payload 裡的數字，下一個人只要三行就能畫回去，
    而那三行不會有任何測試看得到。整檔標的**沒有任何一家給目標價**時，
    `InstrumentConsensus.target` 為 None（不是這個型別的某個空值）。

    總覽路徑（`build_overview` → `TargetConsensus`）不受影響：那一頁本來就在講目標價。
    """

    revision_direction: Direction = "none"


class InstrumentConsensus(BaseModel):
    """標的卡片的精簡共識預覽；無共識（尚未擷取/窗期空）時整體為 None。"""

    window: Window
    stance: InstrumentStance
    target: Optional[InstrumentTargetBrief] = None


class RadarInstrumentItem(BaseModel):
    market: Market
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
    limit: int = 50
    offset: int
    has_more: bool = False
    next_offset: Optional[int] = None
    items: list[RadarInstrumentItem]
    # 各市場筆數（受 q／stance 影響，不受 market 影響）。前端市場膠囊上的數字只能來自這裡
    # ——沒有出現在這個 dict 裡的市場就是「這個篩選下沒有標的」，前端據此只印市場名稱。
    facets: dict[str, int] = {}
    # 整個篩選結果的最新研報日期（不是本頁的）。報頭「最新更新」用它。
    latest_report_date: Optional[str] = None
