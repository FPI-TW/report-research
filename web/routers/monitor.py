# web/routers/monitor.py
"""監控資料 API：/api/stats（語料統計）與 /api/progress（匯入/標註即時進度）。

從 web/server.py 拆出（第三步）。兩條路由同源於 /app/monitor 儀表板。

**stats 與 progress 共用 _DB_STATS_CACHE**：兩者都經 _db_stats_snapshot 取語料
快照，TTL 內只打一次 DB。這是它們必須同模組的原因——快取是模組級狀態，拆到兩
個模組會分裂成兩份，TTL 去重失效（test_server_stats 的「6 次 execute」斷言即
守此）。

進度解析（log tail + /proc 掃描）是同步工作，progress 以 asyncio.to_thread 執行
_gather_runtime，不阻塞事件迴圈。

**三層 TTL 快取，各有不同的理由**（監控頁每 5 秒輪詢一次，所以每一項成本都會
乘上開著頁面的分頁數；TanStack Query 在視窗失焦時會停 interval，所以成立條件是
「監控頁開著且在前景」）：

  _DB_STATS_CACHE    10 條 DB 查詢。15 秒 ⇒ 每三次輪詢只打一次 DB。
  _RUNTIME_CACHE     整個 runtime 區塊（log tail + /proc + tag 檔數）。
  _TAG_COUNT_CACHE   `data/tags/` 的 scandir，**這裡真正的熱點**：本機實測
                     15,852 個檔、冷 412 ms／熱 117 ms，而三次 `_proc_alive`
                     加起來只 2.4 ms（架構檢視把成本歸給 `_proc_alive`，量下去
                     發現差 50 倍）。60 秒是安全的：全量標註只在初次建庫時跑，
                     那個場景下一分鐘的粒度完全夠用。

（/monitor、/help 的 302 轉址屬 SPA 導覽，不在此——留在 server.py。）
"""
import asyncio
import glob as _glob
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.services.extraction import EXTRACTION_VERSION
from app.services.filename import source_display
from web import auth, deps

logger = logging.getLogger(__name__)

router = APIRouter()

# 「待複核」的分數門檻沿用研報端的 REPORT_FAITHFULNESS_MIN，避免監控頁自成一套標準
# 而與實際觸發修正的門檻對不上。
_FAITHFULNESS_MIN = get_settings().report_faithfulness_min

# 15 秒（原 5 秒）：前端每 5 秒輪詢，這一塊是 9 條 DB 查詢，其中市場分佈與商品類型
# 是全表 GROUP BY。拉到 15 秒讓 DB 負載降為三分之一，而 `ts` 欄與 runtime 區塊仍每次
# 更新，觀感幾乎無差——這些數字本來就是「幾萬篇語料的累計量」，秒級沒有意義。
DB_STATS_CACHE_TTL_SECONDS = 15.0
_DB_STATS_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}

# runtime 區塊（log tail + /proc 掃描 + tag 檔數）。10 秒 ⇒ 兩次輪詢只掃一次。
RUNTIME_CACHE_TTL_SECONDS = 10.0
_RUNTIME_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}

# `data/tags/` 的 scandir（見模組 docstring 的實測數字）。
TAG_FILE_COUNT_TTL_SECONDS = 60.0
_TAG_COUNT_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}


def _cached(store: dict, ttl: float, produce):
    """單行程 TTL 快取。命中即回，否則呼叫 produce() 並記住。

    刻意不加鎖：`_gather_runtime` 跑在 to_thread，兩條執行緒同時撲空時最壞的後果
    是重算一次（dict 賦值在 GIL 下是原子的），加鎖換來的只是把那一次重算變成等待。
    """
    now = time.monotonic()
    if store.get("data") is not None and now < float(store.get("expires_at") or 0.0):
        return store["data"]
    data = produce()
    store["data"] = data
    store["expires_at"] = now + ttl
    return data


