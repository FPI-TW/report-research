"""使用分析（Admin v2 Analytics）：只出彙總的指標計算、k 門檻抑制與每晚彙總的寫入。

兩個呼叫端、同一組 SQL：

- **web**（`web/routers/admin_analytics.py`，`/api/admin/analytics/*`）：最近 `ANALYTICS_LIVE_WINDOW_DAYS`（90）天
  即時查 `qa_log`、`usage_counter`；更早的日子讀 `analytics_daily`。回應的 `range.spans` 標示每一段的來源。
- **每晚彙總**（`scripts/analytics_rollup.py`，零 LLM）：把一天的同一組指標寫進 `analytics_daily`
  （`compute_daily` → `write_day`）。即時與彙總共用 `compute_daily`，所以兩段的數字是同一把尺。

`usage_daily`（閱讀、原檔、搜尋的「主題×日」計數）與 `report_upload`、`admin_audit_log` 都是永久保存、不會被
使用者刪除的表，任何日期都直接查，不經彙總。

**隱私（使用者定案 2、3、4；寫進結構，不靠呼叫端自律）**

- 回應只有彙總：沒有任何依使用者拆分的視圖，SQL 只在 `count(DISTINCT user_id)` 裡碰 user_id，
  不選出、不分組；問題與答案文字（`qa_log.question`／`answer`）完全不讀。`analytics_daily` 的 dim 只放系統詞彙
  （路由類別、模型、錯誤種類）、市場、標的代碼與研報 id，**從不放使用者識別**。
- **k 門檻**：指向標的、研報、市場、搜尋市場與路由類別這類可能回推個人偏好的格子，不重複人數 < k
  （`ANALYTICS_MIN_USERS`，3）一律抑制——數值與人數都不給（`suppressed=true`），前端顯示「<3」。
  開放詞彙的清單（標的、研報、閱讀、原檔）連鍵都不給，只回被抑制的項目數；固定詞彙（市場、路由類別）才保留鍵。
  被抑制的格子一律排在可見格子之後、依鍵排序——依（被隱藏的）數值排序等於洩漏大小關係。
- **互補抑制**：分布的總量減掉可見格＝被抑制格的總和，被抑制的恰好 1 格時就被唯一推回。固定詞彙的分布再抑制
  一格「數值最小、未被抑制」的格子（`suppression_reason=complementary`，同樣不給數值），整個分布只剩那 1 格時連鍵
  都拿掉（`protect_fixed`）；開放詞彙清單被藏恰好 1 項且可見項全數回傳時，同理再藏一項（`top_list`）。
  只有總量（每日問答數、活躍人數、延遲、上傳審核量）不設門檻。
- 人數是**下限**：多日彙總的不重複人數無法再去重，跨日合併取各日的最大值（`merge_cells`）；
  `usage_daily.users` 本身就是下限。下限只會讓抑制更保守。`qa_log.user_id` 為 NULL 的列（個別帳號上線前的
  共用歷史、免登入開發模式）計入次數、不計入人數，所以它們永遠湊不滿門檻。
- 刪帳：即時統計隨 `qa_log` 被刪而減少（尊重刪除）；`analytics_daily` 沒有 user_id，已寫入的日子保留。
  所以預設的回填只補「還沒彙總過」的日子（`missing_days`），不覆寫已保留的彙總，除非明確 `--force`。

**彙總段以彙總當時的值為準**：「低於門檻」用的是當晚的 `FAITHFULNESS_MIN`；讚／倒讚是當晚的狀態，之後才按的
不會補進已彙總的日子（`--force` 重算會更新，但也會讓被硬刪的問答從彙總裡消失）。

**忠實度**一律經 `app/services/judge_schema.py` 的 `CURRENT_JUDGE_SQL`（只計現行 judge），分數 SQL 與
`web/stats_snapshot.py` 的 `_EVAL_COLUMNS` 同一套（jsonb 先 `jsonb_typeof` 再 cast，degraded 不計分）。
這裡刻意不 import 監控 router：彙總腳本不得把 web 與抽取層拖進來。彙總把 judge 名稱寫在 dim，讀的時候只取
現行 judge 那一格——換 judge 之後舊尺的分數不會混進趨勢。

延遲百分位用 PostgreSQL 的 `percentile_disc`：回傳「累積比例 ≥ p 的第一個值」，與
`scripts/analyze_qa_log.py` 的 `percentiles`（nearest-rank：`ceil(p·n) − 1`）逐值相同。多日的百分位不能由
每日百分位合併，所以範圍層級的 p50／p95 只算即時那一段，每日的點則兩段都有。

日期一律台北時間的日曆日（與 `usage_events`、上傳配額一致）；週以週一起算（PostgreSQL `date_trunc('week')`）。
**刻意不 import 檢索、嵌入、LLM 模組**（`tests/test_analytics.py` 以子行程確認不會載入 torch）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from app.services.judge_schema import CURRENT_JUDGE_SQL
from app.services.tagging import MARKET_DISPLAY, MARKETS

TZ = timezone(timedelta(hours=8))
TZ_NAME = "Asia/Taipei"

SOURCE_LIVE = "live"
SOURCE_ROLLUP = "rollup"

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 731
TOP_DEFAULT_LIMIT = 20
TOP_MAX_LIMIT = 100
_MAX_DIM = 128

# 每晚彙總寫的標記：有這一列＝這一天算過（即使當天完全沒有活動）。回填據此找缺漏的日子。
ROLLUP_MARKER = "rollup.computed"

# ── analytics_daily 的 metric 詞彙（DB 只限形狀，唯一定義在這裡）──────────────────
M_QUESTIONS = "qa.questions"            # users＝當天問過問題的不重複人數
M_STOPPED = "qa.stopped"
M_LAT_P50 = "qa.latency_p50"            # 毫秒；不含停止列
M_LAT_P95 = "qa.latency_p95"
M_THINK_P50 = "qa.thinking_p50"         # 首字時間（毫秒）
M_THINK_P95 = "qa.thinking_p95"
M_LAT_N = "qa.latency_n"
M_TRUNCATED = "qa.llm_truncated"
M_INVALID_ROWS = "qa.invalid_citation_rows"
M_INVALID_TOTAL = "qa.invalid_citations"
M_ACTIVE = "users.active"               # qa_log ∪ usage_counter 的不重複人數
M_FEEDBACK_LIKE = "feedback.like"
M_FEEDBACK_DISLIKE = "feedback.dislike"
M_CHECKED_ALL = "quality.checked_all"   # 所有 judge（覆蓋率類）
# 以下五個只計現行 judge，dim＝judge 名稱。
M_JUDGE_CHECKED = "quality.judge_checked"
M_DEGRADED = "quality.degraded"
M_BELOW_MIN = "quality.below_min"
M_SCORE_SUM = "quality.score_sum"
M_SCORE_N = "quality.score_n"
JUDGE_METRICS = (M_JUDGE_CHECKED, M_DEGRADED, M_BELOW_MIN, M_SCORE_SUM, M_SCORE_N)
# 路由分布（dim＝filters 的值）；只有 path 受 k 門檻約束（見 SUPPRESSIBLE_ROUTES）。
ROUTE_KEYS = ("path", "decided_by", "llm_model", "llm_error")
SUPPRESSIBLE_ROUTES = frozenset({"path"})
# 熱門（dim＝研報 id／標的代碼／市場代碼），users＝引用它的不重複人數，一律受 k 門檻約束。
M_HOT_REPORT = "hot.report"
M_HOT_TARGET = "hot.target"
M_HOT_MARKET = "hot.market"

# usage_daily 的類別（與 app/services/usage_events.py 的 KIND_* 一致；這裡只讀）。
USAGE_KINDS = ("reading", "report_file", "search", "ask")

# 上傳審核量看的稽核 action（只計次數，不看 detail）。
AUDIT_ACTIONS = (
    "review.update", "qa_content.read", "upload.create", "upload.publish", "upload.reject",
    "report.hide", "report.restore",
)


@dataclass(frozen=True)
class Params:
    """計算需要的設定（`params_from_settings` 從 app/config.py 取；測試直接建）。"""

    min_users: int = 3
    live_days: int = 90
    judge_model: str = ""
    faithfulness_min: float = 0.9


def params_from_settings(settings=None) -> Params:
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    return Params(min_users=int(settings.analytics_min_users), live_days=int(settings.analytics_live_window_days),
                  judge_model=str(settings.faithfulness_model), faithfulness_min=float(settings.faithfulness_min))


@dataclass(frozen=True)
class MetricRow:
    day: date | None   # None＝範圍層級（整段一列）
    metric: str
    dim: str
    value: float
    users: int | None


def today() -> date:
    return datetime.now(TZ).date()


def day_start(d: date) -> datetime:
    """台北時間 d 的 00:00（tz-aware）。"""
    return datetime.combine(d, time(0), TZ)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _dim(value) -> str:
    return str(value if value is not None else "")[:_MAX_DIM]


# ── 範圍：哪一段即時、哪一段讀彙總 ──────────────────────────────────────────


@dataclass(frozen=True)
class Plan:
    since: date
    until: date
    today: date
    live_since: date
    live: tuple[date, date] | None
    rollup: tuple[date, date] | None


class RangeError(ValueError):
    pass


def plan_range(since: date | None, until: date | None, *, today_: date, live_days: int,
               default_days: int = DEFAULT_RANGE_DAYS, max_days: int = MAX_RANGE_DAYS) -> Plan:
    """[since, until]（含兩端、台北日期）。until 預設今天、晚於今天時截到今天；since 預設往前 default_days 天。"""
    until = min(until or today_, today_)
    since = since or (until - timedelta(days=default_days - 1))
    if since > until:
        raise RangeError("since 不可晚於 until")
    if (until - since).days + 1 > max_days:
        raise RangeError(f"時間範圍最多 {max_days} 天")
    live_since = today_ - timedelta(days=live_days - 1)
    live = (max(since, live_since), until) if until >= live_since else None
    rollup = (since, min(until, live_since - timedelta(days=1))) if since < live_since else None
    return Plan(since=since, until=until, today=today_, live_since=live_since, live=live, rollup=rollup)


def range_info(plan: Plan, params: Params) -> dict:
    spans = []
    if plan.rollup:
        spans.append({"since": plan.rollup[0].isoformat(), "until": plan.rollup[1].isoformat(),
                      "source": SOURCE_ROLLUP})
    if plan.live:
        spans.append({"since": plan.live[0].isoformat(), "until": plan.live[1].isoformat(), "source": SOURCE_LIVE})
    return {"since": plan.since.isoformat(), "until": plan.until.isoformat(), "today": plan.today.isoformat(),
            "live_since": plan.live_since.isoformat(), "timezone": TZ_NAME, "min_users": params.min_users,
            "spans": spans}


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ── k 門檻 ──────────────────────────────────────────────────────────────────


def is_suppressed(users: int | None, k: int) -> bool:
    """不重複人數未達 k（含不知道人數）就抑制。"""
    return users is None or users < k


REASON_MIN_USERS = "min_users"          # 不重複人數 < k
REASON_COMPLEMENTARY = "complementary"  # 互補抑制：避免以總量減其他格推回唯一被抑制的那一格


def make_cell(key: str, value: float | None, users: int | None, *, k: int, suppressible: bool,
              label: str | None = None) -> dict:
    """一個格子。抑制時數值與人數都是 None（不是 0：0 也是資訊）。"""
    if suppressible and is_suppressed(users, k):
        return {"key": key, "label": label, "value": None, "users": None, "suppressed": True,
                "suppression_reason": REASON_MIN_USERS}
    return {"key": key, "label": label, "value": _num(value), "users": users, "suppressed": False,
            "suppression_reason": None}


def _co_suppress(cell: dict) -> dict:
    return {**cell, "value": None, "users": None, "suppressed": True, "suppression_reason": REASON_COMPLEMENTARY}


def _smallest(visible: list[dict]) -> dict:
    return min(visible, key=lambda c: (c["value"] or 0, c["key"]))


def protect_fixed(cells: list[dict]) -> tuple[list[dict], int]:
    """固定詞彙分布（市場、路由類別）的互補抑制，回傳 (格子, 連鍵都拿掉的格數)。

    詞彙固定時，分布的總量（例如問答總數）減掉所有可見格，就是被抑制格的總和；被抑制的若**恰好 1 格**，
    它的數值就被唯一推回。所以再抑制一格「數值最小、未被抑制」的格子（`suppression_reason=complementary`，
    同樣不給數值與人數）：總量減其他格只剩兩格的和。被抑制 0 格或 ≥ 2 格時不動。
    唯一被抑制的格子旁邊已經沒有可見格可以陪它時（整個分布只有它），連鍵都拿掉、只計數——
    總量就是它的值，留著鍵等於公開「那 1–2 個人全都問了這一類」。
    """
    hidden = [c for c in cells if c["suppressed"]]
    if len(hidden) != 1:
        return cells, 0
    visible = [c for c in cells if not c["suppressed"]]
    if not visible:
        return [], 1
    target = _smallest(visible)
    return [_co_suppress(c) if c is target else c for c in cells], 0


def _num(v):
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def order_cells(cells: list[dict]) -> list[dict]:
    """可見格子依數值大→小、鍵小→大；被抑制的排在最後、只依鍵排序（不洩漏被隱藏數值的大小關係）。"""
    visible = sorted((c for c in cells if not c["suppressed"]), key=lambda c: (-(c["value"] or 0), c["key"]))
    hidden = sorted((c for c in cells if c["suppressed"]), key=lambda c: c["key"])
    return visible + hidden


def merge_cells(parts: Iterable[tuple[str, float, int | None]]) -> dict[str, tuple[float, int | None]]:
    """把多段（即時的範圍層級＋彙總的每日）同一鍵的格子併起來：數值相加、人數取最大值（不重複人數的下限）。"""
    out: dict[str, tuple[float, int | None]] = {}
    for key, value, users in parts:
        v0, u0 = out.get(key, (0.0, None))
        u = users if u0 is None else (u0 if users is None else max(u0, users))
        out[key] = (v0 + float(value or 0), u)
    return out


def top_list(merged: dict[str, tuple[float, int | None]], *, k: int, limit: int, open_vocab: bool,
             labels: dict[str, str] | None = None, vocabulary: Iterable[str] | None = None) -> dict:
    """熱門清單。

    - 開放詞彙（標的、研報、閱讀、原檔）：被抑制的連鍵都不給，只回 `suppressed_count`。總量（例如總覽的閱讀次數）
      減掉回傳的項目＝被藏項目的總和；被藏的恰好 1 項、而且可見項目全數回傳（沒被 limit 截斷）時，那 1 項的值會被
      唯一推回——雖然推不出是哪一篇，仍比照互補抑制再藏起數值最小的可見項（`complementary_count`）。
      被 limit 截斷時，截掉的可見項本身就讓差值不唯一，不必再藏。
    - 固定詞彙（市場）：被抑制的格子保留鍵、顯示「<3」，並做互補抑制（`protect_fixed`）；詞彙裡沒出現的鍵不補
      （0 不是有人用過）。
    """
    labels = labels or {}
    cells = [make_cell(key, v, u, k=k, suppressible=True, label=labels.get(key)) for key, (v, u) in merged.items()]
    if vocabulary is not None:
        allowed = set(vocabulary)
        cells = [c for c in cells if c["key"] in allowed]
    if open_vocab:
        ordered = order_cells(cells)
        visible = [c for c in ordered if not c["suppressed"]]
        hidden = [c for c in ordered if c["suppressed"]]
        complementary = 0
        if len(hidden) == 1 and visible and len(visible) <= limit:
            target = _smallest(visible)
            visible = [c for c in visible if c is not target]
            complementary = 1
        return {"cells": visible[:limit], "suppressed_count": len(hidden), "complementary_count": complementary,
                "truncated": len(visible) > limit}
    cells, dropped = protect_fixed(cells)
    ordered = order_cells(cells)
    visible = [c for c in ordered if not c["suppressed"]]
    hidden = [c for c in ordered if c["suppressed"]]
    return {"cells": visible[:limit] + hidden,
            "suppressed_count": sum(c["suppression_reason"] == REASON_MIN_USERS for c in hidden) + dropped,
            "complementary_count": sum(c["suppression_reason"] == REASON_COMPLEMENTARY for c in hidden),
            "truncated": len(visible) > limit}


# ── SQL ──────────────────────────────────────────────────────────────────────

_DAY = f"(q.created_at AT TIME ZONE '{TZ_NAME}')::date"
_RANGE = "q.created_at >= :start_ts AND q.created_at < :end_ts"
# 與 web/stats_snapshot.py 的 _EVAL_SCORE_SQL／_EVAL_NOT_DEGRADED_SQL 同義。
_SCORE = ("CASE WHEN jsonb_typeof(q.evaluation->'faithfulness_score') = 'number' "
          "THEN (q.evaluation->>'faithfulness_score')::float END")
_NOT_DEGRADED = "q.evaluation->'degraded' IS DISTINCT FROM 'true'::jsonb"
_INVALID = ("CASE WHEN jsonb_typeof(q.filters->'invalid_citation_count') = 'number' "
            "THEN (q.filters->>'invalid_citation_count')::numeric END")
# CURRENT_JUDGE_SQL 寫的是裸欄位 evaluation；qa_log 在這裡的別名是 q，單表查詢裡兩者等價。
_JUDGE = CURRENT_JUDGE_SQL


def _group(by_day: bool) -> str:
    return _DAY if by_day else "NULL::date"


def _qa_sql(by_day: bool) -> str:
    g = _group(by_day)
    return f"""
