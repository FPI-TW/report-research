import asyncio
import sys
import unittest
from datetime import datetime, timezone
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
        stats = {}
        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _fake_rerank):
            await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000, rerank_top_m=50, timer=t, stats=stats,
            )
        # 名額＝選篇會接納的研報的 chunk 數（這裡只有 2 個），上限才是 rerank_top_m
        self.assertEqual(seen["top_m"], 2)
        self.assertEqual(seen["build_scored"], [(0, 0.99, "reranked")])  # 重排結果進 build_context
        self.assertIn("rerank", t.marks)
        self.assertIsNotNone(seen["deadline"])  # deadline 傳入供批次邊界提早中止
        # rerank 實際套用 → 必須把「重排前的 fused」當 gate 快照傳給 build_context。
        # 少了它，select_reports 就會拿 rerank 的 sigmoid [0,1] 分去比以 fused 尺度
        # 校準的 ASK_RELEVANCE_FLOOR=0.62（量綱錯配，會誤剔 tier 0 高相關候選）。
        self.assertEqual(seen["build_kw"]["gate_scores"], {"c1": 0.5, "c2": 0.71})
        self.assertTrue(stats["rerank_applied"])
        self.assertFalse(stats["rerank_timed_out"])
        self.assertGreaterEqual(stats["rerank_queue_ms"], 0)
        self.assertGreaterEqual(stats["rerank_compute_ms"], 0)

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
            stats = {}
            out = await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15,
                max_passages=4, max_chars=20000,
                rerank_top_m=50, rerank_timeout=0.05, stats=stats,
            )
            await asyncio.sleep(0.3)  # 讓背景 task 收尾，避免 loop 關閉警告
        self.assertEqual(out, (["S"], "CTX"))
        self.assertIs(seen["build_scored"], hybrid_out)  # 逾時 → 用原 fused 序
        # 逾時＝分數未被覆寫，gate 快照不傳（比 fused 等價，且讓「有無覆寫」可讀）
        self.assertIsNone(seen["build_kw"]["gate_scores"])
        self.assertTrue(stats["rerank_timed_out"])
        self.assertFalse(stats["rerank_applied"])

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


def _dated(cid, rid, rdate, content=None):
    vals = [None] * len(ChunkRow._fields)
    vals[ChunkRow._fields.index("chunk_id")] = cid
    vals[ChunkRow._fields.index("report_id")] = rid
    vals[ChunkRow._fields.index("report_date")] = rdate
    vals[ChunkRow._fields.index("content")] = content or f"{rid} 段"
    vals[ChunkRow._fields.index("distance")] = 0.0
    return ChunkRow._make(vals)


_NOW_DT = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _stale_mix():
    """head 前段是兩篇過舊研報（一定被軟截斷剔除），後面才是夠新又夠相關的。"""
    return [
        (2, 0.99, _dated("o1", "old1", "2025-01-01")),
        (2, 0.98, _dated("o2", "old2", "2025-02-01")),
        (2, 0.97, _dated("f1", "fresh1", "2026-07-01")),
        (2, 0.96, _dated("o1b", "old1", "2025-01-01")),
        (2, 0.95, _dated("f2", "fresh2", "2026-06-20")),
    ]



def _arrange(scored, top_m=16):
    import app.services.retrieval_pipeline as rp

    return rp.rerank_head_first(scored, top_m=top_m, max_passages=4, now=_NOW_DT)


