import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import sync_new_reports as snr  # noqa: E402

from scripts import _claude_cli as cc  # noqa: E402


@dataclass
class _Tag:
    market: str | None
    is_research: bool


class SyncNewReportsTests(unittest.TestCase):
    def test_parse_rsync_delta_keeps_only_files_with_known_ext(self):
        lines = [
            "新增/",  # 目錄列 → 略過
            "新增/0701 報告.pdf",
            "note.txt",  # 非目標副檔名 → 略過
            "a/b/Taiwan daily.docx",
            "",  # 空行
        ]
        out = snr.parse_rsync_delta(lines, Path("/local"))
        self.assertEqual(
            out,
            [
                Path("/local/新增/0701 報告.pdf"),
                Path("/local/a/b/Taiwan daily.docx"),
            ],
        )

    def test_parse_rsync_delta_dedupes_preserving_order(self):
        out = snr.parse_rsync_delta(["x.pdf", "x.pdf", "y.PDF"], Path("/d"))
        self.assertEqual(out, [Path("/d/x.pdf"), Path("/d/y.PDF")])

    def test_parse_rsync_delta_dedupes_case_insensitively(self):
        # 來源為大小寫不敏感檔系統；同檔不同大小寫應視為同一檔，保留首次出現的原始路徑
        out = snr.parse_rsync_delta(["x.PDF", "x.pdf"], Path("/d"))
        self.assertEqual(out, [Path("/d/x.PDF")])

    def test_skip_before_tag_priority_order(self):
        self.assertEqual(snr.skip_before_tag(True, False, False), "skip_admin")
        self.assertEqual(snr.skip_before_tag(False, True, False), "skip_scanned")
        self.assertEqual(snr.skip_before_tag(False, False, True), "skip_exists")
        self.assertIsNone(snr.skip_before_tag(False, False, False))

    def test_skip_after_tag(self):
        self.assertEqual(snr.skip_after_tag(None), "skip_untagged")
        self.assertEqual(snr.skip_after_tag(_Tag(None, True)), "skip_non_research")
        self.assertEqual(snr.skip_after_tag(_Tag("TW", False)), "skip_non_research")
        self.assertIsNone(snr.skip_after_tag(_Tag("TW", True)))


