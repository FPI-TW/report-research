"""觀點雷達純 SQL 取數層（named param、無字串拼接注入；風格對齊 overview.py/store.py）。

窗期過濾交給 Python（單標的訊號通常僅數十列），此層只負責「取該標的/券商的全部有效
訊號」「覆蓋度計數」「標的目錄」。jsonb 欄位以 ::text 取出（見 types.SIGNAL_SELECT_COLUMNS）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.tagging import MARKETS

from app.services.radar.types import (
    EFFECTIVE_BROKER_SQL,
    SIGNAL_SELECT_SQL,
    Signal,
    parse_signal_row,
)

VALID_STATUSES = ["valid", "partial"]


@dataclass
class CoverageCounts:
    market: str
    instrument_code: str
    instrument_name: Optional[str]
    brokers_total: int
    brokers_extracted: int
    reports_available: int
    has_reports: bool


@dataclass(frozen=True)
class BrokerCoverageCounts:
    instrument_reports_available: int
    broker_reports_available: int


@dataclass
class RadarInstrumentRow:
    market: str
    instrument_code: str
    instrument_name: Optional[str]
    broker_count: int
    report_count: int
    latest_report_date: Optional[date]
    coverage_state: str  # ok | partial


def _instrument_signals_sql(broker: bool) -> str:
    where_broker = f"AND {EFFECTIVE_BROKER_SQL} = :broker " if broker else ""
    order = (
        "ORDER BY s.report_date DESC NULLS LAST, s.created_at DESC, s.id DESC"
        if broker
        else (
            f"ORDER BY {EFFECTIVE_BROKER_SQL} ASC NULLS LAST, "
            "s.report_date DESC NULLS LAST, s.created_at DESC, s.id DESC"
        )
    )
    return (
        f"SELECT {SIGNAL_SELECT_SQL} "
        "FROM research.report_signal s "
        "JOIN research.research_report r ON r.id = s.report_id "
        "WHERE s.market = :market AND s.instrument_code = :code "
        "  AND s.extraction_status = ANY(:statuses) "
        f"{where_broker}"
        f"{order}"
    )


async def fetch_instrument_signals(
    session: AsyncSession, market: str, code: str, *, statuses=VALID_STATUSES
) -> list[Signal]:
    rows = (
        await session.execute(
            text(_instrument_signals_sql(broker=False)),
            {"market": market, "code": code, "statuses": list(statuses)},
        )
    ).all()
    return [parse_signal_row(r) for r in rows]


async def fetch_broker_signals(
    session: AsyncSession, market: str, code: str, broker: str, *, statuses=VALID_STATUSES
) -> list[Signal]:
    rows = (
        await session.execute(
            text(_instrument_signals_sql(broker=True)),
            {"market": market, "code": code, "broker": broker, "statuses": list(statuses)},
        )
    ).all()
    return [parse_signal_row(r) for r in rows]


# 陣列參數一律用 CAST(:x AS text[])，不可寫 :x::text[]：text() 的 bind 比對規則遇到
# 參數名緊接 :: 會回溯成短名（:markets → bind "market" ＋殘字 "s"），冒號原樣送進 PG 而炸
# syntax error，且缺參數不會有例外——只在真的打 DB 時才爆。
_BATCH_SIGNALS_SQL = text(
    f"SELECT {SIGNAL_SELECT_SQL} "
    "FROM research.report_signal s "
    "JOIN research.research_report r ON r.id = s.report_id "
    "WHERE (s.market, s.instrument_code) IN ("
    "  SELECT m, c FROM unnest(CAST(:markets AS text[]), CAST(:codes AS text[])) AS t(m, c)) "
    "  AND s.extraction_status = ANY(:statuses) "
    f"ORDER BY s.market, s.instrument_code, {EFFECTIVE_BROKER_SQL} ASC NULLS LAST, "
    "         s.report_date DESC NULLS LAST, s.created_at DESC, s.id DESC"
)


async def fetch_signals_for_instruments(
    session: AsyncSession, keys: list[tuple[str, str]], *, statuses=VALID_STATUSES
) -> dict[tuple[str, str], list[Signal]]:
    """一次批次抓多檔（當頁 ≤50）的全部有效訊號，依 (market, code) 分組。

    picker 卡片精簡共識用：單次查詢取代逐檔 N+1；窗期過濾仍交給 Python。
    """
    if not keys:
        return {}
    out: dict[tuple[str, str], list[Signal]] = {}
    rows = (
        await session.execute(
            _BATCH_SIGNALS_SQL,
            {
                "markets": [m for m, _ in keys],
                "codes": [c for _, c in keys],
                "statuses": list(statuses),
            },
        )
    ).all()
    for r in rows:
        sig = parse_signal_row(r)
        out.setdefault((sig.market, sig.instrument_code), []).append(sig)
    return out


_COVERAGE_SQL = text(
    """
    SELECT
      (SELECT count(DISTINCT COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), '')))
         FROM research.research_report r
         LEFT JOIN research.report_signal s
           ON s.report_id = r.id
          AND s.market = :market
          AND s.instrument_code = :code
         WHERE r.market = :market AND :code = ANY(r.stock_targets)
           AND r.is_research IS NOT FALSE) AS brokers_total,
      (SELECT count(DISTINCT COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), '')))
         FROM research.report_signal s
         JOIN research.research_report r ON r.id = s.report_id
         WHERE s.market = :market AND s.instrument_code = :code
           AND s.extraction_status = ANY(:statuses)
           AND r.market = :market AND :code = ANY(r.stock_targets)
           AND r.is_research IS NOT FALSE) AS brokers_extracted,
      (SELECT count(*) FROM research.research_report r
         WHERE r.market = :market AND :code = ANY(r.stock_targets)
           AND r.is_research IS NOT FALSE) AS reports_available,
      (SELECT r.company_name FROM research.research_report r
         WHERE r.market = :market AND r.stock_code = :code
           AND r.company_name IS NOT NULL
           AND r.is_research IS NOT FALSE
         ORDER BY r.report_date DESC NULLS LAST, r.created_at DESC, r.id
         LIMIT 1) AS instrument_name
    """
)


async def fetch_coverage_counts(
    session: AsyncSession, market: str, code: str
) -> CoverageCounts:
    row = (
        await session.execute(
            _COVERAGE_SQL, {"market": market, "code": code, "statuses": VALID_STATUSES}
        )
    ).first()
    brokers_total = int(row[0] or 0)
    brokers_extracted = int(row[1] or 0)
    reports_available = int(row[2] or 0)
    return CoverageCounts(
        market=market, instrument_code=code, instrument_name=row[3],
        brokers_total=brokers_total, brokers_extracted=brokers_extracted,
        reports_available=reports_available, has_reports=reports_available > 0,
    )


_BROKER_COVERAGE_SQL = text(
    f"""
    SELECT
      count(DISTINCT r.id) AS instrument_reports_available,
      count(DISTINCT r.id) FILTER (
        WHERE {EFFECTIVE_BROKER_SQL} = :broker
      ) AS broker_reports_available
    FROM research.research_report r
    LEFT JOIN research.report_signal s
      ON s.report_id = r.id
     AND s.market = :market
     AND s.instrument_code = :code
    WHERE r.market = :market
      AND :code = ANY(r.stock_targets)
      AND r.is_research IS NOT FALSE
    """
)


async def fetch_broker_coverage_counts(
    session: AsyncSession, market: str, code: str, broker: str
) -> BrokerCoverageCounts:
    """區分標的不存在、canonical broker 不存在與尚未擷取訊號。"""
    row = (
        await session.execute(
            _BROKER_COVERAGE_SQL,
            {"market": market, "code": code, "broker": broker},
        )
    ).first()
    return BrokerCoverageCounts(
        instrument_reports_available=int(row[0] or 0) if row else 0,
        broker_reports_available=int(row[1] or 0) if row else 0,
    )


def _catalog_cte() -> str:
    """有可展示訊號的標的目錄（每 (market, code) 一列）。"""
    return (
        "WITH signal_base AS ("
        "  SELECT s.market, s.instrument_code, s.report_date, s.extraction_status,"
        f"         {EFFECTIVE_BROKER_SQL} AS broker"
        "  FROM research.report_signal s"
        "  JOIN research.research_report r ON r.id = s.report_id"
        "  WHERE r.is_research IS NOT FALSE"
        "    AND s.market = r.market"
        "    AND s.market = ANY(:markets)"
        "    AND s.instrument_code = ANY(r.stock_targets)"
        "), sig AS ("
        "  SELECT market, instrument_code,"
        "         count(DISTINCT broker) FILTER (WHERE extraction_status = 'valid') AS sig_brokers,"
        "         max(report_date) FILTER (WHERE broker IS NOT NULL"
        "                                  AND extraction_status = ANY(:statuses)) AS latest,"
        "         bool_or(extraction_status = ANY(:statuses))"
        "           FILTER (WHERE broker IS NOT NULL) AS has_valid,"
        "         bool_or(extraction_status = 'partial')"
        "           FILTER (WHERE broker IS NOT NULL) AS has_partial"
        "  FROM signal_base GROUP BY market, instrument_code"
        "), rep AS ("
        "  SELECT r.market, st AS instrument_code,"
        f"         count(DISTINCT {EFFECTIVE_BROKER_SQL}) AS broker_count,"
        "         count(DISTINCT r.id) AS report_count,"
        "         (array_agg(r.company_name ORDER BY r.report_date DESC NULLS LAST,"
        "                                           r.created_at DESC, r.id)"
        "            FILTER (WHERE r.company_name IS NOT NULL"
        "                    AND r.stock_code = st))[1] AS name"
        "  FROM research.research_report r"
        "  CROSS JOIN LATERAL unnest(r.stock_targets) st"
        "  LEFT JOIN research.report_signal s"
        "    ON s.report_id = r.id"
        "   AND s.market = r.market"
        "   AND s.instrument_code = st"
        "  WHERE r.is_research IS NOT FALSE"
        "    AND r.market = ANY(:markets)"
        "  GROUP BY r.market, st"
        "), cat AS ("
        "  SELECT rep.market, rep.instrument_code, rep.name, rep.broker_count,"
        "         rep.report_count, sig.latest, sig.sig_brokers, sig.has_partial"
        "  FROM sig JOIN rep ON rep.market = sig.market"
        "                   AND rep.instrument_code = sig.instrument_code"
        "  WHERE sig.has_valid = true"
        ")"
    )


def _catalog_filters(market: Optional[str], q: Optional[str]) -> tuple[str, dict]:
    conds: list[str] = []
    params: dict = {"statuses": VALID_STATUSES, "markets": list(MARKETS)}
    if market:
        conds.append("cat.market = :market")
        params["market"] = market
    if q:
        conds.append("(cat.instrument_code ILIKE :q OR cat.name ILIKE :q)")
        params["q"] = f"%{q}%"
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


async def list_radar_instruments(
    session: AsyncSession, *, market: Optional[str] = None, q: Optional[str] = None,
    limit: int = 50, offset: int = 0,
) -> tuple[int, list[RadarInstrumentRow]]:
    where, params = _catalog_filters(market, q)
    cte = _catalog_cte()
    total = (
        await session.execute(text(f"{cte} SELECT count(*) FROM cat {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"{cte} SELECT cat.market, cat.instrument_code, cat.name, cat.broker_count, "
                f"cat.report_count, cat.latest, cat.sig_brokers, cat.has_partial FROM cat {where} "
                "ORDER BY cat.latest DESC NULLS LAST, cat.market, cat.instrument_code "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    items = [
        RadarInstrumentRow(
            market=r[0], instrument_code=r[1], instrument_name=r[2],
            broker_count=int(r[3] or 0), report_count=int(r[4] or 0),
            latest_report_date=r[5],
            coverage_state=(
                "partial"
                if bool(r[7]) or int(r[6] or 0) < int(r[3] or 0)
                else "ok"
            ),
        )
        for r in rows
    ]
    return int(total), items
