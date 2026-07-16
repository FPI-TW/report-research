"""觀點雷達聚合編排（純 Python 決定性，讀取時不呼叫 LLM）。

build_overview：跨券商總覽（共識快照 + 四維論點 + 近期事件 + 券商清單）。
build_broker_history：單券商歷程（全歷程快照 + 相鄰差異，延遲載入）。
共識一律取「每家券商窗期內最新有效訊號」，不把同券商舊報告累積成共識。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from app.services.filename import source_display
from app.services.radar import scale
from app.services.radar.queries import CoverageCounts
from app.services.radar.schemas import (
    BrokerHistoryResponse,
    BrokerSnapshot,
    BrokerSummary,
    ChangeItem,
    Coverage,
    EpsConsensus,
    EpsGroup,
    EventCard,
    RadarOverviewResponse,
    RatingBucketCount,
    RatingConsensus,
    ReportLink,
    SnapshotDiff,
    TargetConsensus,
    TargetGroup,
    ThesisCell,
    ThesisDimension,
)
from app.services.radar.scale import DIM_DISPLAY, DIMLABEL_DISPLAY
from app.services.radar.types import Change, Signal
from app.services.signal_extract import THESIS_DIMENSIONS
from app.services.tagging import MARKET_DISPLAY

WINDOW_DAYS = {"30": 30, "90": 90, "180": 180, "all": None}
FIVE_LEVELS = ("buy", "overweight", "neutral", "underweight", "sell")
MAX_EVENTS = 12


def _broker_display(b: Optional[str]) -> Optional[str]:
    return source_display(b) if b else None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _window_start(as_of: Optional[date], window: str) -> Optional[date]:
    days = WINDOW_DAYS.get(window)
    if days is None or as_of is None:
        return None
    return as_of - timedelta(days=days)


def _in_window(s: Signal, ws: Optional[date]) -> bool:
    if ws is None:
        return True
    return s.report_date is not None and s.report_date >= ws


def _report_link(s: Signal) -> ReportLink:
    return ReportLink(
        report_id=s.report_id, file_name=s.file_name, report_date=_iso(s.report_date),
        broker=s.broker, broker_display=_broker_display(s.broker),
    )


def _by_broker(signals: list[Signal]) -> dict[Optional[str], list[Signal]]:
    d: dict[Optional[str], list[Signal]] = defaultdict(list)
    for s in signals:
        d[s.broker].append(s)
    for lst in d.values():
        lst.sort(key=lambda s: (s.report_date or date.min, s.id), reverse=True)
    return dict(d)


def _change_item(c: Change) -> ChangeItem:
    return ChangeItem(
        field=c.field, dimension=c.dimension, label=c.label, direction=c.direction,
        prev_value=c.prev_value, curr_value=c.curr_value, pct_change=c.pct_change,
        comparable=c.comparable, incomparable_reason=c.incomparable_reason,
    )


def _headline(material: list[Change]) -> str:
    order = {"rating": 0, "eps": 1, "target_price": 2, "thesis": 3}
    c = min(material, key=lambda x: order.get(x.field, 9))
    pct = f"{abs(c.pct_change):.1f}%" if c.pct_change is not None else ""
    if c.field == "rating":
        verb = {"up": "上調", "down": "下調"}.get(c.direction, "調整")
        return f"{verb}評等：{c.label}"
    if c.field == "target_price":
        verb = {"up": "上修", "down": "下修"}.get(c.direction, "調整")
        return f"目標價{verb} {pct}".strip()
    if c.field == "eps":
        verb = {"up": "上修", "down": "下修"}.get(c.direction, "調整")
        return f"{c.label} {verb} {pct}".strip()
    verb = {"up": "轉強", "down": "轉弱"}.get(c.direction, "調整")
    return f"{c.label}論點{verb}"


def _evidence_for(s: Signal, material: list[Change]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in material:
        text: Optional[str] = None
        if c.field == "target_price":
            text = s.target_price_evidence
        elif c.field == "thesis" and c.dimension:
            cell = s.thesis.get(c.dimension)
            text = cell.evidence if cell else None
        elif c.field == "eps":
            text = next((e.evidence for e in s.eps if e.evidence), None)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out[:2]


# ── 共識子聚合 ──

def _consensus_set(by_broker, ws) -> dict[Optional[str], Signal]:
    """每家券商窗期內最新有效訊號（窗期外的券商不計入共識）。"""
    out: dict[Optional[str], Signal] = {}
    for broker, lst in by_broker.items():
        latest = next((s for s in lst if _in_window(s, ws)), None)
        if latest is not None:
            out[broker] = latest
    return out


def _prev_signal(by_broker, s: Signal) -> Optional[Signal]:
    lst = by_broker[s.broker]
    idx = lst.index(s)
    return lst[idx + 1] if idx + 1 < len(lst) else None


def _rating_consensus(consensus, by_broker) -> RatingConsensus:
    dist = {lvl: 0 for lvl in FIVE_LEVELS}
    bullish = neutral = bearish = unknown = 0
    for s in consensus.values():
        r = s.rating_normalized
        if r == "unknown":
            unknown += 1
            continue
        if r in dist:
            dist[r] += 1
        bucket = scale.rating_bucket(r)
        if bucket == "bullish":
            bullish += 1
        elif bucket == "bearish":
            bearish += 1
        elif bucket == "neutral":
            neutral += 1
    up = down = flat = 0
    for s in consensus.values():
        prev = _prev_signal(by_broker, s)
        if prev is None:
            continue
        d = scale.rating_direction(prev.rating_normalized, s.rating_normalized)
        if d == "up":
            up += 1
        elif d == "down":
            down += 1
        elif d == "flat":
            flat += 1
    return RatingConsensus(
        distribution=[RatingBucketCount(rating=lvl, count=dist[lvl]) for lvl in FIVE_LEVELS],
        bullish=bullish, neutral=neutral, bearish=bearish, unknown=unknown,
        total_rated=sum(dist.values()), upgrades=up, downgrades=down, unchanged=flat,
    )


def _target_consensus(consensus, by_broker) -> Optional[TargetConsensus]:
    values: dict[str, list[float]] = defaultdict(list)
    pairs: dict[str, list[tuple]] = defaultdict(list)
    for s in consensus.values():
        if s.target_price is None or not s.target_currency:
            continue
        cur = s.target_currency
        values[cur].append(s.target_price)
        lst = by_broker[s.broker]
        idx = lst.index(s)
        prev = next(
            (p for p in lst[idx + 1:]
             if p.target_price is not None and p.target_currency == cur),
            None,
        )
        if prev is not None:
            pairs[cur].append((prev.target_price, s.target_price))
    if not values:
        return None
    groups: list[TargetGroup] = []
    for cur, vals in values.items():
        q = scale.quantiles(vals)
        rp, rd = scale.median_pct(pairs.get(cur, []))
        groups.append(TargetGroup(
            currency=cur, median=q.median, q1=q.q1, q3=q.q3, low=q.low, high=q.high,
            count=q.count, revision_pct=rp, revision_direction=rd,
        ))
    groups.sort(key=lambda g: (-g.count, g.currency))
    primary = groups[0].currency
    note = None
    if len(groups) > 1:
        others = "、".join(f"{g.count} 家 {g.currency}" for g in groups[1:])
        note = f"另有 {others} 計價，未併入 {primary} 中位數"
    return TargetConsensus(primary_currency=primary, groups=groups, note=note)


def _eps_consensus(consensus, by_broker) -> Optional[EpsConsensus]:
    values: dict[tuple, list[float]] = defaultdict(list)
    pairs: dict[tuple, list[tuple]] = defaultdict(list)
    for s in consensus.values():
        prev = _prev_signal(by_broker, s)
        prev_eps = {e.group_key(): e for e in prev.eps} if prev else {}
        for e in s.eps:
            if e.value is None:
                continue
            key = e.group_key()
            values[key].append(e.value)
            pe = prev_eps.get(key)
            if pe is not None and pe.value is not None:
                pairs[key].append((pe.value, e.value))
    if not values:
        return None
    groups: list[EpsGroup] = []
    for key, vals in values.items():
        fy, period, currency, unit = key
        q = scale.quantiles(vals)
        rp, rd = scale.median_pct(pairs.get(key, []))
        groups.append(EpsGroup(
            fiscal_year=fy, period=period, currency=currency, unit=unit,
            median=q.median, count=q.count, revision_pct=rp, revision_direction=rd,
        ))
    groups.sort(key=lambda g: (-g.count, g.fiscal_year or 0))
    return EpsConsensus(primary=groups[0], groups=groups)


def _dim_sample_summary(consensus, dim: str) -> Optional[str]:
    best: Optional[tuple[date, str]] = None
    for s in consensus.values():
        cell = s.thesis.get(dim)
        if cell and cell.summary:
            d = s.report_date or date.min
            if best is None or d > best[0]:
                best = (d, cell.summary)
    return best[1] if best else None


def _thesis_dimensions(consensus, by_broker) -> list[ThesisDimension]:
    out: list[ThesisDimension] = []
    for dim in THESIS_DIMENSIONS:
        up = down = flat = n = 0
        for s in consensus.values():
            cell = s.thesis.get(dim)
            if not cell:
                continue
            cs = scale.stance_constructiveness(dim, cell.stance)
            if cs is None:
                continue
            lst = by_broker[s.broker]
            idx = lst.index(s)
            prev_c: Optional[int] = None
            for p in lst[idx + 1:]:
                pc = p.thesis.get(dim)
                if pc is not None:
                    val = scale.stance_constructiveness(dim, pc.stance)
                    if val is not None:
                        prev_c = val
                        break
            if prev_c is None:
                continue
            n += 1
            if cs > prev_c:
                up += 1
            elif cs < prev_c:
                down += 1
            else:
                flat += 1
        label = scale.classify_dimension(up, down, flat, n)
        note = None
        if n:
            note = (f"{down}/{n} 家提高風險比重" if dim == "risk"
                    else f"{up}/{n} 家出現上修訊號")
        out.append(ThesisDimension(
            dimension=dim, dimension_display=DIM_DISPLAY[dim],
            label=label, label_display=DIMLABEL_DISPLAY[label],
            brokers_strengthen=up, brokers_weaken=down, brokers_comparable=n,
            coverage_note=note, sample_summary=_dim_sample_summary(consensus, dim),
        ))
    return out


def _events(by_broker, ws) -> tuple[list[EventCard], int]:
    events: list[EventCard] = []
    for broker, lst in by_broker.items():
        for i, s in enumerate(lst):
            if s.report_date is None or not _in_window(s, ws):
                continue
            prev = lst[i + 1] if i + 1 < len(lst) else None
            changes = scale.diff_signals(prev, s)
            material = [c for c in changes if scale.is_material(c)]
            if not material:
                continue
            events.append(EventCard(
                broker=broker, broker_display=_broker_display(broker),
                report_date=_iso(s.report_date) or "", headline=_headline(material),
                changes=[_change_item(c) for c in changes],
                evidence=_evidence_for(s, material), report_link=_report_link(s),
            ))
    events.sort(key=lambda e: e.report_date, reverse=True)
    return events[:MAX_EVENTS], len(events)


def _broker_summaries(by_broker, ws) -> list[BrokerSummary]:
    out: list[BrokerSummary] = []
    for broker, lst in by_broker.items():
        s = lst[0]
        prev = lst[1] if len(lst) > 1 else None
        material = [c for c in scale.diff_signals(prev, s) if scale.is_material(c)]
        top = material[0] if material else None
        latest_eps = s.eps[0] if s.eps else None
        out.append(BrokerSummary(
            broker=broker, broker_display=_broker_display(broker),
            latest_rating=s.rating_normalized, latest_rating_raw=s.rating_raw,
            latest_target_price=s.target_price, latest_target_currency=s.target_currency,
            latest_eps_value=latest_eps.value if latest_eps else None,
            latest_eps_fy=latest_eps.fiscal_year if latest_eps else None,
            latest_report_date=_iso(s.report_date) or "", report_link=_report_link(s),
            recent_change_label=_headline([top]) if top else None,
            recent_change_direction=top.direction if top else "none",
            stale=not _in_window(s, ws), has_history=len(lst) > 1,
        ))
    out.sort(key=lambda b: b.latest_report_date, reverse=True)
    return out


def _coverage_state(coverage: CoverageCounts, signals, consensus) -> str:
    if not signals:
        return "pending_extraction"
    if not consensus:
        return "window_empty"
    if coverage.brokers_extracted < coverage.brokers_total:
        return "partial"
    return "ok"


def build_overview(
    signals: list[Signal], coverage: CoverageCounts, *, window: str
) -> RadarOverviewResponse:
    market = coverage.market or (signals[0].market if signals else "")
    as_of = max((s.report_date for s in signals if s.report_date), default=None)
    ws = _window_start(as_of, window)
    by_broker = _by_broker(signals)
    consensus = _consensus_set(by_broker, ws)

    rating = _rating_consensus(consensus, by_broker) if consensus else None
    target = _target_consensus(consensus, by_broker) if consensus else None
    eps = _eps_consensus(consensus, by_broker) if consensus else None
    thesis = _thesis_dimensions(consensus, by_broker)  # 永遠 4 格
    events, events_total = _events(by_broker, ws)
    brokers = _broker_summaries(by_broker, ws)

    notes: list[str] = []
    if target and target.note:
        notes.append(target.note)
    if eps and len(eps.groups) > 1:
        notes.append("EPS 依會計年度/幣別分列，僅同組可比較")

    state = _coverage_state(coverage, signals, consensus)
    cov = Coverage(
        state=state, brokers_total=coverage.brokers_total,
        brokers_extracted=coverage.brokers_extracted, brokers_in_consensus=len(consensus),
        reports_available=coverage.reports_available,
        note=f"{coverage.brokers_extracted}/{coverage.brokers_total} 家券商已擷取",
    )
    return RadarOverviewResponse(
        market=market, market_display=MARKET_DISPLAY.get(market),
        instrument_code=coverage.instrument_code, instrument_name=coverage.instrument_name,
        window=window, as_of=_iso(as_of), coverage=cov,
        rating=rating, target_price=target, eps=eps, thesis=thesis,
        recent_events=events, recent_events_total=events_total, brokers=brokers, notes=notes,
    )


def _eps_group_single(e) -> EpsGroup:
    return EpsGroup(
        fiscal_year=e.fiscal_year, period=e.period, currency=e.currency, unit=e.unit,
        median=e.value if e.value is not None else 0.0, count=1,
    )


def _thesis_cell(dim: str, cell) -> ThesisCell:
    return ThesisCell(
        dimension=dim, dimension_display=DIM_DISPLAY[dim],
        stance=cell.stance if cell else None, summary=cell.summary if cell else None,
        evidence=cell.evidence if cell else None,
    )


def build_broker_history(
    signals: list[Signal], *, market: str, code: str, broker: str, window: str,
    coverage_state: str = "ok",
) -> BrokerHistoryResponse:
    signals = sorted(signals, key=lambda s: (s.report_date or date.min, s.id), reverse=True)
    as_of = max((s.report_date for s in signals if s.report_date), default=None)
    ws = _window_start(as_of, window)

    snapshots: list[BrokerSnapshot] = []
    for s in signals:
        snapshots.append(BrokerSnapshot(
            report_id=s.report_id, report_date=_iso(s.report_date) or "",
            in_window=_in_window(s, ws), rating=s.rating_normalized, rating_raw=s.rating_raw,
            target_price=s.target_price, target_currency=s.target_currency,
            eps=[_eps_group_single(e) for e in s.eps if e.value is not None],
            thesis=[_thesis_cell(dim, s.thesis.get(dim)) for dim in THESIS_DIMENSIONS],
            extraction_status=s.extraction_status, report_link=_report_link(s),
        ))

    diffs: list[SnapshotDiff] = []
    for i, s in enumerate(signals):
        prev = signals[i + 1] if i + 1 < len(signals) else None
        diffs.append(SnapshotDiff(
            from_report_date=_iso(prev.report_date) if prev else None,
            to_report_date=_iso(s.report_date) or "",
            changes=[_change_item(c) for c in scale.diff_signals(prev, s)],
            has_prior_comparable=prev is not None,
            note=None if prev else "此窗期內沒有前次可比較研報",
        ))

    return BrokerHistoryResponse(
        market=market, instrument_code=code, broker=broker,
        broker_display=_broker_display(broker), window=window, as_of=_iso(as_of),
        current_rating=signals[0].rating_normalized if signals else "unknown",
        report_count=len(signals), snapshots=snapshots, diffs=diffs,
        coverage_state=coverage_state,
    )
