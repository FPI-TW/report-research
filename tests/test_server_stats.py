import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# stats/progress 及其快取、進度解析 helper 已拆到 web.routers.monitor；服務綁定
# （SessionFactory）仍在 web.deps。故 handler/快取/常數的覆寫指向 monitor 模組。
from web import deps  # noqa: E402
from web.routers import monitor  # noqa: E402


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FirstResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class OrchestratorParseTests(unittest.TestCase):
    def test_parse_resume_done_entry(self):
        parsed = monitor._parse_orchestrator_entry(
            "[2026-06-18 23:28:15] === resume done ==="
        )

        self.assertEqual(
            parsed,
            {
                "raw": "[2026-06-18 23:28:15] === resume done ===",
                "timestamp": "2026-06-18 23:28:15",
                "status": "done",
                "label": "編排器已完成",
            },
        )

    def test_parse_unknown_entry_falls_back_to_raw(self):
        parsed = monitor._parse_orchestrator_entry("some unexpected orchestrator text")

        self.assertEqual(
            parsed,
            {
                "raw": "some unexpected orchestrator text",
                "timestamp": None,
                "status": "unknown",
                "label": "編排器狀態",
            },
        )


class SyncLogParseTests(unittest.TestCase):
    """`data/sync_run_<date>.log` 的狀態判定。

    這條 log 是**每日一檔**，一天內會被 8 輪同步接續 append——「整檔有沒有出現
    `=== sync done ===`」因此是錯的判準：第一輪跑完之後它永遠是「已完成」，
    後面 7 輪不管掛在哪都看不出來。
    """

    START = "[2026-07-30 09:00:01] === sync start (pid=1234) ==="
    DONE = "[2026-07-30 09:04:12] === sync done ==="

    def test_done_when_last_marker_is_done(self):
        p = monitor._parse_sync_entry([self.START, self.DONE])
        self.assertEqual(p["status"], "done")
        self.assertEqual(p["label"], "同步已完成")
        self.assertEqual(p["timestamp"], "2026-07-30 09:04:12")

    def test_running_when_a_new_round_started_after_an_earlier_done(self):
        """第二輪已開始、還沒結束——舊寫法（看整檔有無 done）會誤報「已完成」。"""
        lines = [
            self.START,
            self.DONE,
            "[2026-07-30 12:00:01] === sync start (pid=5678) ===",
            "[2026-07-30 12:00:09] 增量匯入 delta…",
        ]
        p = monitor._parse_sync_entry(lines)
        self.assertEqual(p["status"], "running")
        self.assertEqual(p["timestamp"], "2026-07-30 12:00:09")
        self.assertIn("增量匯入", p["raw"])

    def test_unknown_when_tail_has_no_marker(self):
        """檔尾只讀數十 KB，start/done 可能已被切掉——不硬猜，回 unknown。"""
        p = monitor._parse_sync_entry(["[2026-07-30 12:00:09] rsync 同步中…"])
        self.assertEqual(p["status"], "unknown")
        self.assertEqual(p["label"], "同步狀態")

    def test_line_without_timestamp_keeps_raw(self):
        p = monitor._parse_sync_entry(["some unexpected text"])
        self.assertIsNone(p["timestamp"])
        self.assertEqual(p["raw"], "some unexpected text")

    def test_empty_is_none(self):
        self.assertIsNone(monitor._parse_sync_entry([]))
        self.assertIsNone(monitor._parse_sync_entry(["", "   "]))


