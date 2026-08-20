"""延遲／路由／標的覆蓋率的基準量測（唯讀，零 LLM、零寫入）。

存在理由：2026-08-17 的四項目標路線圖把「取得基準」列為第 0 步——四項裡有三項的
優先序取決於數字，而那些數字多數只存在於程式碼註解裡的點狀實測（`app/config.py:201`
的 rerank 50 對 ~34s、`app/services/answer.py:2006` 的檢索 ~48s）。**延遲是例外**：
`scripts/analyze_qa_log.py` 已經會算 latency_ms／thinking_ms 的分位數，本腳本不是
它們的唯一來源——差異見下方「與既有工具的分工」。路由分佈與標的覆蓋率則確實完全
沒有既有工具在算。

三份報表：
  1. qa_log 的 latency_ms / thinking_ms 分佈（p50/p95/p99/max）
  2. qa_log 的路由分佈（filters->>'path' × filters->>'decided_by'）
     ——`fail_open` 的佔比即分類器失敗率，那個數字現在完全不可觀測。
  3. research_report 的 stock_targets / stock_code 覆蓋率（目標 3 的可行性前提）

與既有工具的分工：`scripts/analyze_qa_log.py`（README 已收錄）也會算
latency_ms／thinking_ms 的分位數，兩者不是同一支工具的兩個入口，數字也**不可直接
互相比較**——(a) 那支報 p50/p90/p99，本腳本報 **p95**（路線圖 §4.1 要的是 p95，
那支沒有這個分位）；(b) 本腳本額外報 n／n_latency／n_thinking 三個母體大小，供讀者
自行判斷視窗夠不夠長；(c) 路由分佈與標的覆蓋率兩份報表是那支完全沒有的——分類器
fail_open 率與目標 3 的可行性前提。百分位集合不同代表兩邊的「尾端」數字不是同一個
統計量，未來若要合併兩支工具，先把分位數集合對齊，不要假設它們現在就是一回事。

刻意不做：
  - 不寫任何表、不呼叫 LLM、不載入 embedding 模型。
  - 不設門檻、不告警。這是一次性量測工具，不是偵測器——停更偵測是
    `scripts/check_batch_freshness.py` 的職責，兩者不合流。

用法：
  uv run python scripts/measure_baseline.py
  uv run python scripts/measure_baseline.py --days 90
  uv run python scripts/measure_baseline.py --json

退出碼：0＝完成；2＝DB 不可用（與 check_batch_freshness.py 同慣例，處置不同故分流）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

EXIT_OK = 0
EXIT_UNKNOWN = 2

# percentile_disc 是 ordered-set aggregate，會略過 NULL 輸入列——thinking_ms 在舊列
# 上可能是 NULL（該欄是後來補的），故延遲與思考時間各自的母體大小不同，兩者都要報。
LATENCY_SQL = """
SELECT
  count(*)                                                    AS n,
  count(latency_ms)                                           AS n_latency,
  count(thinking_ms)                                          AS n_thinking,
  percentile_disc(0.5)  WITHIN GROUP (ORDER BY latency_ms)    AS latency_p50,
  percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)    AS latency_p95,
  percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)    AS latency_p99,
  max(latency_ms)                                             AS latency_max,
  percentile_disc(0.5)  WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p50,
  percentile_disc(0.95) WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p95,
  percentile_disc(0.99) WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p99,
  max(thinking_ms)                                            AS thinking_max
FROM research.qa_log
WHERE created_at >= now() - make_interval(days => :days)
"""

# path / decided_by 皆為 M4 之後才寫入，舊列沒有這兩個鍵 → COALESCE 成 '(none)'，
# 讓「有多少列根本沒有路由遙測」自己現形，而不是被靜默排除在分母之外。
ROUTE_SQL = """
SELECT
  COALESCE(filters->>'path', '(none)')        AS path,
  COALESCE(filters->>'decided_by', '(none)')  AS decided_by,
  count(*)                                    AS n
FROM research.qa_log
WHERE created_at >= now() - make_interval(days => :days)
GROUP BY 1, 2
ORDER BY 3 DESC, 1, 2
"""

# 母體用 is_research（該欄已收斂為 NOT NULL DEFAULT true），與檢索母體一致。
# stock_code 是純量欄位、stock_targets 是陣列，兩者互不覆蓋——目標 3 的召回路徑
# 要同時吃這兩欄，故分開報並另報聯集。
COVERAGE_SQL = """
SELECT
  count(*)                                                              AS total,
  count(*) FILTER (
    WHERE stock_targets IS NOT NULL AND cardinality(stock_targets) > 0
  )                                                                     AS with_targets,
  count(*) FILTER (
    WHERE stock_code IS NOT NULL AND stock_code <> ''
  )                                                                     AS with_stock_code,
  count(*) FILTER (
    WHERE (stock_targets IS NOT NULL AND cardinality(stock_targets) > 0)
       OR (stock_code IS NOT NULL AND stock_code <> '')
  )                                                                     AS with_any
