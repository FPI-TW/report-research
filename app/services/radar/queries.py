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

from app.services.radar.types import (
    EFFECTIVE_BROKER_SQL,
    SIGNAL_SELECT_SQL,
    Signal,
    parse_signal_row,
)
from app.services.retrieval import _LIKE_ESC
from app.services.tagging import MARKETS

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


@dataclass(frozen=True)
class RadarCatalogPage:
    """目錄一頁 ＋ 該頁所屬篩選結果的兩個整體數字。

    `total` 與 `latest_report_date` 講的是**整個篩選結果**（LIMIT 之前），不是這一頁：
    報頭的「N 檔標的／最新更新」要的正是這兩個。先前呼叫端拿 `items[0]` 當最新日期，
    那在預設排序下碰巧成立，一旦排序改成研報數就會靜默印出某一檔的日期。
    """

    total: int
    items: list[RadarInstrumentRow]
    latest_report_date: Optional[date]


# 目錄排序白名單：鍵是對外的值，值是**直接進 SQL 的字面**——所以只能白名單，不能拼接。
# 每一條都以 (market, instrument_code) 收尾：排序鍵有並列時，沒有決定性尾綴會讓同一列
# 在相鄰兩頁重複出現或整個消失，而 OFFSET 分頁不會為此報任何錯。
CATALOG_SORTS: dict[str, str] = {
    "latest": "cat.latest DESC NULLS LAST, cat.market, cat.instrument_code",
    "reports": (
        "cat.report_count DESC, cat.latest DESC NULLS LAST, cat.market, cat.instrument_code"
    ),
    "brokers": (
        "cat.broker_count DESC, cat.latest DESC NULLS LAST, cat.market, cat.instrument_code"
    ),
    "code": "cat.market, cat.instrument_code",
}
DEFAULT_CATALOG_SORT = "latest"


def catalog_order_by(sort: Optional[str]) -> str:
    """排序鍵 → ORDER BY 片段。未知值退回預設而非拋錯（路由層已用 enum 擋過一次）。"""
    return CATALOG_SORTS.get(sort or DEFAULT_CATALOG_SORT, CATALOG_SORTS[DEFAULT_CATALOG_SORT])


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


