"""M4a answer.py 接線測試：time_sensitive 分支走受信任 adapter。

- 無 provider（預設 registry 空）→ M4 既有 notice 婉拒零回歸。
- 有 provider → 確定性模板答案（含資料時間/來源性質）、檢索結果不得進入答案、
  qa_log ext_sources 帶結構化來源。
- 續問路徑（前檢命中）完全不觸發 retrieve_context。
"""

import hashlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.retrieval_pipeline as rp  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import trusted_market_data as tmd  # noqa: E402

_Q = "台積電今天收盤價多少"  # 命中 _TIME_SENSITIVE_TERMS 前檢（零 LLM、決定性）


def _fresh_point():
    return tmd.TrustedDataPoint(
        value="1085.00",
        unit="TWD",
        as_of=datetime.now(timezone.utc) - timedelta(minutes=1),
        published_at=None,
        url="https://example.com/quote/2330",
        source_type="exchange",
        content_hash=hashlib.sha256(b"payload").hexdigest(),
        provider="fake-quote",
        category="quote",
        subject="台積電 2330",
    )


def _spec():
    return tmd.ProviderSpec(
        name="fake-quote",
        category="quote",
        allowed_domains=("example.com",),
        max_age=timedelta(minutes=15),
        cache_ttl=timedelta(seconds=60),
        timeout=1.0,
        min_interval=0.0,
        exchange_tz="Asia/Taipei",
    )


class _Provider:
    def __init__(self, point):
        self.point = point

    async def fetch(self, query):
        return self.point


class FormatTrustedAnswerTests(unittest.TestCase):
    def test_contains_time_source_nature_and_disclaimer(self):
        body = ans.format_trusted_answer(_fresh_point())
        self.assertIn("台積電 2330", body)
        self.assertIn("1085.00 TWD", body)
        self.assertIn("資料時間", body)
        self.assertIn("exchange", body)          # 來源性質
        self.assertIn("fake-quote", body)        # provider
        self.assertIn("https://example.com/quote/2330", body)
        self.assertIn("僅供參考", body)


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "qa-fixed-id"


class TrustedAnswerFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmd.clear_providers()
        self._orig_retrieve = rp.retrieve_context
        self._orig_log = ans._log_qa
        self._orig_turns = ans.load_recent_turns

    def tearDown(self):
        tmd.clear_providers()
        rp.retrieve_context = self._orig_retrieve
        ans._log_qa = self._orig_log
        ans.load_recent_turns = self._orig_turns

    async def test_no_provider_keeps_m4_notice(self):
        async def fake_retrieve(question, **kw):
            return ([], "")

        rp.retrieve_context = fake_retrieve
        rec = _Recorder()
        ans._log_qa = rec
        events = [e async for e in ans.answer_question(_Q)]
        kinds = [e[0] for e in events]
        self.assertIn("notice", kinds)
        notices = [p for (k, p) in events if k == "notice"]
        self.assertEqual(notices[0], ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)
        self.assertNotIn("token", kinds)

    async def test_provider_answers_with_time_and_source(self):
        # 檢索回傳的來源絕不能進入時效答案（首輪並行取證被丟棄）
        marker_sources = [ans.Source(n=1, report_id="r1", file_name="f",
                                     market="TW", report_date="2026-01-01")]

        async def fake_retrieve(question, **kw):
            return (marker_sources, "舊研報脈絡")

        rp.retrieve_context = fake_retrieve
        rec = _Recorder()
        ans._log_qa = rec
        tmd.register_provider(_spec(), _Provider(_fresh_point()))

        events = [e async for e in ans.answer_question(_Q)]
        kinds = [e[0] for e in events]
        self.assertNotIn("notice", kinds)
        # sources 事件必須為空（研報來源不得混入時效答案）
        src_payloads = [p for (k, p) in events if k == "sources"]
        self.assertEqual(src_payloads, [[]])
        body = "".join(p for (k, p) in events if k == "token")
        self.assertIn("資料時間", body)
        self.assertIn("exchange", body)
        self.assertNotIn("舊研報脈絡", body)
        ext = [p for (k, p) in events if k == "ext_sources"][0]
        self.assertEqual(ext[0]["url"], "https://example.com/quote/2330")
        self.assertEqual(ext[0]["source_type"], "exchange")
        self.assertIn("as_of", ext[0])
        done = events[-1][1] if events[-1][0] == "done" else None
        self.assertIsNotNone(done)
        self.assertEqual(done["qa_id"], "qa-fixed-id")

        # qa_log：path=time_sensitive、ext_sources 帶結構化來源
        (args, kwargs) = rec.calls[0]
        self.assertEqual(args[3].get("path"), "time_sensitive")  # filters
        self.assertEqual(args[6][0]["source_type"], "exchange")  # ext_sources

    async def test_followup_precheck_never_retrieves(self):
        async def boom_retrieve(question, **kw):
            raise AssertionError("續問時效題不得觸發 retrieve_context")

        async def fake_turns(conv_id, **kw):
            return [("先前的問題", "先前的答案")]

        rp.retrieve_context = boom_retrieve
        ans.load_recent_turns = fake_turns
        rec = _Recorder()
        ans._log_qa = rec
        tmd.register_provider(_spec(), _Provider(_fresh_point()))

        events = [
            e async for e in ans.answer_question(_Q, conversation_id="conv-1")
        ]
        body = "".join(p for (k, p) in events if k == "token")
        self.assertIn("資料時間", body)

    async def test_provider_failure_falls_back_to_notice(self):
        class _BoomProvider:
            async def fetch(self, query):
                raise RuntimeError("provider down")

        async def fake_retrieve(question, **kw):
            return ([], "")

        rp.retrieve_context = fake_retrieve
        rec = _Recorder()
        ans._log_qa = rec
        tmd.register_provider(_spec(), _BoomProvider())

        events = [e async for e in ans.answer_question(_Q)]
        notices = [p for (k, p) in events if k == "notice"]
        self.assertEqual(notices, [ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE])


if __name__ == "__main__":
    unittest.main()