class RerankHeadFirstTests(unittest.TestCase):
    def test_head_skips_reports_selection_would_drop(self):
        """過舊研報（過舊軟截斷一定剔除）不佔 head 名額；其餘 chunk 相對序不變。"""
        arranged, head_n = _arrange(_stale_mix())
        self.assertEqual(head_n, 2)
        self.assertEqual([r.chunk_id for (_t, _f, r) in arranged], ["f1", "f2", "o1", "o2", "o1b"])

    def test_head_is_chunk_level_in_fused_order_and_capped_by_top_m(self):
        """同一篇的多段都可以進 head（名額是 chunk 數），依融合序取前 top_m 個。"""
        scored = [
            (2, 0.99, _dated("o1", "old1", "2025-01-01")),
            (2, 0.98, _dated("a1", "fresh1", "2026-07-01")),
            (2, 0.97, _dated("a2", "fresh1", "2026-07-01", content="fresh1 段二")),
            (2, 0.96, _dated("b1", "fresh2", "2026-06-20")),
        ]
        arranged, head_n = _arrange(scored, top_m=2)
        self.assertEqual(head_n, 2)
        self.assertEqual([r.chunk_id for (_t, _f, r) in arranged], ["a1", "a2", "o1", "b1"])

    def test_returns_same_object_when_head_is_already_first(self):
        scored = [(2, 0.97, _dated("f1", "fresh1", "2026-07-01"))]
        arranged, head_n = _arrange(scored)
        self.assertIs(arranged, scored)
        self.assertEqual(head_n, 1)

    def test_head_skips_empty_content(self):
        scored = [
            (2, 0.99, _dated("e1", "fresh1", "2026-07-01", content="   ")),
            (2, 0.98, _dated("f1", "fresh1", "2026-07-01")),
        ]
        arranged, head_n = _arrange(scored)
        self.assertEqual((head_n, arranged[0][2].chunk_id), (1, "f1"))

    def test_fail_open_returns_input_and_fused_head(self):
        scored = [(0, 0.5, "row"), (0, 0.4, "row2")]
        arranged, head_n = _arrange(scored, top_m=16)
        self.assertIs(arranged, scored)
        self.assertEqual(head_n, 2)

    def test_selection_is_unchanged_without_rerank_scores(self):
        """只是重新排列：不帶 rerank_scores 時脈絡與來源逐字相同。"""
        from app.services.answer import build_context

        scored = _stale_mix()
        arranged, _n = _arrange(scored)
        self.assertEqual(build_context(arranged, now=_NOW_DT), build_context(scored, now=_NOW_DT))


class RerankHeadWiringTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, rerank):
        import app.services.retrieval_pipeline as rp

        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return _stale_mix()

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["build_kw"] = kw
            return (["S"], "CTX")

        def _spy_rerank(question, scored, *, top_m, timer=None, deadline=None):
            seen["rerank_in"] = [r.chunk_id for (_t, _f, r) in scored]
            return rerank(scored, top_m)

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build), \
             mock.patch.object(rp, "rerank_scored", _spy_rerank):
            await rp.retrieve_context(
                "q", k=15, dense_scan=400, max_reports=15, max_passages=4,
                max_chars=20000, rerank_top_m=16, now=_NOW_DT,
            )
        return seen

    async def test_rerank_head_comes_from_eligible_reports_and_scores_reach_selection(self):
        def _rerank(scored, top_m):
            seen_top_m.append(top_m)
            head = [(t, 0.5 + i / 10, r) for i, (t, _f, r) in enumerate(scored[:top_m])]
            return head + scored[top_m:]

        seen_top_m = []
        seen = await self._run(_rerank)
        self.assertEqual(seen_top_m, [2])  # 名額＝選篇會接納的 chunk 數（上限 rerank_top_m）
        self.assertEqual(set(seen["rerank_in"][:2]), {"f1", "f2"})  # 過舊研報不佔 head 名額
        self.assertEqual(
            seen["build_kw"]["rerank_scores"],
            {seen["rerank_in"][0]: 0.5, seen["rerank_in"][1]: 0.6},
        )
        # gate 快照仍是挪動前、重排前的 fused（內容相同）
        self.assertEqual(seen["build_kw"]["gate_scores"]["o1"], 0.99)

    async def test_rerank_not_applied_passes_no_rerank_scores(self):
        seen = await self._run(lambda scored, top_m: scored)  # fail-open：回同一物件
        self.assertIsNone(seen["build_kw"]["rerank_scores"])
        self.assertIsNone(seen["build_kw"]["gate_scores"])