class FallbackReportDateTests(unittest.TestCase):
    def _tmp_file_with_mtime(self, day: date) -> Path:
        fd, name = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        path = Path(name)
        ts = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_existing_report_date_wins(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            explicit = date(2024, 5, 4)
            self.assertEqual(
                snr.fallback_report_date_from_mtime(explicit, path, created_at=None),
                explicit,
            )
        finally:
            path.unlink()

    def test_live_sync_trusts_same_day_mtime_without_created_at(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            self.assertEqual(
                snr.fallback_report_date_from_mtime(None, path, created_at=None),
                date(2026, 6, 25),
            )
        finally:
            path.unlink()

    def test_backfill_still_rejects_copy_like_mtime(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            self.assertIsNone(
                snr.fallback_report_date_from_mtime(
                    None, path, created_at=date(2026, 6, 25)
                )
            )
        finally:
            path.unlink()


class SyncScriptBacklogStepsTests(unittest.TestCase):
    """排程殼的兩段「跨全語料積壓」必須存在、必須限量、且不能被新研報閘門擋住。

    這兩段的失效方式都是**靜默的**：
    - 綁到 `--hashes-file` 或縮進 `if [ -s "$HASHES" ]` 裡 → 沒有新研報的日子完全不動，
      而 sync 仍然 rc=0、unit 不會紅（觀點雷達就是這樣從 2026-07-16 靜止兩週）。
    - `--limit` 被拿掉 → 連續佔住 claude 鎖數十小時，期間每輪匯入撞鎖 rc=75，
      那批研報還會從 rsync delta 消失，得靠 `--all-local` 手動補。

    標題那段補的是一年以上的舊檔（2026-08-06 實測 11,879 篇缺 title，近一年只差 1 篇），
    它們永遠不會出現在任何一輪的 `--hashes-file` 裡——所以「已經有 4b 了」不能取代它。
    """

    def setUp(self):
        self.src = (
            Path(__file__).resolve().parents[1] / "scripts" / "sync_new_reports.sh"
        ).read_text(encoding="utf-8")

    # 一律錨在「呼叫形式」而不是裸檔名：這幾段的註解本身就會提到 generate_titles.py，
    # 用裸檔名數出現次數會數到註解（本測試第一版就是這樣紅的）。
    _INVOKE = "run python scripts/"

    def _invocations(self, script: str) -> list[int]:
        needle = f"{self._INVOKE}{script}"
        out, i = [], self.src.find(needle)
        while i != -1:
            out.append(i)
            i = self.src.find(needle, i + 1)
        return out

    def _title_backlog_call(self) -> str:
        """第二次呼叫 generate_titles.py 的那段（第一次是 4b 的本輪新研報）。"""
        calls = self._invocations("generate_titles.py")
        self.assertEqual(len(calls), 2, "排程殼應有兩段標題批次：本輪新研報 ＋ 歷史積壓")
        return self.src[calls[1]:calls[1] + 300]

    def test_signal_step_is_limited(self):
        i = self._invocations("extract_signals.py")[0]
        self.assertIn("--limit", self.src[i:i + 300])
        self.assertRegex(self.src, r"SIGNAL_LIMIT=\$\{SYNC_SIGNAL_LIMIT:-\d+\}")

    def test_title_backlog_step_exists_and_is_limited(self):
        call = self._title_backlog_call()
        self.assertIn("--limit", call)
        self.assertRegex(
            self.src, r"TITLE_BACKLOG_LIMIT=\$\{SYNC_TITLE_BACKLOG_LIMIT:-\d+\}"
        )

    def test_title_backlog_step_is_not_bound_to_this_round(self):
        """綁 --hashes-file 就退化成 4b 的重複，舊檔永遠補不到。"""
        self.assertNotIn("--hashes-file", self._title_backlog_call())

    def test_backlog_steps_run_outside_the_new_reports_gate(self):
        """兩段都必須在 `if [ -s "$HASHES" ] … else … fi` 之後。

        以 else 分支的 log 字串當錨：它只出現在閘門的 else 裡，排在它之後就代表
        不在閘門內。縮進閘門裡的話沒有新研報的日子這兩段就完全不跑。
        """
        gate_else = self.src.index("本次無新研報入庫")
        self.assertLess(gate_else, self._invocations("extract_signals.py")[0])
        self.assertLess(gate_else, self._invocations("generate_titles.py")[1])

    def test_title_backlog_excludes_this_rounds_hashes(self):
        """審查 L2：積壓段依 report_date DESC 取，本輪新研報恰好排最前；不排除的話 4b
        失敗的那篇同一輪會被打兩次、跳過名單記兩次，「連續 3 輪」實際約 2 輪就跳。

        只在 $HASHES 非空時才帶（空檔或不存在時 read_hashes_file 沒東西可排）。"""
        call = self._title_backlog_call()
        self.assertIn('--exclude-hashes-file "$TITLE_BACKLOG_EXCLUDE"', call)
        self.assertIn('${TITLE_BACKLOG_EXCLUDE:+', call)
        self.assertIn('if [ -s "$HASHES" ]; then TITLE_BACKLOG_EXCLUDE="$HASHES"; fi', self.src)

    def test_title_backlog_failure_is_recorded(self):
        """best-effort 不等於無聲：非零退出要留一筆給 /api/progress 的 unit_failures。"""
        self.assertIn('record_unit_failure "generate_titles_backlog"', self.src)


class TagViaCliFailureReasonTests(unittest.TestCase):
    """標註失敗必須說得出原因。

    這是本 repo 最貴的靜默失效：標註失敗 ⇒ 該檔記成 `skip_untagged` ⇒ **不入庫**，
    而排程殼只印一行「本次無新研報入庫」——與「NAS 真的沒有新檔」在畫面上完全一樣。
    2026-08-12 那輪 rsync 帶進 33 檔全被吞掉，四天後才被發現。
    """

    def _run_with(self, result):
        with mock.patch.object(snr, "run_claude", return_value=result):
            return snr._tag_via_cli("x.pdf", "內文")

    def test_cli_error_is_returned_verbatim(self):
        tag, err = self._run_with(cc.CliResult(None, "CLI 退出碼 1：Credit balance too low"))
        self.assertIsNone(tag)
        self.assertIn("Credit balance", err)

    def test_unparseable_response_is_distinct_from_cli_failure(self):
        """「CLI 壞了」與「CLI 回了但內容不合格」處置完全不同，不可共用一句話。"""
        with mock.patch.object(snr, "run_claude", return_value=cc.CliResult("不是 JSON", None)):
            tag, err = snr._tag_via_cli("x.pdf", "內文")
        self.assertIsNone(tag)
        self.assertIn("解析", err)
        self.assertNotIn("退出碼", err)

    def test_success_returns_tag_and_no_error(self):
        payload = (
            '{"market":"TW","is_research":true,"confidence":0.9,'
            '"instrument_types":["equity"],"relates_stock":true,'
            '"relates_futures":false,"stock_targets":["2330"],"futures_targets":[]}'
        )
        with mock.patch.object(snr, "run_claude", return_value=cc.CliResult(payload, None)):
            tag, err = snr._tag_via_cli("x.pdf", "內文")
        self.assertIsNotNone(tag)
        self.assertIsNone(err)

    def test_missing_cli_propagates_instead_of_becoming_skip_untagged(self):
        """環境壞了要中止整批，不可讓每一篇都靜靜變成 skip_untagged 然後 rc=0。"""
        with mock.patch.object(
            snr, "run_claude", side_effect=cc.CliNotFoundError("不在 PATH")
        ):
            with self.assertRaises(cc.CliNotFoundError):
                snr._tag_via_cli("x.pdf", "內文")


if __name__ == "__main__":
    unittest.main()
