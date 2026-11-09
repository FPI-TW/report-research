# tests/test_brief.py
"""每日簡報：純函式（窗期／素材／解析）＋ 三支端點（TestClient + 假 deps）。

DB 查詢本身（_signal_changes_sql 的 lag 視窗、新鮮度過濾）不在此檔——那需要真的
pgvector 容器，與 tests/test_schema_constraints.py 同一類。此處守的是**接線與判斷**：
窗期算錯、模型輸出沒收乾淨、空狀態被當成錯誤，這三種失效都不會有任何錯誤訊息。
"""
import argparse
import contextlib
import io
import os
import sys
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import generate_brief  # noqa: E402  (scripts/ 已在 sys.path)
from fastapi.testclient import TestClient  # noqa: E402

from app.services.brief import (  # noqa: E402
    SIGNAL_MAX_REPORT_AGE_DAYS,
    BriefReport,
    BriefRow,
    BriefSignalChange,
    BriefWindowMix,
    build_material,
    build_prompt,
    fetch_following,
    fetch_previous,
    fetch_signal_changes,
    market_zh,
    parse_brief,
    rating_zh,
    summarize_window_mix,
    with_instrument_names,
)
from web import deps, server  # noqa: E402

NOW = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)


def _report(n=1, title="基板漲價超預期，重申買進", summary="摘要一句話。"):
    return BriefReport(
        report_id=f"rep-{n}",
        file_hash=f"{n:064d}",
        file_name=f"daiwa-{n}.pdf",
        title=title,
        market="TW",
        source="daiwa",
        report_date=date(2026, 8, 7),
        summary=summary,
    )


def _change(**kw):
    base = dict(
        market="TW",
        instrument_code="8046",
        broker="daiwa",
        report_date=date(2026, 8, 7),
        rating_from="neutral",
        rating_to="buy",
        target_from=100.0,
        target_to=130.0,
        target_currency="TWD",
        instrument_name=None,
    )
    base.update(kw)
    return BriefSignalChange(**base)


class WindowTests(unittest.TestCase):
    """窗期算錯只會讓簡報少幾篇，沒有任何錯誤訊息——所以它是純函式且有測試。"""

    def test_no_previous_brief_looks_back_24h(self):
        start, end = generate_brief.resolve_window(None, NOW)
        self.assertEqual(end, NOW)
        self.assertEqual(start, NOW - timedelta(hours=24))

    def test_continues_from_previous_window_without_gap(self):
        previous_end = NOW - timedelta(hours=30)
        start, _ = generate_brief.resolve_window(previous_end, NOW)
        self.assertEqual(start, previous_end, "窗期必須從上一份的結尾接上，否則中間那段永遠沒人報")

    def test_lookback_is_capped(self):
        """久未執行後不可一次把幾週的東西全塞進 prompt。"""
        previous_end = NOW - timedelta(days=90)
        start, _ = generate_brief.resolve_window(previous_end, NOW, max_lookback_days=7)
        self.assertEqual(start, NOW - timedelta(days=7))

    def test_not_after_caps_the_end_and_lookback_counts_from_it(self):
        """過去的日子不可把回看上限算在現在頭上：否則起點會跑到終點之後，整段窗期被吃掉。"""
        not_after = NOW - timedelta(days=30)
        previous_end = not_after - timedelta(days=2)
        self.assertEqual(
            generate_brief.resolve_window(previous_end, NOW, not_after=not_after),
            (previous_end, not_after),
        )
        start, end = generate_brief.resolve_window(previous_end, NOW, max_lookback_days=1, not_after=not_after)
        self.assertEqual((start, end), (not_after - timedelta(days=1), not_after))
        start, end = generate_brief.resolve_window(None, NOW, not_after=not_after)
        self.assertEqual((start, end), (not_after - timedelta(hours=24), not_after))

    def test_not_after_later_than_now_does_not_extend_past_now(self):
        _, end = generate_brief.resolve_window(None, NOW, not_after=NOW + timedelta(hours=5))
        self.assertEqual(end, NOW)


