# tests/test_brief.py
"""每日簡報：純函式（窗期／素材／解析）＋ 三支端點（TestClient + 假 deps）。

DB 查詢本身（_SIGNAL_CHANGES_SQL 的 lag 視窗、新鮮度過濾）不在此檔——那需要真的
pgvector 容器，與 tests/test_schema_constraints.py 同一類。此處守的是**接線與判斷**：
窗期算錯、模型輸出沒收乾淨、空狀態被當成錯誤，這三種失效都不會有任何錯誤訊息。
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import generate_brief  # noqa: E402  (scripts/ 已在 sys.path)
from fastapi.testclient import TestClient  # noqa: E402

from app.services.brief import (  # noqa: E402
    BriefReport,
    BriefRow,
    BriefSignalChange,
    build_material,
    build_prompt,
    market_zh,
    parse_brief,
    rating_zh,
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
            )
        }
        deps.SessionFactory = _FakeSession
        deps.fetch_brief_dates = self._dates
        deps.fetch_brief_reports = self._reports
        self.client = _authed_client()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(deps, name, value)

    async def _dates(self, session, limit=30):
        return [date(2026, 8, 7), date(2026, 8, 6)]

    async def _reports(self, session, ids):
        return [_report()] if ids else []

    def test_latest_returns_payload_with_sources(self):
        async def fetch(session):
            return _row()

        deps.fetch_latest_brief = fetch
        body = self.client.get("/api/brief/latest").json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["brief"]["brief_date"], "2026-08-07")
        self.assertEqual(body["brief"]["reports"][0]["file_hash"], f"{1:064d}")
        self.assertEqual(body["available_dates"], ["2026-08-07", "2026-08-06"])

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
