import asyncio
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
        from unittest import mock

        import app.services.retrieval_pipeline as rp

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

        def _spy_rerank(question, scored, *, top_m, timer=None, deadline=None):
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
        from unittest import mock

        import app.services.retrieval_pipeline as rp

        seen = {}

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class _Timer:
            def __init__(self): self.marks = []
            def mark(self, name): self.marks.append(name)

        async def _fake_hybrid(session, q, vec, **kw):
            return [(0, 0.5, _row("c1")), (1, 0.71, _row("c2"))]

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["build_kw"] = kw
            return (["S"], "CTX")

        def _fake_rerank(question, scored, *, top_m, timer=None, deadline=None):
            seen["top_m"] = top_m
            seen["deadline"] = deadline
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
        self.assertIsNotNone(seen["deadline"])  # deadline 傳入供批次邊界提早中止
        # rerank 實際套用 → 必須把「重排前的 fused」當 gate 快照傳給 build_context。
        # 少了它，select_reports 就會拿 rerank 的 sigmoid [0,1] 分去比以 fused 尺度
        # 校準的 ASK_RELEVANCE_FLOOR=0.62（量綱錯配，會誤剔 tier 0 高相關候選）。
        self.assertEqual(seen["build_kw"]["gate_scores"], {"c1": 0.5, "c2": 0.71})

    async def test_rerank_timeout_expiry_falls_back_to_fused(self):
        import time as _time

        import app.services.retrieval_pipeline as rp

        seen = {}
        hybrid_out = [(0, 0.5, _row("c1"))]

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def _fake_hybrid(session, q, vec, **kw):
            return hybrid_out

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["build_kw"] = kw
            return (["S"], "CTX")

        def _slow_rerank(question, scored, *, top_m, timer=None, deadline=None):
            _time.sleep(0.2)
            return [(0, 0.99, "reranked")]

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _slow_rerank):
            out = await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000,
                rerank_top_m=50, rerank_timeout=0.05,
            )
            await asyncio.sleep(0.3)  # 讓背景 task 收尾，避免 loop 關閉警告
        self.assertEqual(out, (["S"], "CTX"))
        self.assertIs(seen["build_scored"], hybrid_out)  # 逾時 → 用原 fused 序
        # 逾時＝分數未被覆寫，gate 快照不傳（比 fused 等價，且讓「有無覆寫」可讀）
        self.assertIsNone(seen["build_kw"]["gate_scores"])

    async def test_rerank_timeout_none_falls_back_to_module_default(self):
        import time as _time

        import app.services.retrieval_pipeline as rp

        seen = {}
        hybrid_out = [(0, 0.5, _row("c1"))]

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def _fake_hybrid(session, q, vec, **kw):
            return hybrid_out

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            return (["S"], "CTX")

        def _slow_rerank(question, scored, *, top_m, timer=None, deadline=None):
            _time.sleep(0.2)
            return [(0, 0.99, "reranked")]

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _slow_rerank), \
             mock.patch.object(rp, "_RERANK_TIMEOUT", 0.05):
            await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000, rerank_top_m=50,
            )
            await asyncio.sleep(0.3)
        self.assertIs(seen["build_scored"], hybrid_out)  # 未給參數 → 模組預設仍生效


from app.services.rows import ChunkRow  # noqa: E402


def _row(cid, rid="R", content="內容"):
    # 位置一律由 _fields 推導，不要寫死數字：ChunkRow 中段插欄（file_hash、title…）
    # 時寫死的索引會無聲指向錯欄——scripts/eval_retrieval.py 曾因此讓評測分數變垃圾。
    vals = [None] * len(ChunkRow._fields)
    vals[ChunkRow._fields.index("chunk_id")] = cid
    vals[ChunkRow._fields.index("report_id")] = rid
    vals[ChunkRow._fields.index("content")] = content
    vals[ChunkRow._fields.index("distance")] = 0.0
    return ChunkRow._make(vals)


class _Session:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