SELECT {g} AS d,
       count(*) AS questions,
       count(DISTINCT q.user_id) AS askers,
       count(*) FILTER (WHERE q.stopped) AS stopped,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY q.latency_ms) FILTER (WHERE NOT q.stopped) AS lat50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY q.latency_ms) FILTER (WHERE NOT q.stopped) AS lat95,
       count(q.latency_ms) FILTER (WHERE NOT q.stopped) AS lat_n,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY q.thinking_ms) FILTER (WHERE NOT q.stopped) AS th50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY q.thinking_ms) FILTER (WHERE NOT q.stopped) AS th95,
       count(*) FILTER (WHERE q.filters->'llm_truncated' = 'true'::jsonb) AS truncated,
       count(*) FILTER (WHERE ({_INVALID}) > 0) AS inv_rows,
       coalesce(sum({_INVALID}), 0) AS inv_total,
       count(*) FILTER (WHERE q.feedback = 'like') AS likes,
       count(*) FILTER (WHERE q.feedback = 'dislike') AS dislikes,
       count(q.evaluation) AS checked_all,
       count(q.evaluation) FILTER (WHERE {_JUDGE}) AS judge_checked,
       count(*) FILTER (WHERE q.evaluation->'degraded' = 'true'::jsonb AND {_JUDGE}) AS degraded,
       count(*) FILTER (WHERE ({_SCORE}) < :fmin AND {_JUDGE}) AS below_min,
       coalesce(sum({_SCORE}) FILTER (WHERE {_JUDGE} AND {_NOT_DEGRADED}), 0) AS score_sum,
       count({_SCORE}) FILTER (WHERE {_JUDGE} AND {_NOT_DEGRADED}) AS score_n
