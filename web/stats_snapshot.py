# web/stats_snapshot.py
"""語料與查核統計的 DB 快照：/api/stats、/api/progress 與 /api/review/judge-scale 共用。

從 web/routers/monitor.py 抽出（issue #348）：待複核頁的判定尺端點在 web/routers/review.py，
而 router 之間不互相 import，所以快照與它的快取放在 router 之外的這裡。

**三個端點共用 `_DB_STATS_CACHE`**：都經 `db_stats_snapshot()` 取快照，TTL 內只打一次 DB
（tests/test_server_stats.py 的查詢次數斷言守此）。快取是模組級狀態，任何讀者另存一份或另寫
查詢，TTL 去重就失效。各端點只挑自己需要的鍵回給前端：`/api/stats` 只給語料分面、
`/api/review/judge-scale` 只給判定尺，完整快照只出現在 `/api/progress`（管理員＋`ops.read`）。

存取方式比照 `web/deps.py`：router 寫 `from web import stats_snapshot` 再呼叫
`stats_snapshot.db_stats_snapshot()`，測試以 `patch.object(stats_snapshot, "db_stats_snapshot", ...)`
注入假快照。`from web.stats_snapshot import db_stats_snapshot` 會在 import 當下把函式複製走，
patch 會靜默落空。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text

from app.config import get_settings
from app.services.extraction import EXTRACTION_VERSION
from app.services.filename import source_display
from app.services.judge_schema import CURRENT_JUDGE_SQL
from web import deps

logger = logging.getLogger(__name__)

# 「待複核」的分數門檻與離線評測（scripts/eval_faithfulness.py）共用 FAITHFULNESS_MIN，
# 避免監控頁自成一套標準。
_FAITHFULNESS_MIN = get_settings().faithfulness_min
# 現行生產 judge。分數類統計只計它量的列（app/services/judge_schema.py），與待複核佇列、
# scripts/eval_faithfulness.py 同一條規則。
_JUDGE_MODEL = get_settings().faithfulness_model

# M8 查核統計的欄位：(回應鍵, SQL 聚合)。抽成常數讓測試逐欄核對過濾條件——只斷言
# 「整條 SQL 有 :judge_model」的話，漏掉任何一欄的條件都照樣綠。
#
# 兩種語意刻意分開：
# - **覆蓋率類**（total、checked、latest）計所有 judge：換 judge 之後「有沒有在查」這件事
#   沒有變，主數字不能因為換尺就驟降，看起來像抽查路徑崩了。
# - **分數類**（judge_checked、degraded、below_min、avg_score、avg_n、judge_since）只計現行
#   judge（CURRENT_JUDGE_SQL，缺 judge_model 的舊列視為 claude-haiku-4-5）：兩把尺的分數混著
#   平均、混著排待複核就沒有意義。avg_score 與它的樣本數 avg_n 另排除 degraded 列（沒有分數）。
# other_judge_checked＝checked − judge_checked，卡片據此說明有多少筆不計入分數類統計。
_EVAL_SCORE_SQL = (
    "CASE WHEN jsonb_typeof(evaluation->'faithfulness_score') = 'number' "
    "THEN (evaluation->>'faithfulness_score')::float END"
)
_EVAL_NOT_DEGRADED_SQL = "evaluation->'degraded' IS DISTINCT FROM 'true'::jsonb"
_EVAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("total", "count(*)"),
    ("checked", "count(evaluation)"),
    ("judge_checked", f"count(evaluation) FILTER (WHERE {CURRENT_JUDGE_SQL})"),
    ("degraded", f"count(*) FILTER (WHERE evaluation->'degraded' = 'true'::jsonb AND {CURRENT_JUDGE_SQL})"),
    ("below_min", f"count(*) FILTER (WHERE ({_EVAL_SCORE_SQL}) < :fmin AND {CURRENT_JUDGE_SQL})"),
    ("avg_score", f"avg({_EVAL_SCORE_SQL}) FILTER (WHERE {CURRENT_JUDGE_SQL} AND {_EVAL_NOT_DEGRADED_SQL})"),
    ("avg_n", f"count({_EVAL_SCORE_SQL}) FILTER (WHERE {CURRENT_JUDGE_SQL} AND {_EVAL_NOT_DEGRADED_SQL})"),
    ("latest", "max(created_at::date) FILTER (WHERE evaluation IS NOT NULL)"),
    ("judge_since", f"min(created_at::date) FILTER (WHERE evaluation IS NOT NULL AND {CURRENT_JUDGE_SQL})"),
    ("other_judge_checked", f"count(evaluation) FILTER (WHERE NOT ({CURRENT_JUDGE_SQL}))"),
)
# 只計現行 judge 的欄位（測試逐一核對它們的 SQL 帶 CURRENT_JUDGE_SQL、其餘不帶）。
_EVAL_CURRENT_JUDGE_KEYS = frozenset(
    {"judge_checked", "degraded", "below_min", "avg_score", "avg_n", "judge_since"}
)

# 15 秒（原 5 秒）：前端每 5 秒輪詢，這一塊是 9 條 DB 查詢，其中市場分佈與商品類型
# 是全表 GROUP BY。拉到 15 秒讓 DB 負載降為三分之一，而 `ts` 欄與 runtime 區塊仍每次
# 更新，觀感幾乎無差——這些數字本來就是「幾萬篇語料的累計量」，秒級沒有意義。
DB_STATS_CACHE_TTL_SECONDS = 15.0
_DB_STATS_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}


def reset_cache() -> None:
    """清空 DB 快照的快取。

    **測試用**：模組級快取會跨測試存活，A 測試 patch 掉 `deps.SessionFactory` 後取到的
    快照會留給 B 測試。由 `tests/conftest.py` 的 autouse fixture 每題前後各清一次。
    """
    _DB_STATS_CACHE["data"] = None
    _DB_STATS_CACHE["expires_at"] = 0.0

async def _fetch_db_stats_snapshot() -> dict:
    async with deps.SessionFactory() as session:
        total_reports = (
            await session.execute(text("SELECT count(*) FROM research.research_report"))
        ).scalar_one()
        total_chunks = (
            await session.execute(text("SELECT count(*) FROM research.report_chunk"))
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT market, count(*) c FROM research.research_report "
                    "GROUP BY market ORDER BY c DESC"
                )
            )
        ).all()
        instr_rows = (
            await session.execute(
                text(
                    "SELECT unnest(instrument_types) it, count(*) c "
                    "FROM research.research_report "
                    "WHERE instrument_types IS NOT NULL "
                    "GROUP BY it ORDER BY c DESC"
                )
            )
        ).all()
        type_rows = (
            await session.execute(
                text(
                    "SELECT report_type, count(*) c "
                    "FROM research.research_report "
                    "WHERE report_type IS NOT NULL "
                    "GROUP BY report_type ORDER BY c DESC"
                )
            )
        ).all()
        # 券商分佈。**刻意不濾 is_research／full_text**，與上面的 market/instrument
        # 分面一致——三者的分母都要等於 db.reports，否則監控頁上兩張分佈卡的百分比
        # 會各自對到不同的總數，而且沒有任何地方看得出來。
        #
        # 一併取 max(report_date) 是因為這頁是「導入」監控：光有篇數看不出某家券商
        # 是不是早就停止供稿（實測 masterlink 3,814 篇、最新一篇停在一年前）。
        # NULL source 那一列刻意保留：未辨識券商本身就是導入品質的訊號。
        source_rows = (
            await session.execute(
                text(
                    "SELECT source, count(*) c, max(report_date) latest "
                    "FROM research.research_report "
                    "GROUP BY source ORDER BY c DESC, source"
                )
            )
        ).all()
        s_done, s_total = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE summary IS NOT NULL), count(*) "
                    "FROM research.research_report "
                    "WHERE full_text IS NOT NULL AND is_research IS NOT FALSE"
                )
            )
        ).first()
        # 派生資產的新鮮度（近 30 天窗口）。
        #
        # **為什麼是近 30 天而非全表**：takeaway/signal 都刻意只跑子集（前者近 90 天、
        # 後者「子集先行」），全表覆蓋率永遠很低、無法當訊號。真正要偵測的是「批次
        # 停跑」——那會表現為近期窗口的覆蓋率驟降與 latest 日期不再前進。
        #
        # 先前這裡**只量 summary，而 summary 恰好是唯一有排程的**；真正在腐化的兩張表
        # （2026-07 實測 takeaway 停更 8 天、signal 停更 12 天）零量測。缺席時閱讀頁
        # 整區不進 DOM（優雅降級），所以症狀是「最新研報靜默少一個功能」，
        # 永遠不會有人回報。
        tk_done, tk_total, tk_latest = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE t.report_id IS NOT NULL), count(*), "
                    "       (SELECT max(created_at)::date FROM research.report_takeaway) "
                    "FROM research.research_report r "
                    "LEFT JOIN (SELECT DISTINCT report_id FROM research.report_takeaway) t "
                    "       ON t.report_id = r.id "
                    "WHERE r.report_date > current_date - 30 "
                    "  AND r.full_text IS NOT NULL AND r.is_research IS NOT FALSE"
                )
            )
        ).first()
        sig_done, sig_total, sig_latest = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE s.report_id IS NOT NULL), count(*), "
                    "       (SELECT max(created_at)::date FROM research.report_signal) "
                    "FROM research.research_report r "
                    "LEFT JOIN (SELECT DISTINCT report_id FROM research.report_signal) s "
                    "       ON s.report_id = r.id "
                    "WHERE r.report_date > current_date - 30 "
                    "  AND r.full_text IS NOT NULL AND r.is_research IS NOT FALSE"
                )
            )
        ).first()

        # M8 忠實度查核（qa_log.evaluation）的健康度。
        #
        # **這裡看的不是覆蓋率**：問答端有取樣率、且只查含金融數字的回答，
        # 所以「有 evaluation 的比例」天生就不該是 100%，拿它當訊號只會
        # 一直亮紅燈（同 takeaway/signal 的教訓）。真正的訊號是另外三個：
        #   degraded  judge 異常時 fail-open 會寫 degraded=true、分數留 None。
        #             這條若衝高，代表查核**還在跑但全部沒查到東西**——最像
        #             「一切正常」的故障樣態。
        #   below_min 分數低於門檻＝該筆待人工複核（實測有 0.208 這種數字）。
        #   latest    最後一次真的查核的日期；不再前進＝整條路徑停了。
        #
        # jsonb 一律用 jsonb_typeof 過濾後才 cast：畸形一列就讓監控頁 500，
        # 而監控頁恰恰是故障時唯一還想得到要打開的東西。
        #
        # 哪些欄位只計現行 judge、哪些計所有 judge：見模組頂端的 _EVAL_COLUMNS。
        # judge_since 是窗期內現行 judge 最早的一筆——切換後頭幾天樣本很小，卡片要讓人看得出來。
        _eval_cols = ", ".join(sql for _key, sql in _EVAL_COLUMNS)
        eval_rows = (
            await session.execute(
                text(
                    f"SELECT 'qa' kind, {_eval_cols} FROM research.qa_log "
                    "WHERE created_at > now() - interval '30 days'"
                ),
                {"fmin": _FAITHFULNESS_MIN, "judge_model": _JUDGE_MODEL},
            )
        ).all()

        # 抽取品質與回填進度（E1，docs/EXTRACTION.md §7）。**單一查詢**，
        # 三段 UNION ALL 併成 (kind, key, count) 列——與上面 M8 那條同一個理由：stats 與
        # progress 共用同一份快照、TTL 內只打一次 DB，查詢數是測試釘住的契約。
        #
        # **放在最後、且包 try**：schema 還沒套（E1b 合併後到 make schema 之間）時
        # extraction_log 不存在，這條會炸；監控頁恰恰是故障時唯一還想打開的東西，
        # 所以缺表只讓這一張卡降級（extraction=None），不讓整頁 500。放最後是因為
        # 例外會讓交易進入 aborted 狀態，後面的查詢全部跟著失敗。
        extraction_rows: list | None
        try:
            extraction_rows = (
                await session.execute(
                    text(
                        "SELECT 'version' kind, coalesce(extraction_version, '(unknown)') k, count(*) "
                        "FROM research.research_report GROUP BY 2 "
                        "UNION ALL "
                        "SELECT 'stopped_at', stopped_at, count(*) FROM research.extraction_log GROUP BY 2 "
                        "UNION ALL "
                        "SELECT 'flag', 'needs_review', count(*) FILTER (WHERE needs_review) "
                        "FROM research.research_report "
                        "UNION ALL "
                        "SELECT 'flag', 'pages_failed', "
                        "count(*) FILTER (WHERE coalesce(array_length(pages_failed, 1), 0) > 0) "
                        "FROM research.research_report "
                        "UNION ALL "
                        "SELECT 'log_latest', coalesce(max(updated_at)::date::text, ''), 0 "
                        "FROM research.extraction_log"
                    )
                )
            ).all()
        except Exception as exc:  # noqa: BLE001 — 缺表／缺欄：降級成 None，不讓整頁 500
            logger.warning("extraction stats unavailable (schema not applied?): %s", exc)
            await session.rollback()
            extraction_rows = None

    def _d(v) -> str | None:
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)

    extraction = _extraction_block(extraction_rows, int(total_reports))

    def _eval_block(row) -> dict:
        v = dict(zip((key for key, _sql in _EVAL_COLUMNS), row[1:]))
        return {
            # 覆蓋率類：所有 judge。
            "total": int(v["total"]),
            "checked": int(v["checked"]),
            "latest": _d(v["latest"]),
            # 分數類：只計現行 judge（judge_model）。
            "judge_model": _JUDGE_MODEL,
            "judge_checked": int(v["judge_checked"]),
            "degraded": int(v["degraded"]),
            "below_min": int(v["below_min"]),
            "avg_score": round(float(v["avg_score"]), 4) if v["avg_score"] is not None else None,
            "avg_n": int(v["avg_n"]),
            "judge_since": _d(v["judge_since"]),
            "other_judge_checked": int(v["other_judge_checked"]),
        }

    evals = {r[0]: _eval_block(r) for r in eval_rows}

    return {
        "total_reports": total_reports,
        "total_chunks": total_chunks,
        "markets": [{"market": m, "count": c} for m, c in rows],
        # display 在後端算：`source_display` 的對照表是 app/services/filename.py 的
        # 單一真相，檢索頁與閱讀頁也走它。搬一份到前端等於兩份會漂的字典。
        # 未收錄的 source 由 source_display 原樣回傳（不會變 None），NULL 才是 None。
        "sources": [
            {"source": s, "display": source_display(s), "count": int(c), "latest": _d(latest)}
            for s, c, latest in source_rows
        ],
        "instrument_types": [{"type": t, "count": c} for t, c in instr_rows],
        "report_types": [{"type": t, "count": c} for t, c in type_rows],
        "summary_done": int(s_done),
        "summary_total": int(s_total),
        # 近 30 天窗口的派生資產覆蓋率 + 全表最新產出日（批次停跑的偵測訊號）
        "takeaway_done_30d": int(tk_done),
        "takeaway_total_30d": int(tk_total),
        "takeaway_latest": _d(tk_latest),
        "signal_done_30d": int(sig_done),
        "signal_total_30d": int(sig_total),
        "signal_latest": _d(sig_latest),
        # M8 查核健康度（近 30 天）。
        "evaluation": {
            "qa": evals.get("qa"),
            "min_score": _FAITHFULNESS_MIN,
        },
        "extraction": extraction,
    }


def _extraction_block(rows: list | None, total_reports: int) -> dict | None:
    """(kind, key, count) 列 → 抽取品質區塊。rows 為 None（缺表）回 None，前端降級。

    `backfill` 的分母是 `research_report` 全表、分子是已達目標版本者：回填要跑
    十幾個晚上，這是唯一不用 SQL 就看得到進度的地方。`stopped_at` 分佈與
    `needs_review`／`pages_failed` 是 §2 目標 #1「靜默失敗歸零」的可見面。"""
    if rows is None:
        return None
    versions: dict[str, int] = {}
    stopped: dict[str, int] = {}
    flags: dict[str, int] = {}
    log_latest: str | None = None
    for kind, key, count in rows:
        if kind == "version":
            versions[str(key)] = int(count)
        elif kind == "stopped_at":
            stopped[str(key)] = int(count)
        elif kind == "flag":
            flags[str(key)] = int(count)
        elif kind == "log_latest":
            log_latest = str(key) or None
    done = versions.get(EXTRACTION_VERSION, 0)
    return {
        "target_version": EXTRACTION_VERSION,
        "versions": [
            {"version": v, "count": c} for v, c in sorted(versions.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "needs_review": flags.get("needs_review", 0),
        "pages_failed": flags.get("pages_failed", 0),
        "stopped_at": stopped,
        "log_latest": log_latest,
        "backfill": coverage_block(done, total_reports, log_latest),
    }


async def db_stats_snapshot() -> dict:
    now = time.monotonic()
    cached = _DB_STATS_CACHE.get("data")
    expires_at = float(_DB_STATS_CACHE.get("expires_at", 0.0) or 0.0)
    if cached is not None and now < expires_at:
        return cached  # type: ignore[return-value]
    data = await _fetch_db_stats_snapshot()
    _DB_STATS_CACHE["data"] = data
    _DB_STATS_CACHE["expires_at"] = now + DB_STATS_CACHE_TTL_SECONDS
    return data


def coverage_block(done: int, total: int, latest: str | None) -> dict:
    """近 30 天覆蓋率區塊；形狀比照既有的 summary（done/total/remaining/pct）另加 latest。

    抽取回填與 `/api/progress` 的 takeaway／signal 共用。它原本在 monitor router 裡，2026-07-28
    曾因為夾在 `@router.get` 與 handler 之間被裝飾成端點，`/api/progress` 對正常請求回 422；
    搬到這裡之後不再有這個風險，router 檔的同一條規則仍由 test_monitor_http 的 HTTP 層測試守。
    """
    done, total = int(done), int(total)
    return {
        "done": done,
        "total": total,
        "remaining": max(0, total - done),
        "pct": round(done / total * 100, 2) if total else 0.0,
        "latest": latest,
    }
