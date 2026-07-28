# web/routers/monitor.py
"""監控資料 API：/api/stats（語料統計）與 /api/progress（匯入/標註即時進度）。

從 web/server.py 拆出（第三步）。兩條路由同源於 /app/monitor 儀表板。

**stats 與 progress 共用 _DB_STATS_CACHE**：兩者都經 _db_stats_snapshot 取語料
快照，TTL 內只打一次 DB。這是它們必須同模組的原因——快取是模組級狀態，拆到兩
個模組會分裂成兩份，TTL 去重失效（test_server_stats 的「6 次 execute」斷言即
守此）。

進度解析（log tail + /proc 掃描）是同步工作，progress 以 asyncio.to_thread 執行
_gather_runtime，不阻塞事件迴圈。

（/monitor、/help 的 302 轉址屬 SPA 導覽，不在此——留在 server.py。）
"""
import asyncio
import glob as _glob
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from web import auth, deps

router = APIRouter()


DB_STATS_CACHE_TTL_SECONDS = 5.0
_DB_STATS_CACHE: dict[str, object] = {"data": None, "expires_at": 0.0}


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

    def _d(v) -> str | None:
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)

    return {
        "total_reports": total_reports,
        "total_chunks": total_chunks,
        "markets": [{"market": m, "count": c} for m, c in rows],
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


def _count_tag_files() -> int:
    """即時 tag 檔數：每完成一個標註就 +1，比 log（每 100 筆才印）連續細緻。"""
    try:
        return sum(1 for e in os.scandir(TAGS_DIR) if e.name.endswith(".json"))
    except OSError:
        return 0


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


def _gather_runtime() -> dict:
    """同步蒐集（log 解析 + /proc 掃描），於 to_thread 中執行不阻塞事件迴圈。"""
    return {
        "tagging": _tag_progress(),
        "ingest": _ingest_progress(),
        "pipelines": {
            "web": True,
            "ingest": _proc_alive("ingest_all.py"),
            "tag": _proc_alive("tag_all_cli.py"),
            "summaries": _proc_alive("generate_summaries.py"),
        },
        "orchestrator": _parse_orchestrator_entry(_orchestrator_last()),
    }


@router.get("/api/progress")
def _coverage_block(done: int, total: int, latest: str | None) -> dict:
    """近 30 天覆蓋率區塊；形狀比照既有的 summary（done/total/remaining/pct）另加 latest。"""
    done, total = int(done), int(total)
    return {
        "done": done,
        "total": total,
        "remaining": max(0, total - done),
        "pct": round(done / total * 100, 2) if total else 0.0,
        "latest": latest,
    }


async def progress():
    snapshot = await _db_stats_snapshot()
    runtime = await asyncio.to_thread(_gather_runtime)
    s_done = int(snapshot["summary_done"])
    s_total = int(snapshot["summary_total"])
    return {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "db": {
            "reports": snapshot["total_reports"],
            "chunks": snapshot["total_chunks"],
            "markets": snapshot["markets"],
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
        **runtime,
    }