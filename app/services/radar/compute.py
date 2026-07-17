"""觀點雷達聚合編排（純 Python 決定性，讀取時不呼叫 LLM）。

build_overview：跨券商總覽（共識快照 + 四維論點 + 近期事件 + 券商清單）。
build_broker_history：單券商歷程（全歷程快照 + 相鄰差異，延遲載入）。
共識一律取「每家券商窗期內最新有效訊號」，不把同券商舊報告累積成共識。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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
    InstrumentConsensus,
    InstrumentStance,
    InstrumentTargetBrief,
    RadarEventsResponse,
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
OVERVIEW_EVENTS_LIMIT = 3
HEADLINE_FIELD_ORDER = {"rating": 0, "eps": 1, "target_price": 2, "thesis": 3}


def _broker_display(b: Optional[str]) -> Optional[str]:
    return source_display(b) if b else None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _window_start(as_of: Optional[date], window: str) -> Optional[date]:
    days = WINDOW_DAYS.get(window)
    if days is None or as_of is None:
        return None
    return as_of - timedelta(days=days)


def _in_window(s: Signal, ws: Optional[date], window: str) -> bool:
    if window == "all":
        return True
    return s.report_date is not None and ws is not None and s.report_date >= ws


def _created_at_key(s: Signal) -> datetime:
    value = getattr(s, "created_at", None)
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _signal_sort_key(s: Signal) -> tuple:
    return (s.report_date or date.min, _created_at_key(s), s.id)


def _broker_key(s: Signal) -> Optional[str]:
    if not isinstance(s.broker, str):
        return None
    broker = s.broker.strip()
    return broker or None


def _report_link(s: Signal) -> ReportLink:
    return ReportLink(
        report_id=s.report_id, file_name=s.file_name, report_date=_iso(s.report_date),
        broker=s.broker, broker_display=_broker_display(s.broker),
    )


def _by_broker(signals: list[Signal]) -> dict[str, list[Signal]]:
    d: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        broker = _broker_key(s)
        if broker is not None:
            d[broker].append(s)
    for lst in d.values():
        lst.sort(key=_signal_sort_key, reverse=True)
    return dict(d)


def _attributed_as_of(by_broker: dict[str, list[Signal]]) -> Optional[date]:
    return max(
        (
            signal.report_date
            for broker_signals in by_broker.values()
            for signal in broker_signals
            if signal.report_date is not None
        ),
        default=None,
    )


def _change_item(c: Change) -> ChangeItem:
    return ChangeItem(
        field=c.field, dimension=c.dimension, label=c.label, direction=c.direction,
        prev_value=c.prev_value, curr_value=c.curr_value, pct_change=c.pct_change,
        comparable=c.comparable, reason_code=c.reason_code,
        incomparable_reason=c.incomparable_reason,
    )


def _headline_change(material: list[Change]) -> Change:
    return min(material, key=lambda change: HEADLINE_FIELD_ORDER.get(change.field, 9))


def _headline(material: list[Change]) -> str:
    c = _headline_change(material)
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


def _evidence_for_change(s: Signal, change: Change) -> Optional[str]:
    if change.field == "target_price":
        return s.target_price_evidence
    if change.field == "thesis" and change.dimension:
        cell = s.thesis.get(change.dimension)
        return cell.evidence if cell else None
    if change.field == "eps" and change.eps_group_identity:
        return next(
            (
                estimate.evidence
                for estimate in s.eps
                if estimate.evidence
                and scale.eps_group_identity(estimate.group_key())
                == change.eps_group_identity
            ),
            None,
        )
    return None


def _evidence_for(s: Signal, material: list[Change]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if not material:
        return out
    headline_change = _headline_change(material)
    ordered_changes = [
        headline_change,
        *(change for change in material if change is not headline_change),
    ]
    for c in ordered_changes:
        text = _evidence_for_change(s, c)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out[:2]


# ── 共識子聚合 ──

def _consensus_set(by_broker, ws, window: str) -> dict[str, Signal]:
    """每家券商窗期內最新有效訊號（窗期外的券商不計入共識）。"""
    out: dict[Optional[str], Signal] = {}
    for broker, lst in by_broker.items():
        latest = next((s for s in lst if _in_window(s, ws, window)), None)
        if latest is not None:
            out[broker] = latest
    return out


def _prev_signal(by_broker, s: Signal) -> Optional[Signal]:
    lst = by_broker[_broker_key(s)]
    idx = lst.index(s)
    return lst[idx + 1] if idx + 1 < len(lst) else None


def _rating_movement_counts(by_broker, ws, window: str) -> tuple[int, int, int]:
    up = down = flat = 0
    for signals in by_broker.values():
        comparable = [
            signal
            for signal in signals
            if scale.rating_scale(signal.rating_normalized) is not None
        ]
        in_window = [
            signal
            for signal in comparable
            if _in_window(signal, ws, window)
        ]
        if not in_window:
            continue
        current = in_window[0]
        if window == "all":
            baseline = in_window[-1]
        else:
            baseline = next(
                (
                    signal
                    for signal in comparable
                    if ws is not None
                    and signal.report_date is not None
                    and signal.report_date < ws
                ),
                None,
            )
            if baseline is None:
                baseline = in_window[-1]
        direction = scale.rating_direction(
            baseline.rating_normalized, current.rating_normalized
        )
        if direction == "up":
            up += 1
        elif direction == "down":
            down += 1
        elif direction == "flat":
            flat += 1
    return up, down, flat


def _rating_consensus(consensus, by_broker, ws, window: str) -> RatingConsensus:
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
    up, down, flat = _rating_movement_counts(by_broker, ws, window)
    return RatingConsensus(
        distribution=[RatingBucketCount(rating=lvl, count=dist[lvl]) for lvl in FIVE_LEVELS],
        bullish=bullish, neutral=neutral, bearish=bearish, unknown=unknown,
        total_rated=sum(dist.values()),
        median_rating=scale.median_rating([(lvl, dist[lvl]) for lvl in FIVE_LEVELS]),
        upgrades=up, downgrades=down, unchanged=flat,
    )


def _target_consensus(consensus, by_broker) -> Optional[TargetConsensus]:
    values: dict[str, list[float]] = defaultdict(list)
    pairs: dict[str, list[tuple]] = defaultdict(list)
    for s in consensus.values():
        if s.target_price is None or not s.target_currency:
            continue
        cur = s.target_currency
        values[cur].append(s.target_price)
        lst = by_broker[_broker_key(s)]
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


def _eps_complete_key(group: EpsGroup) -> tuple[str, str, str]:
    return (
        group.period or "",
        group.currency or "",
        group.unit or "",
    )


def _select_primary_eps(
    groups: list[EpsGroup], as_of: Optional[date]
) -> Optional[EpsGroup]:
    """依 broker count、年度群組、基準年與完整 key 選出 primary EPS。"""
    if not groups:
        return None
    max_count = max(group.count for group in groups)
    candidates = [group for group in groups if group.count == max_count]
    annual_groups = [
        group for group in candidates if (group.period or "").upper() == "FY"
    ]
    if annual_groups:
        candidates = annual_groups
    fy_groups = [group for group in candidates if group.fiscal_year is not None]
    if fy_groups:
        candidates = fy_groups
        reference_year = as_of.year if as_of is not None else None
        if reference_year is None:
            selected_year = max(group.fiscal_year for group in candidates)
        else:
            unexpired_years = [
                group.fiscal_year
                for group in candidates
                if group.fiscal_year >= reference_year
            ]
            selected_year = (
                min(unexpired_years)
                if unexpired_years
                else max(group.fiscal_year for group in candidates)
            )
        candidates = [
            group for group in candidates if group.fiscal_year == selected_year
        ]
    return min(candidates, key=_eps_complete_key)


def _eps_consensus(consensus, by_broker, as_of: Optional[date]) -> Optional[EpsConsensus]:
    values: dict[tuple, list[float]] = defaultdict(list)
    pairs: dict[tuple, list[tuple]] = defaultdict(list)
    for s in consensus.values():
        prev = _prev_signal(by_broker, s)
        prev_eps = {}
        if prev is not None:
            for estimate in sorted(
                (e for e in prev.eps if e.value is not None),
                key=lambda e: (
                    scale.eps_group_label(e.group_key()), e.value, e.evidence or ""
                ),
            ):
                prev_eps.setdefault(estimate.group_key(), estimate)
        current_eps = {}
        for estimate in sorted(
            (e for e in s.eps if e.value is not None),
            key=lambda e: (
                scale.eps_group_label(e.group_key()), e.value, e.evidence or ""
            ),
        ):
            current_eps.setdefault(estimate.group_key(), estimate)
        for key, e in current_eps.items():
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
    groups.sort(
        key=lambda group: (
            group.fiscal_year is None,
            group.fiscal_year or 0,
            *_eps_complete_key(group),
        )
    )
    return EpsConsensus(primary=_select_primary_eps(groups, as_of), groups=groups)


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
            lst = by_broker[_broker_key(s)]
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


def _all_events(by_broker, ws, window: str) -> list[EventCard]:
    events: list[EventCard] = []
    for broker, lst in by_broker.items():
        for i, s in enumerate(lst):
            if s.report_date is None or not _in_window(s, ws, window):
                continue
            prev = lst[i + 1] if i + 1 < len(lst) else None
            changes = scale.diff_signals(prev, s)
            material = [c for c in changes if scale.is_material(c)]
            if not material:
                continue
            evidenced_material = [
                change
                for change in material
                if _evidence_for_change(s, change)
            ]
            evidence = _evidence_for(s, evidenced_material)
            if not evidence:
                continue
            events.append(EventCard(
                broker=broker, broker_display=_broker_display(broker),
                report_date=_iso(s.report_date) or "",
                headline=_headline(evidenced_material),
                changes=[_change_item(c) for c in evidenced_material],
                evidence=evidence, report_link=_report_link(s),
            ))
    # 同日事件的次序不可依賴 SQL 或輸入 list 的偶然順序，否則 offset 分頁會重複或漏項。
    events.sort(key=lambda event: (event.broker or "", event.report_link.report_id))
    events.sort(key=lambda event: event.report_date, reverse=True)
    return events


def build_events_page(
    signals: list[Signal], *, market: str, code: str, window: str,
    limit: int, offset: int,
) -> RadarEventsResponse:
    """以 overview 同一事件來源建立穩定、完整的 offset 分頁。"""
    by_broker = _by_broker(signals)
    as_of = _attributed_as_of(by_broker)
    ws = _window_start(as_of, window)
    events = _all_events(by_broker, ws, window)
    items = events[offset:offset + limit]
    total = len(events)
    next_offset = offset + len(items)
    has_more = next_offset < total
    return RadarEventsResponse(
        market=market,
        instrument_code=code,
        window=window,
        as_of=_iso(as_of),
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
        items=items,
    )


def _broker_summaries(by_broker, ws, window: str) -> list[BrokerSummary]:
    out: list[BrokerSummary] = []
    for broker, lst in by_broker.items():
        s = lst[0]
        prev = lst[1] if len(lst) > 1 else None
        material = [c for c in scale.diff_signals(prev, s) if scale.is_material(c)]
        top = material[0] if material else None
        eps_groups = _eps_groups_for_signal(s)
        latest_eps = _select_primary_eps(eps_groups, s.report_date)
        out.append(BrokerSummary(
            broker=broker, broker_display=_broker_display(broker),
            latest_rating=s.rating_normalized, latest_rating_raw=s.rating_raw,
            latest_target_price=s.target_price, latest_target_currency=s.target_currency,
            latest_eps_value=latest_eps.median if latest_eps else None,
            latest_eps_fy=latest_eps.fiscal_year if latest_eps else None,
            latest_eps_period=latest_eps.period if latest_eps else None,
            latest_eps_currency=latest_eps.currency if latest_eps else None,
            latest_eps_unit=latest_eps.unit if latest_eps else None,
            latest_report_date=_iso(s.report_date) or "", report_link=_report_link(s),
            recent_change_label=_headline([top]) if top else None,
            recent_change_direction=top.direction if top else "none",
            stale=not _in_window(s, ws, window), has_history=len(lst) > 1,
        ))
    out.sort(key=lambda b: b.latest_report_date, reverse=True)
    return out


def _coverage_state(coverage: CoverageCounts, signals, consensus) -> str:
    if not signals:
        return "pending_extraction"
    if any(signal.extraction_status == "partial" for signal in signals):
        return "partial"
    if not consensus:
        return "window_empty"
    if coverage.brokers_extracted < coverage.brokers_total:
        return "partial"
    return "ok"


def build_overview(
    signals: list[Signal], coverage: CoverageCounts, *, window: str
) -> RadarOverviewResponse:
    market = coverage.market or (signals[0].market if signals else "")
    by_broker = _by_broker(signals)
    as_of = _attributed_as_of(by_broker)
    ws = _window_start(as_of, window)
    consensus = _consensus_set(by_broker, ws, window)

    rating = _rating_consensus(consensus, by_broker, ws, window) if consensus else None
    target = _target_consensus(consensus, by_broker) if consensus else None
    eps = _eps_consensus(consensus, by_broker, as_of) if consensus else None
    thesis = _thesis_dimensions(consensus, by_broker)  # 永遠 4 格
    all_events = _all_events(by_broker, ws, window)
    events_total = len(all_events)
    events = all_events[:OVERVIEW_EVENTS_LIMIT]
    events_has_more = events_total > len(events)
    brokers = _broker_summaries(by_broker, ws, window)

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
        recent_events=events, recent_events_total=events_total,
        recent_events_has_more=events_has_more,
        recent_events_next_offset=len(events) if events_has_more else None,
        brokers=brokers, notes=notes,
    )


def build_instrument_slim(
    signals: list[Signal], *, window: str = "90"
) -> Optional[InstrumentConsensus]:
    """標的卡片用精簡共識（overview 子集）：中位立場 + 五級分佈 + 淨變動 + 目標價中位。

    重用 build_overview 的共識子聚合，只保留卡片所需欄位。無共識（尚未擷取/窗期空/
    全 unknown）→ None，卡片走淡態。窗期預設 90 天，以各標的自身 as_of 起算。
    """
    if not signals:
        return None
    by_broker = _by_broker(signals)
    as_of = _attributed_as_of(by_broker)
    ws = _window_start(as_of, window)
    consensus = _consensus_set(by_broker, ws, window)
    if not consensus:
        return None
    rc = _rating_consensus(consensus, by_broker, ws, window)
    if rc.total_rated == 0 or rc.median_rating is None:
        return None
    tc = _target_consensus(consensus, by_broker)
    target: Optional[InstrumentTargetBrief] = None
    if tc and tc.groups:
        primary = next(
            (g for g in tc.groups if g.currency == tc.primary_currency), tc.groups[0]
        )
        target = InstrumentTargetBrief(
            currency=primary.currency, median=primary.median,
            revision_pct=primary.revision_pct, revision_direction=primary.revision_direction,
        )
    stance = InstrumentStance(
        rating=rc.median_rating, bullish=rc.bullish, neutral=rc.neutral, bearish=rc.bearish,
        total_rated=rc.total_rated, distribution=rc.distribution,
        upgrades=rc.upgrades, downgrades=rc.downgrades,
        net_rating=rc.upgrades - rc.downgrades,
    )
    return InstrumentConsensus(window=window, stance=stance, target=target)


def _eps_groups_for_signal(signal: Signal) -> list[EpsGroup]:
    values: dict[tuple, list[float]] = defaultdict(list)
    for estimate in signal.eps:
        if estimate.value is not None:
            values[estimate.group_key()].append(estimate.value)
    groups: list[EpsGroup] = []
    for key in sorted(values, key=scale.eps_group_identity):
        fiscal_year, period, currency, unit = key
        quartiles = scale.quantiles(values[key])
        groups.append(EpsGroup(
            fiscal_year=fiscal_year,
            period=period,
            currency=currency,
            unit=unit,
            median=quartiles.median,
            count=1,
        ))
    return groups


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
    signals = sorted(signals, key=_signal_sort_key, reverse=True)
    as_of = max((s.report_date for s in signals if s.report_date), default=None)
    ws = _window_start(as_of, window)

    snapshots: list[BrokerSnapshot] = []
    for s in signals:
        eps_groups = _eps_groups_for_signal(s)
        snapshots.append(BrokerSnapshot(
            report_id=s.report_id, report_date=_iso(s.report_date) or "",
            in_window=_in_window(s, ws, window), rating=s.rating_normalized,
            rating_raw=s.rating_raw,
            target_price=s.target_price, target_currency=s.target_currency,
            eps=eps_groups,
            primary_eps=_select_primary_eps(eps_groups, s.report_date),
            thesis=[_thesis_cell(dim, s.thesis.get(dim)) for dim in THESIS_DIMENSIONS],
            extraction_status=s.extraction_status, report_link=_report_link(s),
        ))

    if coverage_state == "ok":
        if not signals:
            coverage_state = "pending_extraction"
        elif not any(snapshot.in_window for snapshot in snapshots):
            coverage_state = "window_empty"
        elif any(signal.extraction_status == "partial" for signal in signals):
            coverage_state = "partial"

    diffs: list[SnapshotDiff] = []
    for i, s in enumerate(signals):
        older_reports = signals[i + 1:]
        has_prior_report = bool(older_reports)
        prev = next(
            (
                candidate
                for candidate in older_reports
                if scale.has_comparable_fields(candidate, s)
            ),
            None,
        )
        if prev is not None:
            note = None
        elif has_prior_report:
            note = "有前次研報，但沒有可比較欄位"
        else:
            note = "沒有更早研報"
        diffs.append(SnapshotDiff(
            from_report_id=prev.report_id if prev else None,
            from_report_date=_iso(prev.report_date) if prev else None,
            to_report_date=_iso(s.report_date) or "",
            changes=[_change_item(c) for c in scale.diff_signals(prev, s)],
            has_prior_report=has_prior_report,
            has_prior_comparable=prev is not None,
            note=note,
        ))

    return BrokerHistoryResponse(
        market=market, instrument_code=code, broker=broker,
        broker_display=_broker_display(broker), window=window, as_of=_iso(as_of),
        current_rating=signals[0].rating_normalized if signals else "unknown",
        report_count=len(signals), snapshots=snapshots, diffs=diffs,
        coverage_state=coverage_state,
    )
