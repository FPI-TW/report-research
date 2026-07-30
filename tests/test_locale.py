# tests/test_locale.py
"""M10a：輸出語言（locale）貫穿與在地化。

涵蓋三層：
1. `app.services.locale` 純函式（resolve 的 fail-open 矩陣、directive、pick）。
2. 確定性在地化字串（overview 模板、trusted 模板）zh/en 兩版。
3. `answer_question` 把 locale 解析後附加語言覆寫指令到系統提示（en 附加、預設不動）。
"""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import locale as loc  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services.overview import render_overview_text  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def make_row(report_id, file_name, market, content, report_date=None, distance=0.1):
    return ChunkRow(
        chunk_id=None,
        report_id=report_id,
        file_hash=f"h-{report_id}",
        file_name=file_name,
        title=None,
        market=market,
        source=None,
        summary=None,
        report_date=report_date,
        report_type=None,
        instrument_types=None,
        relates_stock=None,
        relates_futures=None,
        stock_targets=None,
        futures_targets=None,
        chunk_index=0,
        content=content,
        distance=distance,
    )


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class ResolveLocaleTests(unittest.TestCase):
    def test_fail_open_to_zh_hant(self):
        for raw in (None, "", "   ", "fr", "de-DE", "zz", "xx-yy"):
            self.assertEqual(loc.resolve_locale(raw), "zh-Hant", raw)

    def test_english_variants(self):
        for raw in ("en", "EN", "en-US", "en_us", "  en  ", "en-GB"):
            self.assertEqual(loc.resolve_locale(raw), "en", raw)

    def test_chinese_variants_all_map_to_zh_hant(self):
        for raw in ("zh", "zh-TW", "zh-Hant", "zh-HK", "ZH_tw", "zh-Hans"):
            self.assertEqual(loc.resolve_locale(raw), "zh-Hant", raw)

    def test_is_english(self):
        self.assertTrue(loc.is_english("en"))
        self.assertFalse(loc.is_english("zh-Hant"))

    def test_output_directive_default_is_empty(self):
        # 預設中文（含未知 fail-open 後的值）回空字串 → 系統提示一字不動
        self.assertEqual(loc.output_directive("zh-Hant"), "")
        self.assertEqual(loc.output_directive("anything-unknown"), "")

    def test_output_directive_english_overrides(self):
        d = loc.output_directive("en")
        self.assertTrue(d)
        self.assertIn("English", d)
        self.assertIn("[EXT_SOURCES]", d)  # 明示保留 sentinel 不變

    def test_pick(self):
        self.assertEqual(loc.pick("en", "中", "en"), "en")
        self.assertEqual(loc.pick("zh-Hant", "中", "en"), "中")
        self.assertEqual(loc.pick("unknown", "中", "en"), "中")  # fail-open


def _overview(total, **over):
    base = dict(
        total=total, filters=None, date_min=None, date_max=None,
        by_market=[], by_instrument=[], by_report_type=[], top_stocks=[], samples=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


class RenderOverviewTextLocaleTests(unittest.TestCase):
    def test_zero_zh_default(self):
        msg = render_overview_text(_overview(0))
        self.assertIn("找不到符合條件", msg)

    def test_zero_en(self):
        msg = render_overview_text(_overview(0), "en")
        self.assertIn("No reports matching the criteria", msg)
        self.assertIn("were found in the corpus", msg)

    def test_nonzero_en_frame(self):
        msg = render_overview_text(_overview(3, by_market=[("TW", 2)]), "en")
        self.assertTrue(msg.startswith("A total of 3 reports match"))
        self.assertIn("By market:", msg)

    def test_unknown_locale_fails_open_to_zh(self):
        msg = render_overview_text(_overview(0), "fr")
        self.assertIn("找不到符合條件", msg)


class FormatTrustedAnswerLocaleTests(unittest.TestCase):
    def _point(self):
        return SimpleNamespace(
            source_type="official", provider="TWSE", subject="2330 收盤價",
            value="1000", unit="TWD", as_of=datetime(2026, 7, 23, tzinfo=timezone.utc),
            published_at=None, url="https://example.com/x",
        )

    def test_zh_default(self):
        from app.services.answer import format_trusted_answer

        body = format_trusted_answer(self._point())
        self.assertIn("根據受信任資料來源", body)
        self.assertIn("資料時間：", body)

    def test_en(self):
        from app.services.answer import format_trusted_answer

        body = format_trusted_answer(self._point(), "en")
        self.assertIn("According to a trusted data source", body)
        self.assertIn("As of:", body)
        self.assertIn("Source: https://example.com/x", body)


class AnswerQuestionLocaleThreadingTests(unittest.IsolatedAsyncioTestCase):
    """answer_question 把 locale 解析後的語言指令附加到主 LLM 系統提示。"""

    def _patch(self, ans, rp, captured):
        async def fake_search(*a, **k):
            return [(0, 0.60, make_row("r1", "x.pdf", "TW", "可口可樂財報。",
                                       date(2026, 6, 1), distance=0.40))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            captured["system"] = k.get("system")
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_condense(history_text, question, **k):
            return (question, sr._decision(sr.CORPUS_QA))

        async def fake_load(conversation_id, **k):
            return []

        orig = (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ans.condense_and_route, ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        return orig

    @staticmethod
    def _restore(ans, rp, orig):
        (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ans.condense_and_route, ans.load_recent_turns,
        ) = orig

    async def _run(self, locale):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        captured: dict = {}
        orig = self._patch(ans, rp, captured)
        try:
            _ = [e async for e in ans.answer_question("可口可樂評級如何", locale=locale)]
        finally:
            self._restore(ans, rp, orig)
        return ans, captured["system"]

    async def test_english_appends_override(self):
        ans, system = await self._run("en")
        self.assertTrue(system.startswith(ans.SYSTEM_PROMPT))
        self.assertIn("OUTPUT LANGUAGE OVERRIDE", system)

    async def test_default_none_leaves_prompt_unchanged(self):
        ans, system = await self._run(None)
        self.assertEqual(system, ans.SYSTEM_PROMPT)

    async def test_unknown_locale_fails_open_no_override(self):
        ans, system = await self._run("fr")
        self.assertEqual(system, ans.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