FROM research.qa_log q
WHERE {_RANGE}
GROUP BY 1
"""


def _route_sql(by_day: bool) -> str:
    g = _group(by_day)
    return f"""
SELECT {g} AS d, f.k, f.j #>> '{{}}' AS v, count(*) AS n, count(DISTINCT q.user_id) AS u
FROM research.qa_log q
CROSS JOIN LATERAL (VALUES ('path', q.filters->'path'), ('decided_by', q.filters->'decided_by'),
                           ('llm_model', q.filters->'llm_model'), ('llm_error', q.filters->'llm_error')) AS f(k, j)
WHERE {_RANGE} AND jsonb_typeof(f.j) = 'string'
GROUP BY 1, 2, 3
"""


def _hot_sql(by_day: bool) -> str:
    # 一題引用同一篇多次、或多篇同標的／同市場，都只算一次（count DISTINCT 題目 id）。
    g = "c.d" if by_day else "NULL::date"
    return f"""
WITH c AS (
    SELECT DISTINCT {_DAY} AS d, q.id AS qa_id, q.user_id, r.id AS report_id, r.market, r.stock_targets
    FROM research.qa_log q
    CROSS JOIN LATERAL unnest(q.cited_report_ids) AS cited(rid)
    JOIN research.research_report r ON r.id = cited.rid
    WHERE {_RANGE} AND q.cited_report_ids IS NOT NULL
)
SELECT {g} AS d, 'report' AS k, c.report_id::text AS v, count(DISTINCT c.qa_id) AS n,
       count(DISTINCT c.user_id) AS u