# 標的過濾一律寫成 stock_targets @> ARRAY[...]，不可寫 :code = ANY(r.stock_targets)：
# PG 的 array_ops GIN 只支援 && @> <@ =，text = text[] 不在其中，所以 = ANY() 形式讓
# idx_rr_stock_targets 完全用不上（每個子查詢都退化成 research_report 全掃）。兩種寫法在
# 這裡的 WHERE 用法下語意等價（arr 為 NULL/空、元素含 NULL、:code 非 NULL 皆已逐情境對過）。
# 註：現況約 1.4 萬列且 full_text 多半 TOAST 出去，單次全掃的量級估算只有數十毫秒（未實測）
# ——這是「規模一放大就線性惡化」的預防，不是已量測到的加速。
_COVERAGE_SQL = text(
    """
    SELECT
      (SELECT count(DISTINCT COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), '')))
         FROM research.research_report r
         LEFT JOIN research.report_signal s
           ON s.report_id = r.id
          AND s.market = :market
          AND s.instrument_code = :code
         WHERE r.market = :market AND r.stock_targets @> ARRAY[:code]::text[]
           AND r.is_research IS NOT FALSE) AS brokers_total,
      (SELECT count(DISTINCT COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), '')))
         FROM research.report_signal s
         JOIN research.research_report r ON r.id = s.report_id
         WHERE s.market = :market AND s.instrument_code = :code
           AND s.extraction_status = ANY(:statuses)
           AND r.market = :market AND r.stock_targets @> ARRAY[:code]::text[]
           AND r.is_research IS NOT FALSE) AS brokers_extracted,
      (SELECT count(*) FROM research.research_report r
         WHERE r.market = :market AND r.stock_targets @> ARRAY[:code]::text[]
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
      AND r.stock_targets @> ARRAY[:code]::text[]
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
        # 同 _COVERAGE_SQL 的理由改寫成 @>；右運算元是外層關聯的欄位而非 bind，nested loop
        # 下仍可拿它當 GIN 的搜尋鍵，而 = ANY() 形式連這個機會都沒有。
        "    AND r.stock_targets @> ARRAY[s.instrument_code]::text[]"
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
    # markets 一律傳入全部 MARKETS，看起來像恆真、其實不是：`market = ANY(:markets)` 擋掉
    # market IS NULL（尚未標市場）與不在這 9 碼內的髒值，兩者都不該進 response 的 enum。
    # 刪掉它會改變結果集，不是無謂條件。
    params: dict = {"statuses": VALID_STATUSES, "markets": list(MARKETS)}
    if market:
        conds.append("cat.market = :market")
        params["market"] = market
    if q:
        conds.append("(cat.instrument_code ILIKE :q OR cat.name ILIKE :q)")
        # 使用者輸入的 % 與 _ 是字面字元不是萬用字元（PostgreSQL LIKE 預設跳脫字元即反斜線）。
        params["q"] = "%" + q.translate(_LIKE_ESC) + "%"
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


async def list_radar_instruments(
    session: AsyncSession, *, market: Optional[str] = None, q: Optional[str] = None,
    limit: Optional[int] = 50, offset: int = 0, sort: Optional[str] = None,
) -> RadarCatalogPage:
    """目錄一頁。`limit=None` ＝不分頁、取回全部符合條件的列。

    `limit=None` 是給「立場篩選」用的：立場來自 `compute.build_instrument_slim()` 的
    中位數，SQL 算不出來，要跨分頁正確就只能先把符合條件的列全部取回、在 Python 算完
    共識再篩再分頁。這條路徑刻意由呼叫端明示，不是預設。
    """
    where, params = _catalog_filters(market, q)
    # 總數、整體最新日期與分頁列走同一次查詢：兩個 window 函式都在 LIMIT 之前算完整結果集，
    # 省掉「同一組 CTE（signal_base/sig/rep 三層聚合，rep 還 CROSS JOIN LATERAL unnest）
    # 為了兩個聚合再跑一遍」。cat 已是 CTE，多這兩個 window 幾乎免費。
    page_sql = ""
    if limit is not None:
        page_sql = " LIMIT :limit OFFSET :offset"
        params = {**params, "limit": limit, "offset": offset}
    # 逐欄取值一律走 .mappings() 的欄名，不用位置索引：這段 SELECT 先前是位置式解包，
    # 而位置式解包在中間插一欄時不會報錯、只會靜默把值接到隔壁欄位去。
    rows = (
        await session.execute(
            text(
                f"{_catalog_cte()} SELECT cat.market, cat.instrument_code, cat.name, "
                "cat.broker_count, cat.report_count, cat.latest, cat.sig_brokers, "
                "cat.has_partial, count(*) OVER () AS total, "
                "max(cat.latest) OVER () AS latest_overall "
                f"FROM cat {where} ORDER BY {catalog_order_by(sort)}{page_sql}"
            ),
            params,
        )
    ).mappings().all()
    # 零列時沒有任何列可帶 total／latest_overall，語意就是 0 與「不知道」。
    total = int(rows[0]["total"] or 0) if rows else 0
    latest_overall = rows[0]["latest_overall"] if rows else None
    items = [
        RadarInstrumentRow(
            market=r["market"], instrument_code=r["instrument_code"],
            instrument_name=r["name"],
            broker_count=int(r["broker_count"] or 0),
            report_count=int(r["report_count"] or 0),
            latest_report_date=r["latest"],
            coverage_state=(
                "partial"
                if bool(r["has_partial"])
                or int(r["sig_brokers"] or 0) < int(r["broker_count"] or 0)
                else "ok"
            ),
        )
        for r in rows
    ]
    return RadarCatalogPage(total=total, items=items, latest_report_date=latest_overall)


async def fetch_catalog_facets(
    session: AsyncSession, *, q: Optional[str] = None
) -> dict[str, int]:
    """各市場的標的筆數（受搜尋詞影響，**不受市場篩選影響**）。

    市場膠囊上的數字要回答的是「切到那個市場會看到幾檔」，所以這裡刻意不套 market 條件。
    另一個刻意的取捨是它自成一次查詢、而不是塞進上面那支的 window 函式：facets 在
    「搜尋沒有任何命中」時仍然要有值（那正是最需要它的時候——使用者要靠它知道該切到哪個
    市場），而零列的結果集帶不回任何 window 值。代價是 CTE 多跑一次。
    """
    where, params = _catalog_filters(None, q)
    rows = (
        await session.execute(
            text(
                f"{_catalog_cte()} SELECT cat.market AS market, count(*) AS n "
                f"FROM cat {where} GROUP BY cat.market"
            ),
            params,
        )
    ).mappings().all()
    return {r["market"]: int(r["n"] or 0) for r in rows}
