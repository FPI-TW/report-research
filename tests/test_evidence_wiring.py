"""M4b 寫入接線測試：主 RAG／時效路徑的 evidence_manifest 建立與 qa_log INSERT。

不觸 DB、不觸 claude CLI；_log_qa 以 recorder 捕 kwargs，SQL 形狀以 fake session 驗。
"""

import hashlib
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.retrieval_pipeline as rp  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import evidence as ev  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services import trusted_market_data as tmd  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def _row(report_id="r-1", file_name="a.pdf", market="TW",
         report_date=date(2026, 6, 1), content="台積電內容。"):
    return ChunkRow(
        chunk_id="c-1", report_id=report_id, file_name=file_name, market=market,
        source=None, summary=None, report_date=report_date, report_type=None,
        instrument_types=None, relates_stock=None, relates_futures=None,
        stock_targets=None, futures_targets=None, chunk_index=0,
        content=content, distance=0.2,
    )


class _FakeSession:
    def __init__(self, recorder=None):
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        if self._recorder is not None:
            self._recorder.append((str(stmt), params))
        return None

    async def commit(self):
        return None


class _LogRecorder:
    def __init__(self):
        self.kwargs = None
        self.args = None

    async def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return "qa-fixed-id"


class MainRagManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_has_corpus_and_controlled_external(self):
        rec = _LogRecorder()

        async def fake_search(session, q, qvec, **k):
            return [(1, 0.9, _row())]

        async def fake_stream(*a, **k):
            yield "台積電觀點[1]。\n"
            yield "[EXT_SOURCES]\n- 補充新聞 | https://news.example.com/a\n"
            yield "- 壞來源 | not-a-url\n"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        orig = (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ans._log_qa,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans._log_qa = rec
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (
                rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
                ans._log_qa,
            ) = orig

        manifest = rec.kwargs["evidence_manifest"]
        self.assertEqual(ev.validate_manifest(manifest), [])
        kinds = [d["kind"] for d in manifest["evidence"]]
        self.assertEqual(kinds, ["corpus", "external"])  # 壞外部來源被跳過
        corpus = manifest["evidence"][0]
        self.assertEqual(corpus["report_id"], "r-1")
        external = manifest["evidence"][1]
        self.assertEqual(external["url"], "https://news.example.com/a")
        self.assertEqual(external["source_type"], "web")
        self.assertIsNotNone(external["retrieved_at"])


class TrustedManifestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmd.clear_providers()

    def tearDown(self):
        tmd.clear_providers()

    async def test_trusted_answer_writes_external_manifest(self):
        rec = _LogRecorder()

        async def fake_retrieve(question, **kw):
            return ([], "")

        point = tmd.TrustedDataPoint(
            value="1085.00", unit="TWD",
            as_of=datetime.now(timezone.utc) - timedelta(minutes=1),
            published_at=None, url="https://example.com/q",
            source_type="exchange",
            content_hash=hashlib.sha256(b"p").hexdigest(),
            provider="fake-quote", category="quote", subject="台積電 2330",
        )

        class _P:
            async def fetch(self, query):
                return point

        tmd.register_provider(
            tmd.ProviderSpec(
                name="fake-quote", category="quote",
                allowed_domains=("example.com",),
                max_age=timedelta(minutes=15), cache_ttl=timedelta(seconds=60),
                timeout=1.0, min_interval=0.0, exchange_tz="Asia/Taipei",
            ),
            _P(),
        )

        orig = (rp.retrieve_context, ans._log_qa)
        rp.retrieve_context = fake_retrieve
        ans._log_qa = rec
        try:
            _ = [e async for e in ans.answer_question("台積電今天收盤價多少")]
        finally:
            rp.retrieve_context, ans._log_qa = orig

        manifest = rec.kwargs["evidence_manifest"]
        self.assertEqual(ev.validate_manifest(manifest), [])
        self.assertEqual(len(manifest["evidence"]), 1)
        e = manifest["evidence"][0]
        self.assertEqual(e["kind"], "external")
        self.assertEqual(e["source_type"], "exchange")
        self.assertEqual(e["content_hash"], point.content_hash)


class LogQaSqlTests(unittest.IsolatedAsyncioTestCase):
    async def test_insert_includes_manifest_column_and_param(self):
        recorded = []
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _FakeSession(recorded)
        try:
            manifest = {"schema_version": 1, "evidence": []}
            qa_id = await ans._log_qa(
                "問題", "答案", [], {}, 5, [], [],
                evidence_manifest=manifest,
            )
        finally:
            ans.SessionFactory = orig
        self.assertIsNotNone(qa_id)
        stmt, params = recorded[0]
        self.assertIn("evidence_manifest", stmt)
        self.assertIn('"schema_version": 1', params["evidence_manifest"])

    async def test_none_manifest_binds_null(self):
        recorded = []
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _FakeSession(recorded)
        try:
            await ans._log_qa("問題", "答案", [], {}, 5, [], [])
        finally:
            ans.SessionFactory = orig
        _, params = recorded[0]
        self.assertIsNone(params["evidence_manifest"])


if __name__ == "__main__":
    unittest.main()