FROM c GROUP BY 1, 2, 3
UNION ALL
SELECT {g}, 'market', c.market, count(DISTINCT c.qa_id), count(DISTINCT c.user_id)
FROM c WHERE c.market IS NOT NULL GROUP BY 1, 2, 3
UNION ALL
SELECT {g}, 'target', t.code, count(DISTINCT c.qa_id), count(DISTINCT c.user_id)
FROM c CROSS JOIN LATERAL unnest(c.stock_targets) AS t(code)
WHERE t.code IS NOT NULL AND t.code <> '' GROUP BY 1, 2, 3
"""


def _active_sql(by_day: bool) -> str:
    g, cg = (_DAY, "c.day") if by_day else ("NULL::date", "NULL::date")
    return f"""
SELECT x.d, count(DISTINCT x.uid) AS u
FROM (
    SELECT {g} AS d, q.user_id AS uid FROM research.qa_log q WHERE {_RANGE} AND q.user_id IS NOT NULL
    UNION ALL
    SELECT {cg}, c.user_id FROM research.usage_counter c
    WHERE c.day >= :start_day AND c.day <= :end_day AND c.count > 0
) x
GROUP BY 1
"""


_HOT_METRIC = {"report": M_HOT_REPORT, "market": M_HOT_MARKET, "target": M_HOT_TARGET}


def _bind(start: date, end: date, params: Params) -> dict:
    return {"start_ts": day_start(start), "end_ts": day_start(end + timedelta(days=1)), "start_day": start,
            "end_day": end, "judge_model": params.judge_model, "fmin": params.faithfulness_min}


def _qa_rows(r, judge: str) -> list[MetricRow]:
    d = r.d
    out = [
        MetricRow(d, M_QUESTIONS, "", float(r.questions), int(r.askers)),
        MetricRow(d, M_STOPPED, "", float(r.stopped), None),
        MetricRow(d, M_LAT_N, "", float(r.lat_n), None),
        MetricRow(d, M_TRUNCATED, "", float(r.truncated), None),
        MetricRow(d, M_INVALID_ROWS, "", float(r.inv_rows), None),
        MetricRow(d, M_INVALID_TOTAL, "", float(r.inv_total), None),
        MetricRow(d, M_FEEDBACK_LIKE, "", float(r.likes), None),
        MetricRow(d, M_FEEDBACK_DISLIKE, "", float(r.dislikes), None),
        MetricRow(d, M_CHECKED_ALL, "", float(r.checked_all), None),
        MetricRow(d, M_JUDGE_CHECKED, judge, float(r.judge_checked), None),
        MetricRow(d, M_DEGRADED, judge, float(r.degraded), None),
        MetricRow(d, M_BELOW_MIN, judge, float(r.below_min), None),
        MetricRow(d, M_SCORE_SUM, judge, float(r.score_sum), None),
        MetricRow(d, M_SCORE_N, judge, float(r.score_n), None),
    ]
    for metric, v in ((M_LAT_P50, r.lat50), (M_LAT_P95, r.lat95), (M_THINK_P50, r.th50), (M_THINK_P95, r.th95)):
        if v is not None:
            out.append(MetricRow(d, metric, "", float(v), None))
    return out


async def qa_metrics(session, start: date, end: date, params: Params, *, by_day: bool) -> list[MetricRow]:
    rows = (await session.execute(text(_qa_sql(by_day)), _bind(start, end, params))).all()
    judge = _dim(params.judge_model)
    return [m for r in rows for m in _qa_rows(r, judge)]


async def route_metrics(session, start: date, end: date, params: Params, *, by_day: bool) -> list[MetricRow]:
    rows = (await session.execute(text(_route_sql(by_day)), _bind(start, end, params))).all()
    return [MetricRow(r.d, f"route.{r.k}", _dim(r.v), float(r.n), int(r.u)) for r in rows]


async def hot_metrics(session, start: date, end: date, params: Params, *, by_day: bool) -> list[MetricRow]:
    rows = (await session.execute(text(_hot_sql(by_day)), _bind(start, end, params))).all()
    return [MetricRow(r.d, _HOT_METRIC[r.k], _dim(r.v), float(r.n), int(r.u)) for r in rows]


async def active_metrics(session, start: date, end: date, params: Params, *, by_day: bool) -> list[MetricRow]:
    rows = (await session.execute(text(_active_sql(by_day)), _bind(start, end, params))).all()
    return [MetricRow(r.d, M_ACTIVE, "", float(r.u), int(r.u)) for r in rows]


async def compute_daily(session, start: date, end: date, params: Params) -> list[MetricRow]:
    """[start, end] 每一天的全部指標（彙總寫入與即時讀取共用）。沒有活動的日子不產生列。"""
    out: list[MetricRow] = []
    for fn in (qa_metrics, route_metrics, hot_metrics, active_metrics):
        out.extend(await fn(session, start, end, params, by_day=True))
    return out


async def read_rollup(session, start: date, end: date, prefixes: tuple[str, ...] = ()) -> list[MetricRow]:
    """analytics_daily 在 [start, end] 的列（可依 metric 前綴篩）。"""
    where = ""
    bind: dict = {"s": start, "e": end}
    if prefixes:
        where = " AND (" + " OR ".join(f"metric LIKE :p{i}" for i in range(len(prefixes))) + ")"
        bind.update({f"p{i}": p.replace("_", r"\_") + "%" for i, p in enumerate(prefixes)})
    rows = (await session.execute(text(
        "SELECT day, metric, dim, value, users FROM research.analytics_daily "
        f"WHERE day >= :s AND day <= :e{where}"), bind)).all()
    return [MetricRow(r.day, r.metric, r.dim, float(r.value), r.users) for r in rows]


async def usage_totals(session, start: date, end: date) -> dict[tuple[date, str], int]:
    """usage_daily 每天各類的總量（subject=''）。"""
    rows = (await session.execute(text(
        "SELECT day, kind, hits FROM research.usage_daily "
        "WHERE subject = '' AND day >= :s AND day <= :e AND kind = ANY(CAST(:kinds AS text[]))"),
        {"s": start, "e": end, "kinds": list(USAGE_KINDS)})).all()
    return {(r.day, r.kind): int(r.hits) for r in rows}


async def usage_subjects(session, start: date, end: date, kind: str) -> dict[str, tuple[float, int | None]]:
    """usage_daily 某類在範圍內各主題的 (hits 總和, users 各日最大值)。"""
    rows = (await session.execute(text(
        "SELECT subject, sum(hits) AS hits, max(users) AS users FROM research.usage_daily "
        "WHERE kind = :kind AND subject <> '' AND day >= :s AND day <= :e GROUP BY subject"),
        {"kind": kind, "s": start, "e": end})).all()
    return {r.subject: (float(r.hits), int(r.users) if r.users is not None else None) for r in rows}


# ── 每晚彙總的寫入 ──────────────────────────────────────────────────────────

# pg_advisory_xact_lock(int4, int4) 的第一個鍵（"anly"）；第二個鍵是日期序數：同一天的兩次彙總排隊，不同天互不擋。
_LOCK_CLASS = 0x616E6C79

_INSERT_SQL = """
INSERT INTO research.analytics_daily (day, metric, dim, value, users)
SELECT * FROM unnest(CAST(:days AS date[]), CAST(:metrics AS text[]), CAST(:dims AS text[]),
                     CAST(:vals AS double precision[]), CAST(:users AS integer[]))
