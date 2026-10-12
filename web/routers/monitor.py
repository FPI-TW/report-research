# web/routers/monitor.py
"""監控資料 API：/api/stats（語料統計）與 /api/progress（匯入/標註即時進度）。

從 web/server.py 拆出（第三步）。/api/stats 供檢索頁的全庫市場計數，一般登入者可用；
/api/progress 只供管理後台的管線分頁（/app/admin/operations/pipeline），**在路由上**掛
`require_admin`＋`ops.read`（不能掛在 router 層：同一支 router 的 /api/stats 要維持
一般使用者可用）。待複核頁的判定尺改走 `/api/review/judge-scale`（web/routers/review.py），
不再為了一個附註拿到整份 runtime 與管線資訊（issue #348）。

**DB 快照與它的快取在 `web/stats_snapshot.py`**：stats、progress 與 review 的判定尺端點
都經 `stats_snapshot.db_stats_snapshot()` 取同一份快照，TTL 內只打一次 DB。放在 router
之外，是因為 router 之間不互相 import。

進度解析（log tail + /proc 掃描）是同步工作，progress 以 asyncio.to_thread 執行
_gather_runtime，不阻塞事件迴圈。

**三層 TTL 快取，各有不同的理由**（管線分頁每 15 秒輪詢一次，所以每一項成本都會
乘上開著頁面的分頁數；TanStack Query 在視窗失焦時會停 interval，所以成立條件是
「管線分頁開著且在前景」）：

  _DB_STATS_CACHE    10 條 DB 查詢。15 秒，與前端輪詢間隔對齊（web/stats_snapshot.py）。
  _RUNTIME_CACHE     整個 runtime 區塊（log tail + /proc + tag 檔數）。
  _TAG_COUNT_CACHE   `data/tags/` 的 scandir，**這裡真正的熱點**：本機實測
                     15,852 個檔、冷 412 ms／熱 117 ms，而三次 `_proc_alive`
                     加起來只 2.4 ms（架構檢視把成本歸給 `_proc_alive`，量下去
                     發現差 50 倍）。60 秒是安全的：全量標註只在初次建庫時跑，
                     那個場景下一分鐘的粒度完全夠用。

（/monitor、/help 的 302 轉址屬 SPA 導覽，不在此——在 web/routers/spa.py。）
"""
import asyncio
import glob as _glob
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from app.services.accounts import User
from web import authz, stats_snapshot

router = APIRouter()

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
    """清空本模組所有 TTL 快取（DB 快照的快取在 `web/stats_snapshot.py`，由它的 `reset_cache` 清）。

    **測試用**：模組級快取會跨測試存活，於是「A 測試 patch 掉 `_gather_runtime`
    後呼叫 progress」會把假值留給 B 測試（形狀相同時完全看不出來）。由
    `tests/conftest.py` 的 autouse fixture 每題前後各清一次。
    """
    for store in (_RUNTIME_CACHE, _TAG_COUNT_CACHE):
        store["data"] = None
        store["expires_at"] = 0.0


@router.get("/api/stats")
async def stats(user: User = Depends(authz.current_user)):
    snapshot = await stats_snapshot.db_stats_snapshot()
    return {
        "total_reports": snapshot["total_reports"],
        "total_chunks": snapshot["total_chunks"],
        "markets": snapshot["markets"],
        "instrument_types": snapshot["instrument_types"],
        "report_types": snapshot["report_types"],
        # 目前登入者（側欄底部顯示）。快照是全站共用的快取，這一鍵每個請求各自帶，
        # 不進快照的快取。角色等身分資訊走 /api/me。
        "username": user.username,
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
            # 近 30 天（見 web/stats_snapshot.py 的註解），而擷取的積壓絕大多數比
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
    """`_gather_runtime` 的 TTL 快取版；形狀與 `stats_snapshot.db_stats_snapshot` 對齊。

    快取放在這一層而不是 `_gather_runtime` 裡面，是為了讓既有測試能繼續 patch
    `_gather_runtime` 取代整塊產出（快取由 conftest 的 fixture 每題清空）。
    """
    return await asyncio.to_thread(
        lambda: _cached(_RUNTIME_CACHE, RUNTIME_CACHE_TTL_SECONDS, _gather_runtime)
    )


# 完整管線資料（runtime、pipelines、log 尾巴）只給有 ops.read 的管理員。掛在路由而不是 router 層：
# /api/stats 在同一支 router，要維持一般使用者可用。tests/test_authz.py 把這條列為必須守門的路徑。
@router.get(
    "/api/progress",
    dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("ops.read"))],
)
async def progress():
    snapshot = await stats_snapshot.db_stats_snapshot()
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
        "takeaway": stats_snapshot.coverage_block(
            snapshot["takeaway_done_30d"], snapshot["takeaway_total_30d"],
            snapshot["takeaway_latest"],
        ),
        "signal": stats_snapshot.coverage_block(
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