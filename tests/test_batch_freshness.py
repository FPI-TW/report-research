# tests/test_batch_freshness.py
"""`scripts/check_batch_freshness.py` 的門檻邏輯與新 unit 的形狀。

門檻判定全部走純函式 `assess()`，用注入的時間與假 session——**不連 DB**。
理由與 `tests/test_db_backup.py` 相同：這支腳本的價值在「判斷對不對」，而不在
「連得上不連得上」，把 DB 拉進來只會換到一支對環境敏感的測試。

這裡守的四件事，每一條都對應一個具體的失效方式：

1. **語料閘的抑制**。三支批次都只吃「本輪新入庫」的研報，沒有新研報時它們一行
   都不會產出——那是正確行為。少了抑制，一個連假就會讓三個資產同時亮紅，而
   「天天假警報」的下一步就是沒人看告警。
2. **`signal` 預設不告警**。`report_signal` 沒有任何排程產生者（`sync` 殼只跑
   摘要／標題／摘錄），給它門檻等於保證永遠紅。
3. **時區**。`created_at` 是 `timestamptz`，但假 session 與 JSON 往返會餵進
   naive 值；aware 與 naive 相減直接 `TypeError`。偵測器自己炸掉而沒人知道，
   正是它要消除的故障型態。
4. **退出碼分流**。1＝停更（去看批次日誌）、2＝查不到（去看 DB／`/healthz`）。
   兩者處置不同，混成同一個碼等於把「DB 掛了」誤導成「批次壞了」。
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_batch_freshness as cbf  # noqa: E402

NOW = datetime(2026, 7, 30, 8, 30, tzinfo=timezone.utc)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _state(findings: list, asset: str) -> str:
    return next(f.state for f in findings if f.asset == asset)


class ThresholdTests(unittest.TestCase):
    """門檻本身：窗內新鮮、窗外停更、邊界不含等號誤差。"""

    def _assess(self, **latest):
        base = {"corpus": _ago(0.1), "summary": _ago(0.1),
                "takeaway": _ago(0.1), "signal": _ago(0.1)}
        base.update(latest)
        return cbf.assess(NOW, base, {"corpus": 0, "summary": 3, "takeaway": 3, "signal": 14})

    def test_all_fresh(self):
        f = self._assess()
        self.assertEqual(_state(f, "summary"), cbf.STATE_FRESH)
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_FRESH)
        self.assertFalse(cbf.has_stale(f))

    def test_takeaway_stale_while_corpus_fresh(self):
        """實際事故的形狀：語料照樣在進，摘錄停了 8 天。"""
        f = self._assess(takeaway=_ago(8))
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_STALE)
        self.assertEqual(_state(f, "summary"), cbf.STATE_FRESH)
        self.assertTrue(cbf.has_stale(f))

    def test_exactly_at_threshold_is_fresh(self):
        """恰好等於門檻不算停更（`>` 不是 `>=`）——否則門檻 3 天實際是 2 天。"""
        f = self._assess(takeaway=_ago(3))
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_FRESH)

    def test_just_over_threshold_is_stale(self):
        f = self._assess(takeaway=_ago(3.01))
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_STALE)

    def test_never_produced_with_live_corpus_is_stale(self):
        f = self._assess(takeaway=None)
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_STALE)
        self.assertIn("從未產出", next(x.detail for x in f if x.asset == "takeaway"))


class CorpusGateTests(unittest.TestCase):
    """語料閘：沒有新研報時派生資產的過期一律抑制，不是故障。"""

    THRESHOLDS = {"corpus": 0, "summary": 3, "takeaway": 3, "signal": 14}

    def test_stale_corpus_suppresses_derived(self):
        """連假／NAS 沒供稿：語料與三張派生表一起停在 9 天前，一條都不該紅。

        signal 在這組資料裡是 `fresh` 而非 `suppressed`（9 天 < 門檻 14 天，根本
        沒到過期就不需要抑制）——抑制只是「已過期但情有可原」的那一格。
        """
        f = cbf.assess(
            NOW,
            {"corpus": _ago(9), "summary": _ago(9), "takeaway": _ago(9), "signal": _ago(9)},
            self.THRESHOLDS,
        )
        for asset in ("summary", "takeaway"):
            self.assertEqual(_state(f, asset), cbf.STATE_SUPPRESSED, asset)
        self.assertEqual(_state(f, "signal"), cbf.STATE_FRESH)
        self.assertFalse(cbf.has_stale(f))

    def test_stale_corpus_suppresses_even_the_widest_window(self):
        f = cbf.assess(
            NOW,
            {"corpus": _ago(30), "summary": _ago(30), "takeaway": _ago(30), "signal": _ago(30)},
            self.THRESHOLDS,
        )
        for asset in ("summary", "takeaway", "signal"):
            self.assertEqual(_state(f, asset), cbf.STATE_SUPPRESSED, asset)
        self.assertFalse(cbf.has_stale(f))

    def test_empty_corpus_suppresses_instead_of_alerting(self):
        """新機器／語料重建中：三張派生表都空，不該被讀成批次壞了。"""
        f = cbf.assess(
            NOW,
            {"corpus": None, "summary": None, "takeaway": None, "signal": None},
            self.THRESHOLDS,
        )
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_SUPPRESSED)
        self.assertFalse(cbf.has_stale(f))

    def test_suppression_is_per_asset_threshold(self):
        """語料停 5 天：門檻 3 天的摘錄被抑制，門檻 14 天的訊號仍受檢。

        抑制窗刻意用「該資產自己的門檻」而不是固定天數——用固定天數的話，寬門檻
        資產會被短暫的語料空窗一併放過，等於門檻越寬越不受檢。
        """
        f = cbf.assess(
            NOW,
            {"corpus": _ago(5), "summary": _ago(5), "takeaway": _ago(5), "signal": _ago(30)},
            self.THRESHOLDS,
        )
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_SUPPRESSED)
        self.assertEqual(_state(f, "signal"), cbf.STATE_STALE)

    def test_corpus_itself_is_not_suppressed_by_itself(self):
        f = cbf.assess(NOW, {"corpus": _ago(9)}, {"corpus": 3})
        self.assertEqual(_state(f, "corpus"), cbf.STATE_STALE)


class DefaultThresholdTests(unittest.TestCase):
    """預設值是政策決定，用測試把它釘住（改動時必須是有意識的）。"""

    def test_signal_defaults_to_disabled(self):
        """`report_signal` 的停更**在原理上無法與正常區分**，所以不設門檻。

        這條的理由在 2026-07-30 換過一次，值得寫清楚：原本是「沒有任何排程產生者，
        給門檻＝保證永遠紅」。PR #150 之後訊號擷取**已經進排程**（每 3 小時
        `extract_signals --limit 15`），原理由失效——而它正是被下面那條反向測試抓到的。

        但結論不變，因為新的理由更根本：訊號只來自高覆蓋子集（2026-07-17 實測
        99/14,575＝0.68%），排程把積壓跑完之後 `max(created_at)` 就不再前進，而那與
        「這段時間沒有合格研報」**完全無法區分**。設任何門檻都會在積壓耗盡的那天
        開始每日假警報。

        真正該偵測的「排程壞了」已由另一條路涵蓋：sync 殼對 `extract_signals` 非零
        退出會呼叫 `record_unit_failure`，而 `/api/progress` 的 `unit_failures` 讀它
        （見 test_sync_shell_records_signal_failures）。
        """
        self.assertEqual(cbf.DEFAULT_THRESHOLDS["signal"], 0)

    def test_sync_shell_records_signal_failures(self):
        """上一條的前提：訊號擷取失敗必須留下可稽核的痕跡。

        取代了原本的 `assertNotIn("extract_signals", shell)`——那條的前提（沒有排程
        產生者）已被 PR #150 推翻。現在要守的是「不設門檻」的正當性：既然不靠新鮮度
        告警，就必須靠失敗記錄。哪天有人把 `record_unit_failure` 從訊號那段拿掉，
        訊號就會變成**完全沒有任何偵測**——那才是這條要擋的漏檢。
        """
        shell = (REPO_ROOT / "scripts" / "sync_new_reports.sh").read_text(encoding="utf-8")
        live = "\n".join(ln for ln in shell.splitlines() if not ln.strip().startswith("#"))
        self.assertIn("extract_signals", live, "訊號擷取應在排程內（PR #150）")
        self.assertRegex(
            live,
            r'record_unit_failure\s+"extract_signals"',
            "訊號擷取失敗必須記進 unit_failures.log——它是這個資產唯一的偵測路徑",
        )

    def test_corpus_defaults_to_disabled(self):
        """語料停更已由 sync unit 的 OnFailure 覆蓋（匯入段失敗 exit 1）。"""
        self.assertEqual(cbf.DEFAULT_THRESHOLDS["corpus"], 0)

    def test_summary_and_takeaway_are_armed(self):
        self.assertEqual(cbf.DEFAULT_THRESHOLDS["summary"], 3)
        self.assertEqual(cbf.DEFAULT_THRESHOLDS["takeaway"], 3)

    def test_disabled_asset_never_reports_stale(self):
        f = cbf.assess(NOW, {"corpus": _ago(0.1), "signal": _ago(900)},
                       {"corpus": 0, "signal": 0})
        self.assertEqual(_state(f, "signal"), cbf.STATE_DISABLED)
        self.assertFalse(cbf.has_stale(f))

    def test_cli_defaults_match_module_defaults(self):
        args = cbf.parse_args([])
        self.assertEqual(cbf.thresholds_from_args(args), cbf.DEFAULT_THRESHOLDS)

    def test_cli_can_arm_signal(self):
        args = cbf.parse_args(["--signal-days", "14"])
        self.assertEqual(cbf.thresholds_from_args(args)["signal"], 14)


class TimezoneTests(unittest.TestCase):
    """naive 時間戳不得讓偵測器自己拋例外。"""

    def test_naive_latest_is_treated_as_utc(self):
        naive = NOW.replace(tzinfo=None) - timedelta(days=8)
        f = cbf.assess(NOW, {"corpus": _ago(0.1), "takeaway": naive}, {"corpus": 0, "takeaway": 3})
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_STALE)

    def test_naive_now_does_not_raise(self):
        f = cbf.assess(
            NOW.replace(tzinfo=None),
            {"corpus": _ago(0.1), "takeaway": _ago(0.1)},
            {"corpus": 0, "takeaway": 3},
        )
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_FRESH)

    def test_offset_aware_latest_is_normalized(self):
        tz8 = timezone(timedelta(hours=8))
        f = cbf.assess(
            NOW,
            {"corpus": _ago(0.1), "takeaway": _ago(0.5).astimezone(tz8)},
            {"corpus": 0, "takeaway": 3},
        )
        self.assertEqual(_state(f, "takeaway"), cbf.STATE_FRESH)


class ExitCodeTests(unittest.IsolatedAsyncioTestCase):
    """1＝停更、2＝查不到。混成一個碼會把「DB 掛了」誤導成「批次壞了」。"""

    class _Session:
        def __init__(self, row):
            self.row = row

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, params=None):
            row = self.row

            class _R:
                def first(self_inner):
                    return row

            return _R()

    def _patch_session(self, factory):
        import app.services.db as db

        orig = db.SessionFactory
        db.SessionFactory = factory
        self.addCleanup(lambda: setattr(db, "SessionFactory", orig))

    def setUp(self):
        """**心跳必須被明確宣告，不能沿用 checkout 的實際狀態。**

        `run()` 現在同時判管線心跳與四個資產。這幾條測的是資產那一維，若不把心跳
        釘成「新鮮」，它們會在沒有心跳檔的 checkout 上一律回 rc=3（UPSTREAM_STALE）
        ——**測試不會壞掉，只會安靜地改成在測別的東西**。
        """
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.heartbeat = Path(self._tmp.name) / "hb"
        self.set_heartbeat_age_hours(1)
        orig = cbf.HEARTBEAT_PATH
        cbf.HEARTBEAT_PATH = self.heartbeat
        self.addCleanup(lambda: setattr(cbf, "HEARTBEAT_PATH", orig))

    def set_heartbeat_age_hours(self, hours: float):
        import time

        self.heartbeat.write_text(
            f"epoch={int(time.time() - hours * 3600)}\n", encoding="utf-8"
        )

    async def test_fresh_exits_zero(self):
        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        self._patch_session(lambda: self._Session((fresh, fresh, fresh, fresh)))
        rc = await cbf.run({"corpus": 0, "summary": 3, "takeaway": 3, "signal": 0}, False, 9)
        self.assertEqual(rc, cbf.EXIT_OK)

    async def test_stale_exits_one(self):
        now = datetime.now(timezone.utc)
        self._patch_session(
            lambda: self._Session((now, now, now - timedelta(days=8), now))
        )
        rc = await cbf.run({"corpus": 0, "summary": 3, "takeaway": 3, "signal": 0}, False, 9)
        self.assertEqual(rc, cbf.EXIT_STALE)

    async def test_stale_pipeline_exits_three_end_to_end(self):
        """資產全新鮮但管線沒跑完 → rc=3，而且不得被誤報成 rc=0。"""
        self.set_heartbeat_age_hours(30)
        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        self._patch_session(lambda: self._Session((fresh, fresh, fresh, fresh)))
        rc = await cbf.run({"corpus": 0, "summary": 3, "takeaway": 3, "signal": 0}, False, 9)
        self.assertEqual(rc, cbf.EXIT_UPSTREAM_STALE)

    async def test_missing_heartbeat_exits_three_even_when_assets_fresh(self):
        self.heartbeat.unlink()
        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        self._patch_session(lambda: self._Session((fresh, fresh, fresh, fresh)))
        rc = await cbf.run({"corpus": 0, "summary": 3, "takeaway": 3, "signal": 0}, False, 9)
        self.assertEqual(rc, cbf.EXIT_UPSTREAM_STALE)

    async def test_db_failure_exits_two(self):
        def _boom():
            raise OSError("connection refused")

        self._patch_session(_boom)
        rc = await cbf.run(cbf.DEFAULT_THRESHOLDS, False, 9)
        self.assertEqual(rc, cbf.EXIT_UNKNOWN)

    async def test_json_output_is_parseable(self):
        import io
        import json
        from contextlib import redirect_stdout

        now = datetime.now(timezone.utc)
        self._patch_session(
            lambda: self._Session((now, now, now - timedelta(days=8), now))
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await cbf.run({"corpus": 0, "summary": 3, "takeaway": 3, "signal": 0}, True, 9)
        payload = json.loads(buf.getvalue())
        self.assertEqual(rc, cbf.EXIT_STALE)
        self.assertTrue(payload["stale"])
        # 5 而非 4：管線那一筆現在也在報告裡（第一筆）。
        self.assertEqual(len(payload["findings"]), 5)
        self.assertEqual(payload["findings"][0]["asset"], "pipeline")
        self.assertIn("upstream_stale", payload, "JSON 必須能表達上游狀態，否則監控接不到")


class SqlShapeTests(unittest.TestCase):
    """查詢形狀：只讀、只 max()、母體與批次實際處理的一致。"""

    def test_query_is_read_only(self):
        upper = cbf.LATEST_SQL.upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "DROP"):
            self.assertNotIn(verb, upper, f"偵測器不得寫 DB（出現 {verb}）")

    def test_query_covers_three_derived_tables_and_corpus(self):
        for table in (
            "research.research_report",
            "research.report_takeaway",
            "research.report_signal",
        ):
            self.assertIn(table, cbf.LATEST_SQL)
        self.assertEqual(cbf.LATEST_SQL.count("max(created_at)"), 4)

    def test_corpus_population_matches_batch_eligibility(self):
        """語料閘的母體必須是「有全文、非行政檔」。

        用全表 max() 會被行政／活動檔拉新，於是「連續幾天只進非研報」時抑制失效、
        三個資產一起假紅——那恰好是抑制存在的理由。
        """
        self.assertIn("full_text IS NOT NULL", cbf.LATEST_SQL)
        self.assertIn("is_research IS NOT FALSE", cbf.LATEST_SQL)


class ReportFormatTests(unittest.TestCase):
    def test_stale_report_names_the_asset_and_where_to_look(self):
        f = cbf.assess(NOW, {"corpus": _ago(0.1), "takeaway": _ago(8)},
                       {"corpus": 0, "takeaway": 3})
        out = cbf.fmt_report(f, NOW)
        self.assertIn("STALE", out)
        self.assertIn("重點摘錄", out)
        self.assertIn("unit_failures.log", out)

    def test_fresh_report_says_so(self):
        f = cbf.assess(NOW, {"corpus": _ago(0.1), "takeaway": _ago(0.1)},
                       {"corpus": 0, "takeaway": 3})
        self.assertIn("全部在門檻內", cbf.fmt_report(f, NOW))


class FreshnessUnitTests(unittest.TestCase):
    """新 unit 的形狀。`tests/test_deploy_units.py` 通掃全部 unit 的 `%h` 與 HOME
    一致性，這裡釘的是「這一支存在、而且真的接上既有告警鏈」——那條 OnFailure 就是
    它的全部價值，被拿掉不會有任何症狀。"""

    SYSTEMD = REPO_ROOT / "deploy" / "systemd"
    SERVICE = SYSTEMD / "report-mark-freshness.service"
    TIMER = SYSTEMD / "report-mark-freshness.timer"

    def _live(self, path: Path) -> str:
        return "\n".join(
            ln for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )

    def test_unit_files_exist(self):
        for p in (self.SERVICE, self.TIMER):
            self.assertTrue(p.is_file(), f"缺少 {p.relative_to(REPO_ROOT)}")

    def test_service_declares_onfailure_alert(self):
        self.assertIn("OnFailure=report-mark-alert@%n.service", self._live(self.SERVICE))

    def test_service_runs_the_checker(self):
        self.assertIn("scripts/check_batch_freshness.py", self._live(self.SERVICE))

    def test_service_hardcodes_home(self):
        live = self._live(self.SERVICE)
        self.assertIn("Environment=HOME=/home/kashionz", live)
        self.assertNotIn("HOME=%h", live)

    def test_service_uses_shared_env_file_optionally(self):
        """`EnvironmentFile=-`：env 檔還沒裝時 unit 仍要能載入（`:?` 自己會報錯）。"""
        self.assertIn("EnvironmentFile=-/etc/default/report-mark-sync", self._live(self.SERVICE))

    def test_timer_is_daily_and_persistent(self):
        live = self._live(self.TIMER)
        self.assertIn("OnCalendar=*-*-* 08:30:00", live)
        self.assertIn("Persistent=true", live)

    def test_timer_does_not_collide_with_sync_or_backup(self):
        """同時跑會互搶 NAS 與磁碟 I/O；sync 是整點 00/3、backup 是 03:30。"""
        mine = self._live(self.TIMER)
        others = [
            self._live(self.SYSTEMD / "report-mark-sync.timer"),
            self._live(self.SYSTEMD / "report-mark-backup.timer"),
        ]
        my_cal = [ln for ln in mine.splitlines() if ln.startswith("OnCalendar=")]
        self.assertEqual(len(my_cal), 1)
        for other in others:
            self.assertNotIn(my_cal[0], other)


if __name__ == "__main__":
    unittest.main()