"""


async def has_rollup(session, day: date) -> bool:
    return (await session.execute(text(
        "SELECT EXISTS (SELECT 1 FROM research.analytics_daily WHERE day = :d AND metric = :m)"),
        {"d": day, "m": ROLLUP_MARKER})).scalar_one()


async def missing_days(session, start: date, end: date) -> list[date]:
    """[start, end] 裡還沒有彙總標記的日子（舊→新）。"""
    if start > end:
        return []
    done = {r[0] for r in (await session.execute(text(
        "SELECT day FROM research.analytics_daily WHERE metric = :m AND day >= :s AND day <= :e"),
        {"m": ROLLUP_MARKER, "s": start, "e": end})).all()}
    return [d for d in _days(start, end) if d not in done]


async def write_day(session, day: date, rows: list[MetricRow]) -> int:
    """覆寫一天：刪掉那天的全部列再寫入（含標記）。由呼叫端 commit；同一天的並行彙總以 advisory lock 排隊。"""
    await session.execute(text("SELECT pg_advisory_xact_lock(:c, :d)"), {"c": _LOCK_CLASS, "d": day.toordinal()})
    await session.execute(text("DELETE FROM research.analytics_daily WHERE day = :d"), {"d": day})
    rows = [r for r in rows if r.day == day] + [MetricRow(day, ROLLUP_MARKER, "", 1.0, None)]
    await session.execute(text(_INSERT_SQL), {
        "days": [r.day for r in rows], "metrics": [r.metric for r in rows], "dims": [_dim(r.dim) for r in rows],
        "vals": [float(r.value) for r in rows], "users": [r.users for r in rows],
    })
    return len(rows)


@dataclass(frozen=True)
class RollupResult:
    day: date
    rows: int
    skipped: bool


async def rollup_day(session_factory, day: date, params: Params, *, overwrite: bool, dry_run: bool = False
                     ) -> RollupResult:
    """彙總一天（一個交易）。overwrite=False 時已有標記就跳過（保留已彙總的匿名資料，見模組 docstring）。"""
    async with session_factory() as session:
        if not overwrite and await has_rollup(session, day):
            await session.rollback()
            return RollupResult(day, 0, True)
        rows = await compute_daily(session, day, day, params)
        if dry_run:
            await session.rollback()
            return RollupResult(day, len(rows) + 1, False)
        n = await write_day(session, day, rows)
        await session.commit()
        return RollupResult(day, n, False)


# ── 讀取端：組回應（純函式＋查詢）──────────────────────────────────────────


def _index(rows: Iterable[MetricRow]) -> dict[tuple[date | None, str, str], MetricRow]:
    return {(r.day, r.metric, r.dim): r for r in rows}


def _val(idx, d, metric, dim="") -> float | None:
    r = idx.get((d, metric, dim))
    return r.value if r is not None else None


def _sum(rows: Iterable[MetricRow], metric: str, dim: str | None = "") -> float:
    return sum(r.value for r in rows if r.metric == metric and (dim is None or r.dim == dim))


async def _daily_rows(session, plan: Plan, params: Params, prefixes: tuple[str, ...],
                      live_fns) -> tuple[list[MetricRow], set[date]]:
    """即時段（live_fns 逐日）＋彙總段的每日列；回傳 (列, 彙總段裡有標記的日子)。"""
    rows: list[MetricRow] = []
    rolled: set[date] = set()
    if plan.rollup:
        got = await read_rollup(session, *plan.rollup, prefixes=prefixes + (ROLLUP_MARKER,))
        rolled = {r.day for r in got if r.metric == ROLLUP_MARKER}
        rows.extend(r for r in got if r.metric != ROLLUP_MARKER)
    if plan.live:
        for fn in live_fns:
            rows.extend(await fn(session, *plan.live, params, by_day=True))
    return rows, rolled


def _source(plan: Plan, d: date) -> str:
    return SOURCE_LIVE if d >= plan.live_since else SOURCE_ROLLUP


async def overview(session, plan: Plan, params: Params) -> dict:
    """總覽：每日問答量、活躍人數、閱讀／原檔／搜尋總量、延遲。總量不設門檻。"""
    rows, rolled = await _daily_rows(session, plan, params, ("qa.", "users."), (qa_metrics, active_metrics))
    idx = _index(rows)
    usage = await usage_totals(session, plan.since, plan.until)
    daily = []
    for d in _days(plan.since, plan.until):
        src = _source(plan, d)
        has = src == SOURCE_LIVE or d in rolled
        q = idx.get((d, M_QUESTIONS, ""))
        daily.append({
            "day": d.isoformat(), "source": src, "has_data": has,
            "questions": int(q.value) if q else (0 if has else None),
            "askers": (q.users if q and q.users is not None else 0) if has else None,
            "active_users": int(_val(idx, d, M_ACTIVE) or 0) if has else None,
            "reading": usage.get((d, "reading"), 0), "report_file": usage.get((d, "report_file"), 0),
            "search": usage.get((d, "search"), 0),
            "latency_p50_ms": _val(idx, d, M_LAT_P50), "latency_p95_ms": _val(idx, d, M_LAT_P95),
            "thinking_p50_ms": _val(idx, d, M_THINK_P50), "thinking_p95_ms": _val(idx, d, M_THINK_P95),
        })
    latency = {"p50_ms": None, "p95_ms": None, "thinking_p50_ms": None, "thinking_p95_ms": None, "n": 0}
    distinct_live = None
    if plan.live:
        live_range = _index(await qa_metrics(session, *plan.live, params, by_day=False))
        latency = {"p50_ms": _val(live_range, None, M_LAT_P50), "p95_ms": _val(live_range, None, M_LAT_P95),
                   "thinking_p50_ms": _val(live_range, None, M_THINK_P50),
                   "thinking_p95_ms": _val(live_range, None, M_THINK_P95),
                   "n": int(_val(live_range, None, M_LAT_N) or 0)}
        active = await active_metrics(session, *plan.live, params, by_day=False)
        distinct_live = int(active[0].value) if active else 0
    totals = {
        "questions": int(_sum(rows, M_QUESTIONS)), "stopped": int(_sum(rows, M_STOPPED)),
        "reading": sum(p["reading"] for p in daily), "report_file": sum(p["report_file"] for p in daily),
        "search": sum(p["search"] for p in daily),
        "active_users_peak": max((p["active_users"] or 0 for p in daily), default=0),
        "distinct_users_live": distinct_live,
        "missing_days": sum(1 for p in daily if not p["has_data"]),
    }
    return {"range": range_info(plan, params), "totals": totals, "latency": latency, "daily": daily}


async def routes(session, plan: Plan, params: Params) -> dict:
    """路由分布（path 受 k 門檻約束）與 LLM 截斷、無效引用、停止的計數。"""
    daily, _rolled = await _daily_rows(session, plan, params, ("qa.", "route."), (qa_metrics,))
    parts: list[MetricRow] = [r for r in daily if r.metric.startswith("route.")]
    if plan.live:
        parts.extend(await route_metrics(session, *plan.live, params, by_day=False))
    distributions = []
    for key in ROUTE_KEYS:
        merged = merge_cells((r.dim, r.value, r.users) for r in parts if r.metric == f"route.{key}")
        suppressible = key in SUPPRESSIBLE_ROUTES
        cells = [make_cell(k, v, u, k=params.min_users, suppressible=suppressible) for k, (v, u) in merged.items()]
        dropped = 0
        if suppressible:   # 問答總數就在同一份回應裡：沒有互補抑制，總數減其他格就推回唯一被抑制的那一類
            cells, dropped = protect_fixed(cells)
        distributions.append({
            "name": key, "suppressible": suppressible, "cells": order_cells(cells),
            "suppressed_count": sum(c["suppression_reason"] == REASON_MIN_USERS for c in cells) + dropped,
            "complementary_count": sum(c["suppression_reason"] == REASON_COMPLEMENTARY for c in cells),
        })
    return {
        "range": range_info(plan, params),
        "questions": int(_sum(daily, M_QUESTIONS)), "stopped": int(_sum(daily, M_STOPPED)),
        "llm_truncated": int(_sum(daily, M_TRUNCATED)),
        "invalid_citation_rows": int(_sum(daily, M_INVALID_ROWS)),
        "invalid_citations": int(_sum(daily, M_INVALID_TOTAL)),
        "distributions": distributions,
    }


async def quality(session, plan: Plan, params: Params) -> dict:
    """忠實度（只計現行 judge）與回饋的週趨勢。"""
    rows, _rolled = await _daily_rows(session, plan, params, ("qa.", "quality.", "feedback."), (qa_metrics,))
    judge = _dim(params.judge_model)
    weeks: dict[date, dict[str, float]] = {}
    for d in _days(plan.since, plan.until):
        weeks.setdefault(week_start(d), {})
    for r in rows:
        if r.day is None:
            continue
        if r.metric in JUDGE_METRICS and r.dim != judge:
            continue   # 別的 judge 量的分數不混進趨勢（彙總段的 dim 是當時的 judge）
        w = weeks.setdefault(week_start(r.day), {})
        w[r.metric] = w.get(r.metric, 0.0) + r.value
    out = []
    for ws in sorted(weeks):
        w = weeks[ws]
        n = int(w.get(M_SCORE_N, 0))
        out.append({
            "week_start": ws.isoformat(),
            "partial": ws < plan.since or ws + timedelta(days=6) > plan.until,
            "questions": int(w.get(M_QUESTIONS, 0)), "checked_all": int(w.get(M_CHECKED_ALL, 0)),
            "judge_checked": int(w.get(M_JUDGE_CHECKED, 0)), "degraded": int(w.get(M_DEGRADED, 0)),
            "below_min": int(w.get(M_BELOW_MIN, 0)),
            "avg_score": round(w[M_SCORE_SUM] / n, 4) if n else None, "score_n": n,
            "likes": int(w.get(M_FEEDBACK_LIKE, 0)), "dislikes": int(w.get(M_FEEDBACK_DISLIKE, 0)),
        })
    checked_all = sum(x["checked_all"] for x in out)
    judge_checked = sum(x["judge_checked"] for x in out)
    return {"range": range_info(plan, params), "judge_model": params.judge_model,
            "faithfulness_min": params.faithfulness_min, "other_judge_checked": max(0, checked_all - judge_checked),
            "weeks": out}


async def _report_labels(session, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = (await session.execute(text(
        "SELECT id::text AS id, COALESCE(NULLIF(title, ''), file_name) AS label FROM research.research_report "
        "WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})).all()
    return {r.id: r.label for r in rows}


async def _hash_labels(session, hashes: list[str]) -> dict[str, str]:
    if not hashes:
        return {}
    rows = (await session.execute(text(
        "SELECT file_hash, COALESCE(NULLIF(title, ''), file_name) AS label FROM research.research_report "
        "WHERE file_hash = ANY(CAST(:h AS text[]))"), {"h": hashes})).all()
    return {r.file_hash: r.label for r in rows}


async def _target_labels(session, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = (await session.execute(text(
        "SELECT DISTINCT ON (stock_code) stock_code, company_name FROM research.research_report "
        "WHERE stock_code = ANY(CAST(:c AS text[])) AND company_name IS NOT NULL AND company_name <> '' "
        "ORDER BY stock_code, report_date DESC NULLS LAST"), {"c": codes})).all()
    return {r.stock_code: r.company_name for r in rows}


def _visible_keys(merged: dict, k: int, limit: int) -> list[str]:
    """只替「會顯示」的鍵查標籤：被抑制的鍵連查都不查。"""
    keys = [key for key, (_v, u) in merged.items() if not is_suppressed(u, k)]
    keys.sort(key=lambda key: (-merged[key][0], key))
    return keys[:limit]


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


async def top(session, plan: Plan, params: Params, limit: int) -> dict:
    """熱門標的、研報、市場（問答引用）與閱讀、原檔、搜尋市場（usage_daily）。全部受 k 門檻約束。"""
    k = params.min_users
    daily, _rolled = await _daily_rows(session, plan, params, ("hot.",), ())
    parts = list(daily)
    if plan.live:
        parts.extend(await hot_metrics(session, *plan.live, params, by_day=False))

    def merged_for(metric):
        return merge_cells((r.dim, r.value, r.users) for r in parts if r.metric == metric)

    m_targets, m_reports, m_markets = merged_for(M_HOT_TARGET), merged_for(M_HOT_REPORT), merged_for(M_HOT_MARKET)
    m_reading = await usage_subjects(session, plan.since, plan.until, "reading")
    m_file = await usage_subjects(session, plan.since, plan.until, "report_file")
    m_search = await usage_subjects(session, plan.since, plan.until, "search")

    report_ids = [x for x in _visible_keys(m_reports, k, limit) + _visible_keys(m_file, k, limit)
                  if _UUID_RE.fullmatch(x)]
    report_labels = await _report_labels(session, sorted(set(report_ids)))
    hash_labels = await _hash_labels(session, _visible_keys(m_reading, k, limit))
    target_labels = await _target_labels(session, _visible_keys(m_targets, k, limit))
    return {
        "range": range_info(plan, params), "limit": limit,
        "targets": top_list(m_targets, k=k, limit=limit, open_vocab=True, labels=target_labels),
        "reports": top_list(m_reports, k=k, limit=limit, open_vocab=True, labels=report_labels),
        "markets": top_list(m_markets, k=k, limit=limit, open_vocab=False, labels=MARKET_DISPLAY,
                            vocabulary=MARKETS),
        "reading": top_list(m_reading, k=k, limit=limit, open_vocab=True, labels=hash_labels),
        "report_file": top_list(m_file, k=k, limit=limit, open_vocab=True, labels=report_labels),
        "search_markets": top_list(m_search, k=k, limit=limit, open_vocab=False, labels=MARKET_DISPLAY,
                                   vocabulary=MARKETS),
    }


_WEEK = f"date_trunc('week', {{col}} AT TIME ZONE '{TZ_NAME}')::date"

_OPS_SQL = f"""
SELECT 'received' AS k, {_WEEK.format(col='uploaded_at')} AS w, count(*) AS n FROM research.report_upload
WHERE uploaded_at >= :start_ts AND uploaded_at < :end_ts GROUP BY 2
UNION ALL
SELECT 'published', {_WEEK.format(col='decided_at')}, count(*) FROM research.report_upload
WHERE state = 'published' AND decided_at >= :start_ts AND decided_at < :end_ts GROUP BY 2
UNION ALL
SELECT 'rejected', {_WEEK.format(col='decided_at')}, count(*) FROM research.report_upload
WHERE state = 'rejected' AND decided_at >= :start_ts AND decided_at < :end_ts GROUP BY 2
UNION ALL
SELECT 'infected', {_WEEK.format(col='state_changed_at')}, count(*) FROM research.report_upload
WHERE state = 'infected' AND state_changed_at >= :start_ts AND state_changed_at < :end_ts GROUP BY 2
UNION ALL
SELECT 'failed', {_WEEK.format(col='state_changed_at')}, count(*) FROM research.report_upload
WHERE state IN ('failed', 'blocked') AND state_changed_at >= :start_ts AND state_changed_at < :end_ts GROUP BY 2
UNION ALL
SELECT 'audit:' || action, {_WEEK.format(col='created_at')}, count(*) FROM research.admin_audit_log
WHERE action = ANY(CAST(:actions AS text[])) AND created_at >= :start_ts AND created_at < :end_ts GROUP BY 1, 2
"""

_OPS_FIELDS = {"received": "uploads_received", "published": "uploads_published", "rejected": "uploads_rejected",
               "infected": "uploads_infected", "failed": "uploads_failed", "audit:review.update": "reviews",
               "audit:qa_content.read": "qa_content_reads"}


async def operations(session, plan: Plan, params: Params) -> dict:
    """上傳與審核量（週）＋範圍內各稽核 action 的次數。兩張表都永久保存，整段即時查。總量，不設門檻。"""
    bind = {"start_ts": day_start(plan.since), "end_ts": day_start(plan.until + timedelta(days=1)),
            "actions": list(AUDIT_ACTIONS)}
    rows = (await session.execute(text(_OPS_SQL), bind)).all()
    weeks: dict[date, dict[str, int]] = {week_start(d): {} for d in _days(plan.since, plan.until)}
    actions = dict.fromkeys(AUDIT_ACTIONS, 0)
    for r in rows:
        w = weeks.setdefault(r.w, {})
        w[r.k] = w.get(r.k, 0) + int(r.n)
        if r.k.startswith("audit:"):
            actions[r.k[len("audit:"):]] += int(r.n)
    out = []
    for ws in sorted(weeks):
        item = {"week_start": ws.isoformat(), "partial": ws < plan.since or ws + timedelta(days=6) > plan.until}
        item.update({field: weeks[ws].get(k, 0) for k, field in _OPS_FIELDS.items()})
        out.append(item)
    return {"range": range_info(plan, params), "weeks": out,
            "audit_actions": [{"action": a, "count": n} for a, n in actions.items()]}