class UnitFailureParseTests(unittest.TestCase):
    """`data/unit_failures.log` 的近期失敗計數。

    兩種寫入者的標頭格式不同（`report-mark-alert.sh` 沒有 STAGE/RC，
    `sync_new_reports.sh` 兩者都有），parser 必須同時吃得下——只認一種的話，
    真正需要看見的那一種可能剛好是被漏掉的那一種。
    """

    NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)

    def _log(self) -> str:
        # 交錯真實內文：systemctl show 與 journal 尾巴不得被誤認成紀錄標頭。
        return "\n".join([
            "=== 2026-07-20T15:00:03+00:00  UNIT=report-mark-web.service ===",
            "Result=exit-code",
            "--- journal (last 30) ---",
            "Jul 20 15:00:03 host uvicorn[1]: boom",
            "",
            "=== 2026-07-29T23:00:00+00:00  UNIT=report-mark-sync.service"
            "  STAGE=generate_summaries  RC=75 ===",
            "--- data/sync_run_20260729.log (last 20) ---",
            "[2026-07-29 23:00:00] 摘要生成非零退出 rc=75",
            "",
            "=== 2026-07-30T07:00:00+00:00  UNIT=report-mark-sync.service"
            "  STAGE=sync_new_reports(import)  RC=2 ===",
            "",
        ])

    def test_counts_per_window(self):
        d = monitor._parse_unit_failures(self._log(), self.NOW)
        self.assertEqual(d["count_24h"], 2)   # 07-29 23:00 與 07-30 07:00
        self.assertEqual(d["count_7d"], 2)    # 07-20 那筆超過 7 天
        self.assertEqual(d["latest"], "2026-07-30T07:00:00+00:00")

    def test_recent_is_newest_first_and_carries_stage_rc(self):
        d = monitor._parse_unit_failures(self._log(), self.NOW)
        self.assertEqual(d["recent"][0]["stage"], "sync_new_reports(import)")
        self.assertEqual(d["recent"][0]["rc"], 2)
        self.assertEqual(d["recent"][1]["rc"], 75)

    def test_alert_sh_format_without_stage_or_rc(self):
        d = monitor._parse_unit_failures(self._log(), self.NOW)
        oldest = d["recent"][-1]
        self.assertEqual(oldest["unit"], "report-mark-web.service")
        self.assertIsNone(oldest["stage"])
        self.assertIsNone(oldest["rc"])

    def test_body_lines_are_not_counted_as_entries(self):
        d = monitor._parse_unit_failures(self._log(), self.NOW)
        self.assertEqual(len(d["recent"]), 3)

    def test_recent_is_capped(self):
        many = "\n".join(
            f"=== 2026-07-30T0{i}:00:00+00:00  UNIT=u{i}.service ===" for i in range(1, 9)
        )
        d = monitor._parse_unit_failures(many, self.NOW)
        self.assertEqual(len(d["recent"]), monitor.UNIT_FAILURES_RECENT)
        self.assertEqual(d["recent"][0]["unit"], "u8.service")

    def test_unparseable_timestamp_is_listed_but_not_counted(self):
        """寧可少算，也不要把「無法定位時間」的東西算成剛剛發生。"""
        d = monitor._parse_unit_failures(
            "=== not-a-timestamp  UNIT=report-mark-sync.service  RC=1 ===", self.NOW
        )
        self.assertEqual(d["count_24h"], 0)
        self.assertIsNone(d["latest"])
        self.assertEqual(len(d["recent"]), 1)

    def test_empty_log_is_all_zero(self):
        d = monitor._parse_unit_failures("", self.NOW)
        self.assertEqual((d["count_24h"], d["count_7d"], d["latest"], d["recent"]),
                         (0, 0, None, []))

    def test_missing_file_is_all_zero(self):
        """檔案不存在＝從未有 unit 失敗過，是正常狀態不是錯誤。"""
        orig = monitor.UNIT_FAILURES_LOG
        monitor.UNIT_FAILURES_LOG = Path("/nonexistent/unit_failures.log")
        try:
            d = monitor._unit_failures()
        finally:
            monitor.UNIT_FAILURES_LOG = orig
        self.assertEqual(d, {"latest": None, "count_24h": 0, "count_7d": 0, "recent": []})