class _TzMixin:
    """把行程時區固定成 UTC+8，日界線的斷言才不隨主機變動（POSIX 寫法，不需要 tzdata）。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"TZ": "CST-8"})
        patcher.start()
        time.tzset()

        def restore():
            patcher.stop()
            time.tzset()

        self.addCleanup(restore)


def _at(day, hour=1):
    """該日 UTC `hour` 時（台北 9 時），排程產生簡報的典型時間點。"""
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _brief(day, start=None, end=None):
    return BriefRow(
        brief_date=day,
        window_start=start or _at(day - timedelta(days=1)),
        window_end=end or _at(day),
        markdown="## 今日重點\n- 舊內容",
        report_ids=["rep-old"],
        report_count=1,
        signal_count=0,
        model="gpt-6-luna",
        created_at=_at(day),
    )


NOV = {d: date(2026, 11, d) for d in range(1, 8)}
CLOCK = datetime(2026, 11, 5, 7, 30, tzinfo=timezone.utc)  # 11/05 台北 15:30


class PlanWindowTests(_TzMixin, unittest.TestCase):
    """窗期首尾相接是這張表的不變量（2026-11-05 實測 63 份裡 61 份逐份相接、零重疊）。"""

    def test_existing_row_keeps_its_stored_window(self):
        """回歸：已有 11/05 時下 --date 11-04 --force，舊簡報被換成「現在往前 24 小時」的素材。"""
        row = _brief(NOV[4])
        window = generate_brief.plan_window(NOV[4], row, _brief(NOV[3]), _brief(NOV[5]), CLOCK)
        self.assertEqual(window, (row.window_start, row.window_end))

    def test_existing_row_of_today_keeps_its_stored_window(self):
        row = _brief(NOV[5])
        window = generate_brief.plan_window(NOV[5], row, _brief(NOV[4]), None, CLOCK)
        self.assertEqual(window, (row.window_start, row.window_end))

    def test_existing_row_is_not_clamped_by_lookback(self):
        """停擺多日後的那份窗期本來就長，或是在 --max-lookback-days 之外的舊日子：一律不動。"""
        row = _brief(NOV[4], start=_at(NOV[4]) - timedelta(days=30))
        window = generate_brief.plan_window(NOV[4], row, None, None, CLOCK, max_lookback_days=7)
        self.assertEqual(window, (row.window_start, row.window_end))

    def test_scheduled_run_ends_now(self):
        previous = _brief(NOV[4])
        window = generate_brief.plan_window(NOV[5], None, previous, None, CLOCK)
        self.assertEqual(window, (previous.window_end, CLOCK))

    def test_scheduled_run_without_any_brief_looks_back_24h(self):
        window = generate_brief.plan_window(NOV[5], None, None, None, CLOCK)
        self.assertEqual(window, (CLOCK - timedelta(hours=24), CLOCK))

    def test_backfill_is_bounded_by_the_following_brief(self):
        """回歸：終點若是現在，就和後面的簡報重疊。這裡 11/05 因停擺超過回看上限而留了縫。"""
        previous = _brief(NOV[3])
        following = _brief(NOV[5], start=previous.window_end + timedelta(hours=3))
        window = generate_brief.plan_window(NOV[4], None, previous, following, CLOCK)
        self.assertEqual(window, (previous.window_end, following.window_start))

    def test_backfill_between_contiguous_briefs_is_empty(self):
        """窗期首尾相接時，缺的那天的研報全在後一份裡，沒有東西可補。"""
        previous = _brief(NOV[3])
        following = _brief(NOV[5], start=previous.window_end)
        start, end = generate_brief.plan_window(NOV[4], None, previous, following, CLOCK)
        self.assertEqual(start, end)

    def test_backfill_with_overlapping_neighbours_is_empty(self):
        previous = _brief(NOV[3])
        following = _brief(NOV[5], start=previous.window_end - timedelta(hours=1))
        start, end = generate_brief.plan_window(NOV[4], None, previous, following, CLOCK)
        self.assertGreaterEqual(start, end)

    def test_backfill_without_later_brief_ends_with_the_target_day(self):
        """沒有後面的簡報也不可到現在：那天的簡報會吞掉之後每一天的素材。"""
        previous = _brief(NOV[1])
        _, end = generate_brief.plan_window(NOV[3], None, previous, None, CLOCK)
        self.assertEqual(end, datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc), "台北 11/04 00:00")

    def test_following_brief_after_the_target_day_does_not_extend_the_window(self):
        previous = _brief(NOV[1])
        following = _brief(NOV[5], start=_at(NOV[4]))
        _, end = generate_brief.plan_window(NOV[3], None, previous, following, CLOCK)
        self.assertEqual(end, datetime(2026, 11, 3, 16, 0, tzinfo=timezone.utc))

    def test_backfill_before_the_first_brief_looks_back_24h_from_its_end(self):
        following = _brief(NOV[5], start=_at(NOV[3]))
        start, end = generate_brief.plan_window(NOV[2], None, None, following, CLOCK)
        self.assertEqual(end, datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(start, end - timedelta(hours=24))

    def test_consecutive_backfills_tile_with_the_scheduled_run(self):
        """由舊到新逐日補，再接今天的排程：沒有縫也沒有重疊。"""
        rows = [_brief(NOV[1])]
        for day in (NOV[2], NOV[3], NOV[4], NOV[5]):
            start, end = generate_brief.plan_window(day, None, rows[-1], None, CLOCK)
            rows.append(_brief(day, start=start, end=end))
        for earlier, later in zip(rows, rows[1:]):
            self.assertEqual(later.window_start, earlier.window_end)
        self.assertEqual(rows[-1].window_end, CLOCK)

    def test_end_of_local_day_is_utc(self):
        """帶 +08:00 的值 `.date()` 與落庫讀回的 UTC 差一天，同窗期重跑的新鮮度下限就不同。"""
        end = generate_brief.end_of_local_day(NOV[4])
        self.assertEqual(end.utcoffset(), timedelta(0))
        self.assertEqual(end.date(), NOV[4])


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


class GenerateWindowTests(unittest.IsolatedAsyncioTestCase):
    """窗期要真的接到素材查詢與落庫上。不呼叫 LLM（call_cli 是假的）、不連 DB。"""

    async def _generate(self, *, target, existing=None, previous=None, following=None, force=True):
        svc = generate_brief.brief_service
        args = argparse.Namespace(
            date=target.isoformat(), force=force, after_hour=0, dry_run=False, model="gpt-6-luna", max_lookback_days=7,
        )
        mocks = {
            "fetch_by_date": mock.AsyncMock(return_value=existing),
            "fetch_previous": mock.AsyncMock(return_value=previous),
            "fetch_following": mock.AsyncMock(return_value=following),
            "fetch_window_reports": mock.AsyncMock(return_value=[_report()]),
            "count_window_reports": mock.AsyncMock(return_value=1),
            "fetch_signal_changes": mock.AsyncMock(return_value=[]),
            "upsert_brief": mock.AsyncMock(),
        }
        call_cli = mock.MagicMock(return_value=(ParseTests.BODY, None))
        out = io.StringIO()
        with mock.patch.multiple(svc, **mocks), \
                mock.patch.object(generate_brief, "SessionFactory", _Session), \
                mock.patch.object(generate_brief, "call_cli", call_cli), \
                mock.patch.object(generate_brief, "blocked_today", return_value=False), \
                mock.patch.object(generate_brief, "claude_cli_lock_or_exit", lambda name: contextlib.nullcontext()), \
                contextlib.redirect_stdout(out):
            rc = await generate_brief.generate(args)
        return rc, mocks, call_cli, out.getvalue()

    async def test_force_on_past_date_reuses_stored_window(self):
        row = _brief(NOV[4])
        rc, mocks, call_cli, _ = await self._generate(target=NOV[4], existing=row)
        self.assertEqual(rc, 0)
        window = (row.window_start, row.window_end)
        for name in ("fetch_window_reports", "count_window_reports", "fetch_signal_changes"):
            self.assertEqual(mocks[name].await_args.args[1:3], window, name)
        kwargs = mocks["upsert_brief"].await_args.kwargs
        self.assertEqual((kwargs["window_start"], kwargs["window_end"]), window, "落庫的窗期不可被覆寫")
        self.assertEqual(kwargs["brief_date"], NOV[4])
        call_cli.assert_called_once()
        mocks["fetch_previous"].assert_not_awaited()
        mocks["fetch_following"].assert_not_awaited()

    async def test_existing_without_force_is_still_a_noop(self):
        rc, mocks, call_cli, out = await self._generate(target=NOV[4], existing=_brief(NOV[4]), force=False)
        self.assertEqual(rc, 0)
        self.assertIn("no-op", out)
        mocks["fetch_window_reports"].assert_not_awaited()
        mocks["upsert_brief"].assert_not_awaited()
        call_cli.assert_not_called()

    async def test_backfill_uses_neighbours_for_the_window(self):
        previous = _brief(NOV[3])
        following = _brief(NOV[5], start=previous.window_end + timedelta(hours=3))
        rc, mocks, _, _ = await self._generate(target=NOV[4], previous=previous, following=following, force=False)
        self.assertEqual(rc, 0)
        kwargs = mocks["upsert_brief"].await_args.kwargs
        self.assertEqual((kwargs["window_start"], kwargs["window_end"]), (previous.window_end, following.window_start))

    async def test_empty_window_writes_nothing_and_skips_the_llm(self):
        previous = _brief(NOV[3])
        following = _brief(NOV[5], start=previous.window_end)
        rc, mocks, call_cli, out = await self._generate(target=NOV[4], previous=previous, following=following)
        self.assertEqual(rc, 0)
        self.assertIn("窗期為空", out)
        mocks["fetch_window_reports"].assert_not_awaited()
        mocks["upsert_brief"].assert_not_awaited()
        call_cli.assert_not_called()


class SignalAgeBaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_rerun_with_stored_window_keeps_the_freshness_floor(self):
        """SIGNAL_MAX_REPORT_AGE_DAYS 的基準是 end：沿用原窗期重跑，新鮮度下限與當初相同，不隨重跑時間改變。"""
        row = _brief(NOV[4])
        session = mock.AsyncMock()
        session.execute.return_value = mock.MagicMock(all=mock.MagicMock(return_value=[]))
        await fetch_signal_changes(session, row.window_start, row.window_end)
        params = session.execute.await_args.args[1]
        self.assertEqual((params["start"], params["end"]), (row.window_start, row.window_end))
        self.assertEqual(params["min_report_date"], NOV[4] - timedelta(days=SIGNAL_MAX_REPORT_AGE_DAYS))


class NeighbourQueryTests(unittest.IsolatedAsyncioTestCase):
    """方向寫反不會有錯誤訊息，只會拿到離目標日最遠的那份。"""

    async def _sql(self, fetch):
        session = mock.AsyncMock()
        session.execute.return_value = mock.MagicMock(first=mock.MagicMock(return_value=None))
        self.assertIsNone(await fetch(session, NOV[4]))
        stmt, params = session.execute.await_args.args
        self.assertEqual(params, {"d": NOV[4]})
        return " ".join(str(stmt).split())

    async def test_previous_is_nearest_earlier(self):
        sql = await self._sql(fetch_previous)
        self.assertIn("brief_date < :d ORDER BY brief_date DESC LIMIT 1", sql)

    async def test_following_is_nearest_later(self):
        sql = await self._sql(fetch_following)
        self.assertIn("brief_date > :d ORDER BY brief_date ASC LIMIT 1", sql)


class WindowMixTests(unittest.TestCase):
    """概況卡的市場分布與券商數：分組結果收斂錯了不會有任何錯誤訊息，只會畫出錯的長條。"""

    def test_sums_per_market_and_counts_distinct_brokers(self):
        mix = summarize_window_mix([
            ("TW", "kgi", 3), ("TW", "yuanta", 2), ("US", "kgi", 4), ("HK", None, 1),
        ])
        self.assertEqual(mix.markets, [("TW", 5), ("US", 4), ("HK", 1)])
        self.assertEqual(mix.broker_count, 2, "同一家券商跨市場只算一次；沒有來源的不算券商")

    def test_unlabelled_market_goes_last_and_ties_are_stable(self):
        mix = summarize_window_mix([(None, "kgi", 9), ("US", "kgi", 2), ("HK", "kgi", 2)])
        self.assertEqual(mix.markets, [("HK", 2), ("US", 2), (None, 9)])

    def test_empty_window(self):
        self.assertEqual(summarize_window_mix([]), BriefWindowMix(markets=[], broker_count=0))


class MaterialTests(unittest.TestCase):
    def test_lists_reports_with_display_title_and_summary(self):
        text = build_material([_report()], [], total_reports=1)
        self.assertIn("基板漲價超預期，重申買進", text)
        self.assertIn("摘要一句話。", text)
        self.assertIn("台股", text, "市場代碼要先中文化，模型才不會照抄 TW")

    def test_falls_back_to_file_name_when_title_missing(self):
        """title 是漸進補的，缺值是常態——素材裡不能因此變成空字串。"""
        text = build_material([_report(title=None)], [], total_reports=1)
        self.assertIn("daiwa-1.pdf", text)

    def test_says_how_many_were_left_out(self):
        """截斷必須說出來：靜默只列一部分會讓簡報看起來「那天只有這幾篇」。"""
        text = build_material([_report()], [], total_reports=50)
        self.assertIn("另有 49 篇未列出", text)

    def test_renders_rating_and_target_change(self):
        text = build_material([], [_change()], total_reports=0)
        self.assertIn("中立→買進", text)
        self.assertIn("+30.0%", text)

    def test_ignores_target_move_below_threshold(self):
        """同一家券商微調不算變動；SQL 已擋一次，素材這層不可又把它寫回去。

        （比對數字而不是「目標價」三個字——那三個字也出現在區塊標題裡，斷言會恆真。）
        """
        text = build_material(
            [], [_change(rating_from="buy", rating_to="buy", target_from=100.0, target_to=100.5)],
            total_reports=0,
        )
        self.assertNotIn("100.5", text)
        self.assertNotIn("→", text)

    def test_empty_sections_are_explicit(self):
        text = build_material([], [], total_reports=0)
        self.assertEqual(text.count("（無）"), 2, "兩段都要顯式寫「無」，不可整段消失")

    def test_prompt_embeds_material_and_date(self):
        prompt = build_prompt(date(2026, 8, 7), "素材內容")
        self.assertIn("2026-08-07", prompt)
        self.assertIn("素材內容", prompt)
        self.assertIn("繁體中文", prompt)


class NameJoinTests(unittest.TestCase):
    def test_attaches_name_and_keeps_missing_as_none(self):
        changes = [_change(), _change(instrument_code="2330")]
        out = with_instrument_names(changes, {("TW", "8046"): "南電"})
        self.assertEqual(out[0].instrument_name, "南電")
        self.assertIsNone(out[1].instrument_name, "查無名稱要留 None，呈現端回退代號")


class ParseTests(unittest.TestCase):
    BODY = "## 今日重點\n- 一句話\n\n## 市場焦點\n台股走勢平穩，量能收斂。\n\n## 評等與目標價變動\n本期無"

    def test_plain_markdown_passes_through(self):
        self.assertEqual(parse_brief(self.BODY), self.BODY)

    def test_strips_outer_code_fence(self):
        self.assertEqual(parse_brief(f"```markdown\n{self.BODY}\n```"), self.BODY)

    def test_drops_preamble_before_first_heading(self):
        """模型偶爾會先講一句「好的，以下是…」，那句話會直接出現在頁面上。"""
        self.assertEqual(parse_brief(f"好的，以下是今天的簡報：\n\n{self.BODY}"), self.BODY)

    def test_rejects_empty_and_too_short(self):
        self.assertIsNone(parse_brief(None))
        self.assertIsNone(parse_brief("   "))
        self.assertIsNone(parse_brief("## 今日重點\n無"))


class LabelTests(unittest.TestCase):
    def test_rating_and_market_labels(self):
        self.assertEqual(rating_zh("buy"), "買進")
        self.assertEqual(rating_zh("unknown"), "未提供")
        self.assertEqual(market_zh("TW"), "台股")

    def test_unknown_codes_pass_through_instead_of_disappearing(self):
        self.assertEqual(rating_zh("weird"), "weird")
        self.assertEqual(market_zh("ZZ"), "ZZ")


# ── 端點 ──────────────────────────────────────────────────────────

class HttpModelTests(unittest.TestCase):
    """簡報交給 `run_claude`（遷移 PR-12；PR-M 起簡報自己的 CLI 版刪除）：不 spawn 子行程；帳號層級錯誤
    與白名單外的 model，main 回 rc=2、不寫 brief_failures.log（那是「這一天產生失敗」的紀錄）。"""

    def _main(self, argv):
        old = sys.argv
        sys.argv = ["generate_brief.py", *argv]
        try:
            return generate_brief.main()
        finally:
            sys.argv = old

    def _transport(self, response):
        import httpx

        from app.services import llm_http as lh

        lh._transport = httpx.MockTransport(lambda req: response)
        lh._reset_clients()
        self.addCleanup(lambda: (setattr(lh, "_transport", None), lh._reset_clients()))

    _ENV = {"OPENAI_API_KEY": "fixed-test-secret-openai0", "OPENAI_BASE_URL": "https://api.example.test"}

    def test_preflight_rejects_missing_key_before_anything_runs(self):
        import contextlib
        import io
        from unittest import mock

        with mock.patch.object(generate_brief, "generate") as gen, \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
             contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as ctx:
            self._main(["--model", "gpt-6-luna"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("OPENAI_API_KEY", err.getvalue())
        gen.assert_not_called()

    def test_http_path_does_not_spawn(self):
        from unittest import mock

        import httpx

        self._transport(httpx.Response(401, json={"error": {"message": "Authentication Fails"}}))
        with mock.patch.dict(os.environ, self._ENV), mock.patch("subprocess.run") as run:
            with self.assertRaises(generate_brief.CliNotFoundError):
                generate_brief.call_cli("素材", "gpt-6-luna")
        run.assert_not_called()
        self.assertFalse(hasattr(generate_brief, "build_cli_args"))
        self.assertFalse(hasattr(generate_brief, "_spawn_cli"))

    def test_non_whitelisted_model_is_rc_2_without_failure_log(self):
        """PR-M：claude-*（PR-M 前走簡報自己的 CLI 版）→ 預檢 rc=2；就算繞過預檢，call_cli 也拋整批中止型
        例外、main 回 rc=2，不記成「這一天產生失敗」。"""
        import contextlib
        import io
        from unittest import mock

        with mock.patch.dict(os.environ, self._ENV), mock.patch.object(generate_brief, "generate") as gen, \
                contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as ctx:
            self._main(["--model", "claude-sonnet-5"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("不在白名單", err.getvalue())
        gen.assert_not_called()

        async def fake_generate(args):
            generate_brief.call_cli("素材", args.model)

        with mock.patch.dict(os.environ, self._ENV), mock.patch("subprocess.run") as run, \
                mock.patch.object(generate_brief, "require_llm_key"), \
                mock.patch.object(generate_brief, "generate", side_effect=fake_generate), \
                mock.patch.object(generate_brief, "record_failure") as rec, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            rc = self._main(["--model", "claude-sonnet-5"])
        self.assertEqual(rc, 2)
        self.assertIn("白名單", err.getvalue())
        rec.assert_not_called()
        run.assert_not_called()

    def test_main_returns_2_on_account_error(self):
        import contextlib
        import io
        from unittest import mock

        import httpx

        async def fake_generate(args):
            generate_brief.call_cli("素材", args.model)

        self._transport(httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(generate_brief, "require_llm_key"), \
             mock.patch.object(generate_brief, "generate", side_effect=fake_generate), \
             mock.patch.object(generate_brief, "record_failure") as rec, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            rc = self._main(["--model", "gpt-6-luna"])
        self.assertEqual(rc, 2)
        self.assertIn("API[quota]", err.getvalue())
        rec.assert_not_called()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        raise AssertionError("端點不應直接打 DB（fetch 已被 monkeypatch）")


def _row(brief_date=date(2026, 8, 7), report_ids=("rep-1",), report_count=1):
    return BriefRow(
        brief_date=brief_date,
        window_start=NOW - timedelta(hours=24),
        window_end=NOW,
        markdown="## 今日重點\n- 一句話",
        report_ids=list(report_ids),
        report_count=report_count,
        signal_count=2,
        model="claude-sonnet-5",
        created_at=NOW,
    )


def _authed_client():
    client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


class BriefApiTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            name: getattr(deps, name)
            for name in (
                "SessionFactory",
                "fetch_latest_brief",
                "fetch_brief_by_date",
                "fetch_brief_dates",
                "fetch_brief_reports",
                "fetch_brief_window_mix",
            )
        }
        deps.SessionFactory = _FakeSession
        deps.fetch_brief_dates = self._dates
        deps.fetch_brief_reports = self._reports
        deps.fetch_brief_window_mix = self._mix
        self.client = _authed_client()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(deps, name, value)

    async def _dates(self, session, limit=30):
        return [date(2026, 8, 7), date(2026, 8, 6)]

    async def _reports(self, session, ids):
        return [_report()] if ids else []

    async def _mix(self, session, start, end):
        self.mix_window = (start, end)
        return BriefWindowMix(markets=[("TW", 3), (None, 1)], broker_count=2)

    def test_latest_returns_payload_with_sources(self):
        async def fetch(session):
            return _row()

        deps.fetch_latest_brief = fetch
        body = self.client.get("/api/brief/latest").json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["brief"]["brief_date"], "2026-08-07")
        self.assertEqual(body["brief"]["reports"][0]["file_hash"], f"{1:064d}")
        self.assertEqual(body["available_dates"], ["2026-08-07", "2026-08-06"])

    def test_payload_carries_market_mix_for_the_brief_window(self):
        async def fetch(session, d):
            return _row(brief_date=d)

        deps.fetch_brief_by_date = fetch
        body = self.client.get("/api/brief/2026-08-07").json()
        self.assertEqual(
            body["brief"]["markets"],
            [{"market": "TW", "report_count": 3}, {"market": None, "report_count": 1}],
        )
        self.assertEqual(body["brief"]["broker_count"], 2)
        self.assertEqual(self.mix_window, (NOW - timedelta(hours=24), NOW), "算的是這份簡報自己的窗期")

    def test_market_mix_failure_degrades_without_breaking_the_brief(self):
        """派生資料 fail-open：分布查不到，簡報本文與來源照常回。"""
        async def fetch(session):
            return _row()

        async def broken(session, start, end):
            raise RuntimeError("db hiccup")

        deps.fetch_latest_brief = fetch
        deps.fetch_brief_window_mix = broken
        with self.assertLogs("web.routers.brief", level="WARNING"):
            resp = self.client.get("/api/brief/latest")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()["brief"]
        self.assertIsNone(body["markets"])
        self.assertIsNone(body["broker_count"])
        self.assertEqual(len(body["reports"]), 1)

    def test_latest_without_any_brief_is_pending_not_404(self):
        """空狀態不是錯誤：批次還沒跑是每天早上都會出現的正常狀態。"""
        async def fetch(session):
            return None

        deps.fetch_latest_brief = fetch
        resp = self.client.get("/api/brief/latest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending")
        self.assertIsNone(resp.json()["brief"])

    def test_missing_source_reports_do_not_break_the_brief(self):
        """report_ids 刻意無 FK：語料重建後舊 id 會查不到，簡報本文仍然有效。"""
        async def fetch(session):
            return _row(report_ids=["gone"], report_count=1)

        async def none_found(session, ids):
            return []

        deps.fetch_latest_brief = fetch
        deps.fetch_brief_reports = none_found
        body = self.client.get("/api/brief/latest").json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["brief"]["reports"], [])
        self.assertEqual(body["brief"]["report_count"], 1, "篇數是決定性計數，不隨連結消失而改變")

    def test_by_date_404_when_absent(self):
        async def fetch(session, d):
            return None

        deps.fetch_brief_by_date = fetch
        self.assertEqual(self.client.get("/api/brief/2026-08-01").status_code, 404)

    def test_by_date_rejects_malformed_date(self):
        self.assertEqual(self.client.get("/api/brief/not-a-date").status_code, 422)

    def test_dates_route_is_not_swallowed_by_the_param_route(self):
        """/dates 必須排在 /{brief_date} 之前，否則會被當成日期而 422。"""
        resp = self.client.get("/api/brief/dates")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["dates"], ["2026-08-07", "2026-08-06"])


if __name__ == "__main__":
    unittest.main()