def reset_caches() -> None:
    """清空本模組所有 TTL 快取。

    **測試用**：模組級快取會跨測試存活，於是「A 測試 patch 掉 `_gather_runtime`
    後呼叫 progress」會把假值留給 B 測試（形狀相同時完全看不出來）。由
    `tests/conftest.py` 的 autouse fixture 每題前後各清一次。
    """
    for store in (_DB_STATS_CACHE, _RUNTIME_CACHE, _TAG_COUNT_CACHE):
        store["data"] = None
        store["expires_at"] = 0.0


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

        # M8 忠實度查核（qa_log.evaluation / report_doc.evaluation）的健康度。
        #
        # **這裡看的不是覆蓋率**：問答端有取樣率、且只查含金融數字的回答，研報端
        # 也可停用，所以「有 evaluation 的比例」天生就不該是 100%，拿它當訊號只會
        # 一直亮紅燈（同 takeaway/signal 的教訓）。真正的訊號是另外三個：
        #   degraded  judge 異常時 fail-open 會寫 degraded=true、分數留 None。
        #             這條若衝高，代表查核**還在跑但全部沒查到東西**——最像
        #             「一切正常」的故障樣態。
        #   below_min 分數低於門檻＝該筆待人工複核（實測有 0.208 這種數字）。
        #   latest    最後一次真的查核的日期；不再前進＝整條路徑停了。
        #
        # jsonb 一律用 jsonb_typeof 過濾後才 cast：畸形一列就讓監控頁 500，
        # 而監控頁恰恰是故障時唯一還想得到要打開的東西。
        _score = (
            "CASE WHEN jsonb_typeof(evaluation->'faithfulness_score') = 'number' "
            "THEN (evaluation->>'faithfulness_score')::float END"
        )
        _eval_cols = (
            "count(*), count(evaluation), "
            "count(*) FILTER (WHERE evaluation->'degraded' = 'true'::jsonb), "
            f"count(*) FILTER (WHERE ({_score}) < :fmin), "
            f"avg({_score}), "
            "max(created_at::date) FILTER (WHERE evaluation IS NOT NULL)"
        )
        eval_rows = (
            await session.execute(
                text(
                    f"SELECT 'qa' kind, {_eval_cols} FROM research.qa_log "
                    "WHERE created_at > now() - interval '30 days' "
                    "UNION ALL "
                    f"SELECT 'report', {_eval_cols} FROM research.report_doc "
                    "WHERE created_at > now() - interval '30 days'"
                ),
                {"fmin": _FAITHFULNESS_MIN},
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

    evals = {
        r[0]: {
            "total": int(r[1]),
            "checked": int(r[2]),
            "degraded": int(r[3]),
            "below_min": int(r[4]),
            "avg_score": round(float(r[5]), 4) if r[5] is not None else None,
            "latest": _d(r[6]),
        }
        for r in eval_rows
    }

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
        # M8 查核健康度（近 30 天），依來源分開：問答與研報的閘門與取樣各自不同。
        "evaluation": {
            "qa": evals.get("qa"),
            "report": evals.get("report"),
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
        "backfill": _coverage_block(done, total_reports, log_latest),
    }


async def _db_stats_snapshot() -> dict:
    now = time.monotonic()
    cached = _DB_STATS_CACHE.get("data")
    expires_at = float(_DB_STATS_CACHE.get("expires_at", 0.0) or 0.0)
    if cached is not None and now < expires_at:
        return cached  # type: ignore[return-value]
    data = await _fetch_db_stats_snapshot()
    _DB_STATS_CACHE["data"] = data
    _DB_STATS_CACHE["expires_at"] = now + DB_STATS_CACHE_TTL_SECONDS
    return data


@router.get("/api/stats")
async def stats():
    snapshot = await _db_stats_snapshot()
    return {
        "total_reports": snapshot["total_reports"],
        "total_chunks": snapshot["total_chunks"],
        "markets": snapshot["markets"],
        "instrument_types": snapshot["instrument_types"],
        "report_types": snapshot["report_types"],
        "username": auth.ACCESS_USERNAME,
    }


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_TAG_RE = re.compile(r"(\d+)/(\d+)\s+ok\+skip=(\d+)\s+fail=(\d+)")
_ING_RE = re.compile(r"ingested=(\d+)\s+chunks=(\d+)\s+fail=(\d+)")
_ORCH_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+===\s+resume\s+"
    r"(?P<state>start|done)(?:\s+\(pid=(?P<pid>\d+)\))?\s+===$"
)


def _latest_log(pattern: str) -> Path | None:
    files = sorted(DATA_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _tail_text(path: Path, nbytes: int = 65536) -> str:
    """讀檔尾數十 KB，避免長 log 整檔載入。"""
    try:
        with open(path, "rb") as fh:
            try:
                fh.seek(-nbytes, os.SEEK_END)
            except OSError:
                fh.seek(0)
            return fh.read().decode("utf-8", "ignore")
    except OSError:
        return ""


TAGS_DIR = DATA_DIR / "tags"


def _scan_tag_files() -> int:
    try:
        return sum(1 for e in os.scandir(TAGS_DIR) if e.name.endswith(".json"))
    except OSError:
        return 0


def _count_tag_files() -> int:
    """即時 tag 檔數：每完成一個標註就 +1，比 log（每 100 筆才印）連續細緻。

    帶 60 秒快取，因為這支是監控頁最貴的一件事——實測 15,852 個檔、冷 412 ms／
    熱 117 ms（同一輪 runtime 裡三次 `_proc_alive` 合計只 2.4 ms）。粒度從 5 秒
    退到 60 秒的代價只影響「全量標註進行中」這一種場景，而那條管線只在初次建庫
    或補跑歷史時才啟動，一分鐘一格已足夠。
    """
    return _cached(_TAG_COUNT_CACHE, TAG_FILE_COUNT_TTL_SECONDS, _scan_tag_files)


def _tag_progress() -> dict | None:
    f = _latest_log("tag_run_*.log")
    if not f:
        return None
    tail = _tail_text(f)
    last = None
    for last in _TAG_RE.finditer(tail):
        pass
    m_total = re.search(r"candidates:\s*(\d+)", tail)
    total = int(m_total.group(1)) if m_total else (int(last.group(2)) if last else 0)
    if not total:
        return None
    # done 以「即時 tag 檔案數」為準（每秒數個、連續成長）；log 的掃描位置會把已標的重數
    done = min(_count_tag_files(), total)
    # 未標數＝缺 tag 檔者（標完即 0）。不取 log 的歷史 fail，避免事後手動補標後仍顯示舊值
    untagged = max(0, total - done)
    return {
        "done": done,
        "total": total,
        "fail": untagged,
        "pct": round(done / total * 100, 2) if total else 0.0,
    }


def _ingest_progress() -> dict | None:
    f = _latest_log("ingest_run_*.log")
    if not f:
        return None
    last = None
    for last in _ING_RE.finditer(_tail_text(f)):
        pass
    if last is None:
        return {"ingested": 0, "chunks": 0, "fail": 0}
    return {
        "ingested": int(last.group(1)),
        "chunks": int(last.group(2)),
        "fail": int(last.group(3)),
    }


def _proc_alive(needle: str) -> bool:
    """掃 /proc 比對 cmdline，判斷背景管線是否在跑（不依賴 docker / pgrep）。"""
    target = needle.encode()
    for p in _glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as fh:
                if target in fh.read().replace(b"\x00", b" "):
                    return True
        except OSError:
            continue
    return False


def _orchestrator_last() -> str | None:
    f = _latest_log("resume_orchestrator_*.log")
    if not f:
        return None
    lines = [ln for ln in _tail_text(f).splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _parse_orchestrator_entry(line: str | None) -> dict | None:
    raw = (line or "").strip()
    if not raw:
        return None
    m = _ORCH_RE.match(raw)
    if m:
        state = m.group("state")
        return {
            "raw": raw,
            "timestamp": m.group("ts"),
            "status": "running" if state == "start" else "done",
            "label": "編排器執行中" if state == "start" else "編排器已完成",
        }
    return {
        "raw": raw,
        "timestamp": None,
        "status": "unknown",
        "label": "編排器狀態",
    }


# sync 殼的 log() 每行固定是 `[YYYY-MM-DD HH:MM:SS] 訊息`
_SYNC_TS_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s")
_SYNC_START = "=== sync start"
_SYNC_DONE = "=== sync done ==="


def _parse_sync_entry(lines: list[str]) -> dict | None:
    """把 `data/sync_run_<date>.log` 的尾巴判成一筆狀態（純函式，便於測試）。

    **完成判定看的是「最後一個標記行是 start 還是 done」**，不是「整檔有沒有出現
    done」：log 是每日一檔，一天內會被 8 輪同步接續 append，看整檔等於第一輪跑完
    之後永遠顯示「已完成」。用「最後一個標記」而非「最後一個 start 之後有無 done」
    是為了對 tail 截斷免疫——只讀檔尾數十 KB 時 start 那行可能已經被切掉。
    """
    rows = [ln.strip() for ln in lines if ln.strip()]
    if not rows:
        return None
    markers = [ln for ln in rows if _SYNC_START in ln or _SYNC_DONE in ln]
    if not markers:
        status, label = "unknown", "同步狀態"
    elif _SYNC_DONE in markers[-1]:
        status, label = "done", "同步已完成"
    else:
        status, label = "running", "同步執行中"
    last = rows[-1]
    m = _SYNC_TS_RE.match(last)
    return {
        "raw": last,
        "timestamp": m.group("ts") if m else None,
        "status": status,
        "label": label,
    }


def _sync_progress() -> dict | None:
    """排程同步（`scripts/sync_new_reports.sh`，每 3 小時）的可見度。

    為什麼需要它：runtime 區塊原本只認 `tag_run_*.log` 與 `ingest_run_*.log`，
    而那兩支全量腳本**只在初次建庫或補跑歷史時才跑**——生產實際的入庫路徑是
    sync 這條鏈，它在監控頁上一直是零可見度。所以現況是「runtime 區塊全 null
    ＋ 三個布林」，而唯一真的在跑的那條看不到。
    """
    f = _latest_log("sync_run_*.log")
    if f is None:
        return None
    return _parse_sync_entry(_tail_text(f).splitlines())


UNIT_FAILURES_LOG = DATA_DIR / "unit_failures.log"
# 每筆紀錄都夾帶失敗當下的 journal 尾巴（alert.sh 取 30 行、sync 殼取 20 行），
# 所以幾筆就是數十 KB。tail 給小了「最近幾筆」會只剩一筆。
UNIT_FAILURES_TAIL_BYTES = 262144
UNIT_FAILURES_RECENT = 5
_UNIT_FAIL_RE = re.compile(
    r"^===\s+(?P<ts>\S+)\s+UNIT=(?P<unit>\S+)"
    r"(?:\s+STAGE=(?P<stage>\S+))?"
    r"(?:\s+RC=(?P<rc>-?\d+))?\s+===$"
)


def _parse_unit_failures(text_blob: str, now: datetime) -> dict:
    """把 `data/unit_failures.log` 判成近期失敗計數（純函式，便於測試）。

    **為什麼是「時間窗計數」而不是「未讀計數」**：這個檔是 append-only、沒有
    logrotate、也沒有任何已讀游標。用累計筆數當紅點條件＝上線第一天就永遠亮著，
    兩週內會被當成背景噪音（本專案已有「永遠紅的東西會被停用」的教訓）。時間窗
    計數會隨故障結束自然熄滅。

    時間戳解析失敗的紀錄仍列進 `recent`、但不計入任何窗——寧可少算也不要把
    無法定位時間的東西算成「剛剛發生」。
    """
    entries: list[dict] = []
    for line in text_blob.splitlines():
        m = _UNIT_FAIL_RE.match(line.strip())
        if m is None:
            continue          # 內文（systemctl show / journal 尾巴）不是紀錄標頭
        raw_ts = m.group("ts")
        try:
            parsed = datetime.fromisoformat(raw_ts)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo or timezone.utc)
        rc = m.group("rc")
        entries.append({
            "ts": raw_ts,
            "_at": parsed,
            "unit": m.group("unit"),
            "stage": m.group("stage"),
            "rc": int(rc) if rc is not None else None,
        })

    def _within(days: int) -> int:
        cutoff = now - timedelta(days=days)
        return sum(1 for e in entries if e["_at"] is not None and e["_at"] >= cutoff)

    dated = [e["_at"] for e in entries if e["_at"] is not None]
    recent = [
        {k: v for k, v in e.items() if k != "_at"}
        for e in entries[-UNIT_FAILURES_RECENT:][::-1]
    ]
    return {
        "latest": max(dated).isoformat() if dated else None,
        "count_24h": _within(1),
        "count_7d": _within(7),
        "recent": recent,
    }


def _unit_failures() -> dict:
    """`OnFailure` 告警落點的程式消費端——在此之前這個檔一個消費端都沒有。

    2026-07-28 那次 24 小時停擺，`OnFailure` **確實**把 10 筆告警寫進了
    `data/unit_failures.log`，webhook 也沒設，所以整整一天沒有人知道。機制上線
    當天就抓到真故障，缺的只是「有人看得到」。
    """
    if not UNIT_FAILURES_LOG.is_file():
        return {"latest": None, "count_24h": 0, "count_7d": 0, "recent": []}
    blob = _tail_text(UNIT_FAILURES_LOG, UNIT_FAILURES_TAIL_BYTES)
    return _parse_unit_failures(blob, datetime.now(timezone.utc))


def _gather_runtime() -> dict:
    """同步蒐集（log 解析 + /proc 掃描），於 to_thread 中執行不阻塞事件迴圈。"""
    return {
        "tagging": _tag_progress(),
        "ingest": _ingest_progress(),
        "pipelines": {
            "web": True,
            "ingest": _proc_alive("ingest_all.py"),
            # 增量匯入。**與上面那格是不同的東西**：`ingest` 掃的是全量腳本
            # `ingest_all.py`，它只在初次建庫或補跑歷史時才跑；生產實際的入庫路徑
            # 是 `sync_new_reports.py`（排程殼 sync_new_reports.sh 呼叫它，手動補
            # 積壓時也是直接跑它）。
            #
            # 少了這格，「匯入正在跑」在監控頁上沒有任何表徵：下面的 `sync` 區塊讀的是
            # **殼層**寫的 data/sync_run_*.log，只在階段邊界更新，而且直接呼叫 .py
            # 時根本不會被寫到——2026-08-12 手動補 1,783 筆積壓時，整頁六格全滅、
            # sync 區塊還停在上一輪排程的「同步已完成」，看起來就像什麼都沒在跑。
            "sync_import": _proc_alive("sync_new_reports.py"),
            "tag": _proc_alive("tag_all_cli.py"),
            "summaries": _proc_alive("generate_summaries.py"),
            # 顯示標題。排程有兩段會跑它（本輪新檔的缺值 ＋ 跨全語料的歷史積壓，
            # 後者每輪限量 SYNC_TITLE_BACKLOG_LIMIT），而 title 覆蓋率卡量的是
            # 近 30 天，歷史積壓跑起來那張卡幾乎不動——少了這格就完全看不出它在跑。
            "titles": _proc_alive("generate_titles.py"),
            # 摘錄與訊號兩支擷取都沒有進度表徵，只能靠這兩格。兩張覆蓋率卡刻意都只量
            # 近 30 天（見 _fetch_db_stats_snapshot 的註解），而擷取的積壓絕大多數比
            # 30 天舊——2026-08-06 實測缺訊號的 13,821 篇裡只有 156 篇落在窗口內。也
            # 就是說回補歷史時那兩張卡幾乎不動，少了這兩格就完全看不出批次在不在跑。
            "takeaways": _proc_alive("extract_takeaways.py"),
            "signals": _proc_alive("extract_signals.py"),
            # E1d 深夜回填（每晚 01:00 起最多 4 小時）。它不寫任何 log 檔，這格是唯一表徵。
            "backfill": _proc_alive("backfill_extraction.py"),
        },
        "orchestrator": _parse_orchestrator_entry(_orchestrator_last()),
        # 生產實際的入庫路徑（每 3 小時）與 unit 失敗告警的第一個讀取端。
        "sync": _sync_progress(),
        "unit_failures": _unit_failures(),
    }


async def _runtime_snapshot() -> dict:
    """`_gather_runtime` 的 TTL 快取版；形狀與 `_db_stats_snapshot` 對齊。

    快取放在這一層而不是 `_gather_runtime` 裡面，是為了讓既有測試能繼續 patch
    `_gather_runtime` 取代整塊產出（快取由 conftest 的 fixture 每題清空）。
    """
    return await asyncio.to_thread(
        lambda: _cached(_RUNTIME_CACHE, RUNTIME_CACHE_TTL_SECONDS, _gather_runtime)
    )


def _coverage_block(done: int, total: int, latest: str | None) -> dict:
    """近 30 天覆蓋率區塊；形狀比照既有的 summary（done/total/remaining/pct）另加 latest。

    **必須定義在 `@router.get` 之上**：夾在裝飾器與 handler 之間的話，裝飾器會套到
    這支輔助函式，FastAPI 就把 done/total/latest 當成 query 參數 → `/api/progress`
    對正常請求回 422。這正是 2026-07-28 的實際事故；測試直接呼叫 `monitor.progress()`
    函式物件、繞過路由層，所以全綠也擋不住（見 test_monitor_http 的 HTTP 層測試）。
    """
    done, total = int(done), int(total)
    return {
        "done": done,
        "total": total,
        "remaining": max(0, total - done),
        "pct": round(done / total * 100, 2) if total else 0.0,
        "latest": latest,
    }


@router.get("/api/progress")
async def progress():
    snapshot = await _db_stats_snapshot()
    runtime = await _runtime_snapshot()
    s_done = int(snapshot["summary_done"])
    s_total = int(snapshot["summary_total"])
    return {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "db": {
            "reports": snapshot["total_reports"],
            "chunks": snapshot["total_chunks"],
            "markets": snapshot["markets"],
            # 券商分佈：與 markets 同層，因為它們是同一種東西（語料的組成），
            # 分母也同樣是 db.reports。**新增鍵記得同步改 progressSchema.ts**。
            "sources": snapshot["sources"],
        },
        "summary": {
            "done": s_done,
            "total": s_total,
            "remaining": max(0, s_total - s_done),
            "pct": round(s_done / s_total * 100, 2) if s_total else 0.0,
        },
        # 派生資產新鮮度（近 30 天窗口 + 全表最新產出日）。
        # 先前這裡**只有 summary，而 summary 恰好是唯一有排程的**；真正在腐化的兩張表
        # 零量測（2026-07 實測 takeaway 停更 8 天、signal 停更 12 天）。缺席時閱讀頁
        # 整區不進 DOM（優雅降級），症狀是「最新研報靜默少一個功能」，不會有人回報。
        # 全表覆蓋率不能當訊號（兩者都刻意只跑子集），要看的是近期窗口與 latest 是否前進。
        "takeaway": _coverage_block(
            snapshot["takeaway_done_30d"], snapshot["takeaway_total_30d"],
            snapshot["takeaway_latest"],
        ),
        "signal": _coverage_block(
            snapshot["signal_done_30d"], snapshot["signal_total_30d"],
            snapshot["signal_latest"],
        ),
        # M8 查核健康度。這兩張表的 evaluation 欄從上線起就**零讀取路徑**
        # （web/、scripts/、frontend/ 各 0 個消費端），等於查核結果只寫不看：
        # judge 壞掉會以 degraded=true 靜默累積，低分回答也沒有任何地方會浮出來。
        "evaluation": snapshot["evaluation"],
        # 抽取品質與回填進度（E1）。缺表時為 None，前端那張卡降級。
        "extraction": snapshot.get("extraction"),
        # runtime 展開後另含 tagging / ingest / pipelines / orchestrator 與新增的
        # sync（生產實際入庫路徑的可見度）、unit_failures（OnFailure 告警的第一個
        # 讀取端）。**新增鍵時記得同步改 frontend 的 progressSchema.ts**——zod 物件
        # 預設 strip，未宣告的鍵不報錯、直接安靜丟掉（takeaway/signal 就這樣從 P4
        # 起一路送到前端卻從未進 DOM）。
        **runtime,
    }