class GatherRuntimeShapeTests(unittest.TestCase):
    """`_gather_runtime` **實際**要產出 sync 與 unit_failures 兩個鍵。

    為什麼這條不能省：`tests/test_monitor_http.py` 的端點測試是 patch 掉
    `_gather_runtime` 再驗 handler 有沒有透傳，所以把這兩個鍵從 `_gather_runtime`
    整條拿掉，那邊照樣全綠——症狀只會是監控頁那張卡永遠顯示「此版後端未提供」。
    這正是本專案記過的「後端在送、前端沒宣告」的鏡像版本。
    """

    def test_runtime_includes_sync_and_unit_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            orig = (monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG)
            monitor.DATA_DIR = empty
            monitor.TAGS_DIR = empty / "tags"
            monitor.UNIT_FAILURES_LOG = empty / "unit_failures.log"
            monitor.reset_caches()
            try:
                runtime = monitor._gather_runtime()
            finally:
                monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG = orig
                monitor.reset_caches()

        for key in ("tagging", "ingest", "pipelines", "orchestrator", "sync", "unit_failures"):
            self.assertIn(key, runtime)
        # 空的 data/ 是合法狀態（新機器）：沒有 log 就是 None，沒有失敗就是全 0。
        self.assertIsNone(runtime["sync"])
        self.assertEqual(runtime["unit_failures"]["count_24h"], 0)

    def test_runtime_pipelines_cover_every_tracked_batch(self):
        """八格管線一格都不能少。

        `takeaways`／`signals` 是 2026-08-06 補的：兩張覆蓋率卡刻意只量近 30 天，而
        回補歷史積壓時絕大多數研報比 30 天舊（實測缺訊號 13,821 篇裡只有 156 篇在
        窗口內），那兩張卡幾乎不動——這兩格是唯一看得出 `extract_takeaways.py` 與
        `extract_signals.py` 在不在跑的地方。

        `sync_import` 是 2026-08-12 補的，理由同型但更嚴重：`ingest` 掃的是全量
        `ingest_all.py`，而生產實際的入庫路徑是 `sync_new_reports.py`，先前**完全
        沒有表徵**——手動補 1,783 筆積壓時整頁六格全滅、`sync` 區塊還停在上一輪
        排程的「同步已完成」。

        用集合相等而非 assertIn：多一格沒同步到前端 ROWS 也該被看見。
        """
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            orig = (monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG)
            monitor.DATA_DIR = empty
            monitor.TAGS_DIR = empty / "tags"
            monitor.UNIT_FAILURES_LOG = empty / "unit_failures.log"
            monitor.reset_caches()
            try:
                runtime = monitor._gather_runtime()
            finally:
                monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG = orig
                monitor.reset_caches()

        self.assertEqual(
            set(runtime["pipelines"]),
            {"web", "ingest", "sync_import", "tag", "summaries", "titles", "takeaways", "signals", "backfill"},
        )

    def test_proc_alive_tracks_a_real_process(self):
        """反轉實驗：同一個 needle，行程活著為 True、收掉後為 False。

        少了 False 那半邊，一支永遠回 True 的 `_proc_alive` 也會通過——而那正是
        「管線燈永遠亮著」的故障樣態，比燈不亮更難發現。
        """
        marker = "report_mark_proc_alive_probe"
        proc = subprocess.Popen(
            [sys.executable, "-c", f"# {marker}\nimport time; time.sleep(30)"]
        )
        try:
            self.assertTrue(monitor._proc_alive(marker))
        finally:
            proc.kill()
            proc.wait()
        self.assertFalse(monitor._proc_alive(marker))

    def test_runtime_reads_a_real_sync_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "sync_run_20260730.log").write_text(
                "[2026-07-30 09:00:01] === sync start (pid=1) ===\n"
                "[2026-07-30 09:04:12] === sync done ===\n",
                encoding="utf-8",
            )
            (d / "unit_failures.log").write_text(
                "=== 2026-07-30T07:00:00+00:00  UNIT=report-mark-sync.service  RC=2 ===\n",
                encoding="utf-8",
            )
            orig = (monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG)
            monitor.DATA_DIR = d
            monitor.TAGS_DIR = d / "tags"
            monitor.UNIT_FAILURES_LOG = d / "unit_failures.log"
            monitor.reset_caches()
            try:
                runtime = monitor._gather_runtime()
            finally:
                monitor.DATA_DIR, monitor.TAGS_DIR, monitor.UNIT_FAILURES_LOG = orig
                monitor.reset_caches()

        self.assertEqual(runtime["sync"]["status"], "done")
        self.assertEqual(runtime["unit_failures"]["recent"][0]["rc"], 2)


class StatsCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_cache_ttl_is_within_requested_range(self):
        """DB 快照 TTL 的合理區間。

        上界從 5 秒放寬到 15 秒（2026-07-30）：前端每 5 秒輪詢，這一塊是 10 條查詢、
        其中兩條是全表 GROUP BY，15 秒讓 DB 負載降為三分之一，而 `ts` 欄與 runtime
        區塊仍每次更新，觀感幾乎無差。下界仍守著「不能拿掉快取」。
        """
        self.assertGreaterEqual(monitor.DB_STATS_CACHE_TTL_SECONDS, 3.0)
        self.assertLessEqual(monitor.DB_STATS_CACHE_TTL_SECONDS, 15.0)

    def test_runtime_and_tag_count_have_their_own_ttl(self):
        """DB 快照拉長只解一半：runtime 區塊（log tail + /proc + tag 檔數）原本
        每次輪詢都重跑，而其中 `data/tags/` 的 scandir 是整頁最貴的一件事
        （本機實測 15,852 檔、冷 412 ms／熱 117 ms；三次 `_proc_alive` 只 2.4 ms）。"""
        self.assertGreaterEqual(monitor.RUNTIME_CACHE_TTL_SECONDS, 5.0)
        self.assertGreaterEqual(monitor.TAG_FILE_COUNT_TTL_SECONDS, 30.0)
        # tag 檔數的窗必須比 runtime 更長，否則它會被 runtime 的每次重算拖著走
        self.assertGreater(
            monitor.TAG_FILE_COUNT_TTL_SECONDS, monitor.RUNTIME_CACHE_TTL_SECONDS
        )

    def setUp(self):
        # conftest 的 autouse fixture 已經清過三個快取；這裡保留是為了讓本檔單獨
        # 以 unittest 執行（不經 pytest）時行為一致。
        monitor.reset_caches()

    async def test_stats_and_progress_share_one_db_snapshot_within_ttl(self):
        calls = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                sql = str(stmt)
                calls.append(sql)
                if "FILTER (WHERE summary IS NOT NULL)" in sql:
                    return _FirstResult((3, 7))
                # 這兩個必須排在下方 catch-all「FROM research.research_report」之前:
                # 它們同樣掃 research_report,被 catch-all 攔到會回純量、解包時炸。
                if "report_takeaway" in sql:
                    return _FirstResult((4, 10, date(2026, 7, 20)))
                if "report_signal" in sql:
                    return _FirstResult((1, 10, date(2026, 7, 16)))
                # M8 查核統計：qa_log 一列 ×（kind + 6 個聚合）。必須排在 catch-all 之前（同上）。
                # E1 抽取品質：三段 UNION ALL 併成 (kind, key, count)。必須排在 catch-all
                # 之前（它也掃 research_report）。
                if "extraction_log" in sql:
                    return _RowsResult([
                        ("version", "ext-2026-09-02.v3", 2),
                        ("version", "(unknown)", 4),
                        ("stopped_at", "ingested", 5),
                        ("stopped_at", "skip_admin", 1),
                        ("flag", "needs_review", 1),
                        ("flag", "pages_failed", 0),
                        ("log_latest", "2026-09-03", 0),
                    ])
                if "count(evaluation)" in sql:
                    return _RowsResult([
                        ("qa", 40, 3, 1, 1, 0.5634, date(2026, 7, 28)),
                    ])
                if "unnest(instrument_types)" in sql:
                    return _RowsResult([("equity", 5)])
                if "GROUP BY report_type" in sql:
                    return _RowsResult([("daily", 4)])
                if "GROUP BY market" in sql:
                    return _RowsResult([("TW", 6)])
                # 券商分佈。NULL source（檔名認不出券商）刻意也回一列——它是導入
                # 品質的訊號，被過濾掉的話 sources 的總和就不再等於 db.reports。
                if "GROUP BY source" in sql:
                    return _RowsResult([
                        ("kgi", 4, date(2026, 8, 4)),
                        (None, 2, date(2026, 7, 31)),
                    ])
                if "FROM research.report_chunk" in sql:
                    return _ScalarResult(12)
                if "FROM research.research_report" in sql:
                    return _ScalarResult(6)
                raise AssertionError(sql)

        orig_session_factory = deps.SessionFactory
        orig_gather_runtime = monitor._gather_runtime
        deps.SessionFactory = lambda: FakeSession()
        monitor._gather_runtime = lambda: {
            "tagging": None,
            "ingest": None,
            "pipelines": {"web": True},
            "orchestrator": None,
        }
        try:
            stats = await monitor.stats()
            progress = await monitor.progress()
        finally:
            deps.SessionFactory = orig_session_factory
            monitor._gather_runtime = orig_gather_runtime

        # 11 = 原本 6 + takeaway/signal 覆蓋率各一 + M8 查核統計一（兩張表以 UNION ALL
        # 併成單次查詢，刻意不拆成兩次）+ 券商分佈一 + E1 抽取品質一（同樣 UNION ALL
        # 併成單次）。這個數字守的是「stats 與 progress 共用 _DB_STATS_CACHE、TTL 內
        # 只打一次 DB」（見 monitor.py docstring）。
        self.assertEqual(len(calls), 11)
        ext = progress["extraction"]
        self.assertEqual(ext["backfill"]["done"], 0 if ext["target_version"] != "ext-2026-09-02.v3" else 2)
        self.assertEqual(ext["backfill"]["total"], 6)
        self.assertEqual(ext["stopped_at"], {"ingested": 5, "skip_admin": 1})
        self.assertEqual(ext["needs_review"], 1)
        self.assertEqual(ext["log_latest"], "2026-09-03")
        self.assertEqual([v["version"] for v in ext["versions"]], ["(unknown)", "ext-2026-09-02.v3"])
        self.assertEqual(stats["total_reports"], 6)
        self.assertEqual(progress["db"]["reports"], 6)
        self.assertEqual(progress["summary"]["total"], 7)
        # 派生資產新鮮度（在 progress 而非 stats——與既有的 summary 覆蓋率同處）:
        # 先前只量 summary,而 summary 恰好是唯一有排程的,真正在腐化的兩張表零量測。
        self.assertEqual(progress["takeaway"]["done"], 4)
        self.assertEqual(progress["takeaway"]["total"], 10)
        self.assertEqual(progress["takeaway"]["remaining"], 6)
        self.assertEqual(progress["takeaway"]["pct"], 40.0)
        self.assertEqual(progress["takeaway"]["latest"], "2026-07-20")
        self.assertEqual(progress["signal"]["done"], 1)
        self.assertEqual(progress["signal"]["latest"], "2026-07-16")
        # 券商分佈：中文名在**後端**由 source_display 決定（對照表的單一真相在
        # app/services/filename.py，檢索頁與閱讀頁也走它）。若哪天有人把對映搬到
        # 前端，這條會紅。NULL source 保留為一列且 display 為 None，由 UI 標「未辨識」。
        self.assertEqual(
            progress["db"]["sources"],
            [
                {"source": "kgi", "display": "凱基", "count": 4, "latest": "2026-08-04"},
                {"source": None, "display": None, "count": 2, "latest": "2026-07-31"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