FROM research.research_report
WHERE is_research
"""


def build_coverage(
    total: int, with_targets: int, with_stock_code: int, with_any: int
) -> dict:
    """覆蓋率百分比（純函式）。

    total=0 時一律回 0.0——空語料是新機器／重建中的正常狀態，讓量測腳本除零炸掉
    等於「拿來診斷的工具自己成為故障源」。
    """
    def pct(n: int) -> float:
        return round(100.0 * n / total, 2) if total else 0.0

    return {
        "total": total,
        "with_targets": with_targets,
        "with_stock_code": with_stock_code,
        "with_any": with_any,
        "pct_targets": pct(with_targets),
        "pct_stock_code": pct(with_stock_code),
        "pct_any": pct(with_any),
    }


async def collect(days: int) -> dict:
    """跑三支查詢並組成報表 dict。DB 不可用時例外原樣上拋，由 main 轉成 rc=2。"""
    from app.services.db import SessionFactory

    async with SessionFactory() as session:
        lat = (await session.execute(text(LATENCY_SQL), {"days": days})).mappings().first()
        routes = (await session.execute(text(ROUTE_SQL), {"days": days})).mappings().all()
        cov = (await session.execute(text(COVERAGE_SQL))).mappings().first()

    return {
        "window_days": days,
        "latency": dict(lat) if lat is not None else {},
        "routes": [dict(r) for r in routes],
        "coverage": build_coverage(
            int(cov["total"] or 0),
            int(cov["with_targets"] or 0),
            int(cov["with_stock_code"] or 0),
            int(cov["with_any"] or 0),
        ),
    }


def render(report: dict) -> str:
    """報表 → 人可讀文字（純函式）。"""
    lat = report["latency"]
    lines = [
        f"== 問答延遲（近 {report['window_days']} 天）==",
        f"  樣本數 n={lat.get('n')} "
        f"(latency 非空 {lat.get('n_latency')}／thinking 非空 {lat.get('n_thinking')})",
        f"  latency_ms   p50={lat.get('latency_p50')} p95={lat.get('latency_p95')} "
        f"p99={lat.get('latency_p99')} max={lat.get('latency_max')}",
        f"  thinking_ms  p50={lat.get('thinking_p50')} p95={lat.get('thinking_p95')} "
        f"p99={lat.get('thinking_p99')} max={lat.get('thinking_max')}",
        "",
        "== 路由分佈 ==",
    ]
    if not report["routes"]:
        lines.append("  （窗期內無 qa_log 列）")
    for r in report["routes"]:
        lines.append(f"  {r['path']:<16} decided_by={r['decided_by']:<12} n={r['n']}")

    c = report["coverage"]
    lines += [
        "",
        "== 標的覆蓋率（is_research 母體）==",
        f"  總篇數 {c['total']}",
        f"  stock_targets 非空  {c['with_targets']} ({c['pct_targets']}%)",
        f"  stock_code    非空  {c['with_stock_code']} ({c['pct_stock_code']}%)",
        f"  兩者聯集            {c['with_any']} ({c['pct_any']}%)",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="延遲／路由／標的覆蓋率基準量測（唯讀）")
    # 預設 365 不是 30：spec 記錄的生產量約 0.7 題／天，30 天窗期只有約 21 列，
    # percentile_disc(0.95) 與 percentile_disc(0.99) 在那個列數下會一起落到
    # max——印出來像三個獨立數字，其實是同一列被複製了三次。n／n_latency／
    # n_thinking 三個母體大小仍會印出來，讀者可自行判斷這個視窗夠不夠長。
    parser.add_argument("--days", type=int, default=365, help="qa_log 回溯窗期（天）")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 而非文字")
    args = parser.parse_args()

    try:
        report = asyncio.run(collect(args.days))
    except Exception as exc:  # DB 不可用與查詢失敗都走這裡；訊息原樣印出供診斷
        # --json 時失敗也要吐 JSON（比照 check_batch_freshness.py 的 payload 形狀）：
        # 否則 `| jq` 包裝在 rc=2 時只拿到空 stdin，連錯誤訊息都解析不到。
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"量測失敗（DB 不可用？）：{payload['error']}", file=sys.stderr)
        return EXIT_UNKNOWN

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(report))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
