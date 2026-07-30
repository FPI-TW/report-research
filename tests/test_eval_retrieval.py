# tests/test_eval_retrieval.py
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_retrieval as ev  # noqa: E402

from app.services.answer import Source  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def _row(report_id, content, distance=0.1):
    """造一列符合 hybrid_search 回傳結構的 ChunkRow。"""
    return ChunkRow(
        chunk_id=None,
        report_id=report_id,
        file_hash=f"h-{report_id}",
        file_name=None,
        title=None,
        market=None,
        source=None,
        summary=None,
        report_date=None,
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


def _src(n, rid, file_name, report_date, market="TW"):
    return Source(
        n=n, report_id=rid, file_name=file_name, market=market, report_date=report_date
    )


class SourceMatchTests(unittest.TestCase):
    def test_match_via_normalization(self):
        # 查詢詞「AI伺服器」應命中片段內有空白/全形的「AI 伺服器」
        scored = [_row("r1", "本季 AI 伺服器 需求強勁。")]
        s = _src(1, "r1", "台積電法說.pdf", "2026-06-01")
        self.assertTrue(ev.source_matches(s, scored, ["AI伺服器"]))
        self.assertFalse(ev.source_matches(s, scored, ["記憶體"]))

    def test_match_uses_file_name(self):
        # 內容無關，但檔名含關鍵字也算命中
        scored = [_row("r1", "與主題無關的內容。")]
        s = _src(1, "r1", "台積電_先進封裝展望.pdf", "2026-06-01")
        self.assertTrue(ev.source_matches(s, scored, ["先進封裝"]))

    def test_no_keywords_is_false(self):
        scored = [_row("r1", "任意內容")]
        s = _src(1, "r1", "x.pdf", "2026-06-01")
        self.assertFalse(ev.source_matches(s, scored, []))


class SourceAgeTests(unittest.TestCase):
    def test_age_in_days(self):
        s = _src(1, "r1", "x.pdf", "2026-01-01")
        self.assertEqual(ev.source_age_days(s, date(2026, 6, 1)), 151)

    def test_missing_date_is_none(self):
        s = _src(1, "r1", "x.pdf", None)
        self.assertIsNone(ev.source_age_days(s, date(2026, 6, 1)))


class CaseMetricsTests(unittest.TestCase):
    AS_OF = date(2026, 6, 2)

    def _setup(self):
        scored = [_row("r1", "AI 伺服器 需求強。"), _row("r2", "純雜訊內容。")]
        sources = [
            _src(1, "r1", "a.pdf", "2026-06-01"),
            _src(2, "r2", "b.pdf", "2026-05-01"),
        ]
        return sources, scored

    def test_hit_and_precision(self):
        sources, scored = self._setup()
        expect = {"any_keywords": ["AI伺服器"], "min_hits": 1}
        m = ev.case_metrics(sources, scored, expect, self.AS_OF)
        self.assertTrue(m["hit"])
        self.assertEqual(m["matched_count"], 1)
        self.assertEqual(m["p_at_k"], 0.5)
        self.assertEqual(m["n_sources"], 2)

    def test_min_hits_not_met(self):
        sources, scored = self._setup()
        expect = {"any_keywords": ["AI伺服器"], "min_hits": 2}
        m = ev.case_metrics(sources, scored, expect, self.AS_OF)
        self.assertFalse(m["hit"])

    def test_recency_pass_when_fresh(self):
        sources, scored = self._setup()
        expect = {"any_keywords": ["AI伺服器"], "recency": {"max_age_days": 180}}
        m = ev.case_metrics(sources, scored, expect, self.AS_OF)
        self.assertTrue(m["recency_pass"])

    def test_recency_fail_when_all_stale(self):
        scored = [_row("r1", "AI 伺服器")]
        sources = [_src(1, "r1", "old.pdf", "2020-01-01")]
        expect = {"any_keywords": ["AI伺服器"], "recency": {"max_age_days": 180}}
        m = ev.case_metrics(sources, scored, expect, self.AS_OF)
        self.assertFalse(m["recency_pass"])

    def test_recency_none_when_unspecified(self):
        sources, scored = self._setup()
        expect = {"any_keywords": ["AI伺服器"]}
        m = ev.case_metrics(sources, scored, expect, self.AS_OF)
        self.assertIsNone(m["recency_pass"])


class AggregateTests(unittest.TestCase):
    def test_aggregate_fields(self):
        per_case = [
            {"hit": True, "p_at_k": 1.0, "ages": [10, 400], "recency_pass": True},
            {"hit": False, "p_at_k": 0.0, "ages": [800], "recency_pass": None},
        ]
        agg = ev.aggregate(per_case, max_age_days=365)
        self.assertEqual(agg["n_cases"], 2)
        self.assertEqual(agg["hit_rate"], 0.5)
        self.assertEqual(agg["mean_p_at_k"], 0.5)
        self.assertEqual(agg["median_cited_age"], 400)
        self.assertEqual(agg["n_cited"], 3)
        # 400 與 800 > 365 → 2/3
        self.assertAlmostEqual(agg["pct_over_max_age"], 2 / 3)
        self.assertEqual(agg["recency_pass_rate"], 1.0)

    def test_aggregate_empty(self):
        agg = ev.aggregate([], max_age_days=365)
        self.assertEqual(agg["n_cases"], 0)
        self.assertIsNone(agg["median_cited_age"])


class LoadQuerysetTests(unittest.TestCase):
    def test_load_valid(self):
        data = {
            "as_of": "2026-06-24",
            "cases": [
                {"id": "c1", "query": "台積電展望", "expect": {"any_keywords": ["台積電"]}}
            ],
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f, ensure_ascii=False)
            path = f.name
        try:
            loaded = ev.load_queryset(path)
            self.assertEqual(loaded["as_of"], "2026-06-24")
            self.assertEqual(len(loaded["cases"]), 1)
            self.assertEqual(loaded["cases"][0]["id"], "c1")
        finally:
            os.unlink(path)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class RunCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_case_end_to_end(self):
        # monkeypatch 檢索與 embedding，驗證 run_case 串接 build_context 並算出指標
        scored = [
            (0, 0.80, _make_full_row("r1", "台積電.pdf", "AI 伺服器需求強", "2026-06-01")),
            (0, 0.50, _make_full_row("r2", "雜訊.pdf", "無關內容", "2026-05-01")),
        ]
        orig_embed, orig_search, orig_factory = (
            ev.embed_query_cached,
            ev.hybrid_search,
            ev.SessionFactory,
        )

        async def fake_search(session, q, qvec, **kw):
            return scored

        ev.embed_query_cached = lambda q: [0.0] * 8
        ev.hybrid_search = fake_search
        ev.SessionFactory = _FakeSession
        try:
            case = {
                "id": "tsmc",
                "query": "台積電 AI 伺服器",
                "expect": {"any_keywords": ["AI伺服器"], "min_hits": 1},
            }
            result = await ev.run_case(case, k=8, as_of=date(2026, 6, 2))
        finally:
            ev.embed_query_cached = orig_embed
            ev.hybrid_search = orig_search
            ev.SessionFactory = orig_factory
        self.assertEqual(result["id"], "tsmc")
        self.assertTrue(result["hit"])
        self.assertGreaterEqual(result["n_sources"], 1)


def _make_full_row(report_id, file_name, content, report_date):
    """build_context 需要的完整 ChunkRow（含 file_name/market/date）。"""
    return ChunkRow(
        chunk_id=None,
        report_id=report_id,
        file_hash=f"h-{report_id}",
        file_name=file_name,
        title=None,
        market="TW",
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
        distance=0.1,
    )


if __name__ == "__main__":
    unittest.main()
