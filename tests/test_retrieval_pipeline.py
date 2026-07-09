import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class RetrieveContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_wires_embed_search_buildcontext_and_marks_timer(self):
        import app.services.retrieval_pipeline as rp

        calls = []

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class _Timer:
            def mark(self, name): calls.append(("mark", name))

        async def _fake_hybrid(session, q, vec, **kw):
            calls.append(("hybrid", q, kw.get("k"), kw.get("dense_scan")))
            return [("scored")]

        def _fake_build(scored, **kw):
            calls.append(("build", tuple(scored), kw.get("max_reports")))
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            t = _Timer()
            sources, ctx = await rp.retrieve_context(
                "台積電", k=30, dense_scan=400, max_reports=25,
                max_passages=6, max_chars=40000, filters={"market": "TW"}, timer=t,
            )

        self.assertEqual((sources, ctx), (["S"], "CTX"))
        self.assertIn(("hybrid", "台積電", 30, 400), calls)
        self.assertIn(("build", ("scored",), 25), calls)
        self.assertIn(("mark", "embed"), calls)
        self.assertIn(("mark", "retrieve"), calls)

    async def test_rerank_top_m_zero_skips_rerank(self):
        import app.services.retrieval_pipeline as rp
        from unittest import mock

        called = {"n": 0}
        seen = {}
        hybrid_out = [(0, 0.5, "row")]

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def _fake_hybrid(session, q, vec, **kw):
            return hybrid_out

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            return (["S"], "CTX")

        def _spy_rerank(question, scored, *, top_m, timer=None):
            called["n"] += 1
            return scored

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _spy_rerank):
            out = await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000, rerank_top_m=0,
            )
        self.assertEqual(out, (["S"], "CTX"))
        self.assertEqual(called["n"], 0)  # rerank_top_m=0 → 不呼叫
        self.assertIs(seen["build_scored"], hybrid_out)  # build_context 收到未變動的原 scored

    async def test_rerank_top_m_positive_calls_rerank_and_marks_timer(self):
        import app.services.retrieval_pipeline as rp
        from unittest import mock

        seen = {}

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class _Timer:
            def __init__(self): self.marks = []
            def mark(self, name): self.marks.append(name)

        async def _fake_hybrid(session, q, vec, **kw):
            return [(0, 0.5, "row")]

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            return (["S"], "CTX")

        def _fake_rerank(question, scored, *, top_m, timer=None):
            seen["top_m"] = top_m
            if timer is not None:
                timer.mark("rerank")
            return [(0, 0.99, "reranked")]

        t = _Timer()
        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _fake_rerank):
            await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000, rerank_top_m=50, timer=t,
            )
        self.assertEqual(seen["top_m"], 50)
        self.assertEqual(seen["build_scored"], [(0, 0.99, "reranked")])  # 重排結果進 build_context
        self.assertIn("rerank", t.marks)


if __name__ == "__main__":
    unittest.main()
