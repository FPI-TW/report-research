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


class MergeScoredTests(unittest.TestCase):
    """merge_scored 純函式：跨清單 chunk 去重取最佳 (tier, fused)、排序、cap 截斷。"""

    def test_dedupes_across_lists_keeping_best_tier_fused(self):
        import app.services.retrieval_pipeline as rp

        a = [(0, 0.5, _row("c1")), (1, 0.4, _row("c2"))]
        b = [(2, 0.3, _row("c1")), (0, 0.9, _row("c2"))]
        out = rp.merge_scored([a, b], cap=10)
        by_id = {row.chunk_id: (t, f) for (t, f, row) in out}
        self.assertEqual(len(out), 2)
        self.assertEqual(by_id["c1"], (2, 0.3))  # tier 優先於 fused
        self.assertEqual(by_id["c2"], (1, 0.4))  # 同 chunk 取 (tier, fused) 最大者

    def test_sorts_by_tier_then_fused_desc(self):
        import app.services.retrieval_pipeline as rp

        out = rp.merge_scored(
            [[(0, 0.9, _row("a")), (2, 0.1, _row("b")), (1, 0.5, _row("c"))]],
            cap=10,
        )
        self.assertEqual([(t, f) for (t, f, _r) in out], [(2, 0.1), (1, 0.5), (0, 0.9)])

    def test_cap_truncates_after_sort(self):
        import app.services.retrieval_pipeline as rp

        rows = [(0, i / 10, _row(f"c{i}")) for i in range(5)]
        out = rp.merge_scored([rows], cap=3)
        self.assertEqual([r.chunk_id for (_t, _f, r) in out], ["c4", "c3", "c2"])

    def test_empty_input_returns_empty(self):
        import app.services.retrieval_pipeline as rp

        self.assertEqual(rp.merge_scored([], cap=5), [])


class RetrieveContextMultiTests(unittest.IsolatedAsyncioTestCase):
    """retrieve_context_multi：fan-out → 合併 → 單次 rerank → build_context。"""

    _KNOBS = dict(
        k=30, dense_scan=400, max_reports=25, max_passages=6, max_chars=40000,
        subquery_dense_scan=200, fanout_concurrency=2, total_candidates=100,
    )

    async def test_fans_out_merges_and_forwards_gate_and_mmr_kwargs(self):
        import app.services.retrieval_pipeline as rp

        rows = {
            "原題": [(2, 0.9, _row("c1", "r1", "內容一"))],
            "面向A": [(1, 0.8, _row("c2", "r2", "內容二"))],
            "面向B": [(0, 0.7, _row("c3", "r3", "內容三"))],
        }
        hybrid_calls = []
        rerank_calls = []
        seen = {}

        class _Timer:
            def __init__(self): self.marks = []
            def mark(self, name): self.marks.append(name)

        async def _fake_hybrid(session, q, vec, **kw):
            hybrid_calls.append((q, kw.get("dense_scan"), kw.get("k"), kw.get("market")))
            return rows[q]

        def _fake_rerank(question, scored, *, top_m, timer=None, deadline=None):
            rerank_calls.append((question, top_m, list(scored)))
            return list(scored)  # 新物件＝實際套用

        async def _fake_fetch(session, chunk_ids):
            seen["fetch_ids"] = list(chunk_ids)
            return {cid: [1.0, 0.0] for cid in chunk_ids}

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["build_kw"] = kw
            return (["S"], "CTX")

        t = _Timer()
        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _fake_rerank), \
             mock.patch.object(rp, "fetch_chunk_embeddings", _fake_fetch), \
             mock.patch.object(rp, "build_context", _fake_build):
            out = await rp.retrieve_context_multi(
                "原題", ["原題", "面向A", "面向B"],
                filters={"market": "TW"}, timer=t,
                rerank_top_m=120, rerank_timeout=5.0,
                mmr_lambda=0.7, mmr_max_per_source=6, mmr_max_per_month=0,
                **self._KNOBS,
            )

        self.assertEqual(out, (["S"], "CTX"))
        # 每條查詢各一次 hybrid_search：原題用呼叫端 dense_scan、子查詢用 subquery_dense_scan
        by_q = {q: scan for (q, scan, _k, _m) in hybrid_calls}
        self.assertEqual(by_q, {"原題": 400, "面向A": 200, "面向B": 200})
        self.assertTrue(all(k == 30 and m == "TW" for (_q, _s, k, m) in hybrid_calls))
        # 合併後單次 rerank：query=原題、top_m 轉發、含全部三條結果
        self.assertEqual(len(rerank_calls), 1)
        q, top_m, scored_in = rerank_calls[0]
        self.assertEqual(q, "原題")
        self.assertEqual(top_m, 120)
        self.assertEqual({r.chunk_id for (_t, _f, r) in scored_in}, {"c1", "c2", "c3"})
        # rerank 正常套用 → 重排結果（合併三條）進 build_context
        self.assertEqual(
            {r.chunk_id for (_t, _f, r) in seen["build_scored"]}, {"c1", "c2", "c3"}
        )
        # gate 快照＝rerank 前 fused；MMR kwargs 轉發
        kw = seen["build_kw"]
        self.assertEqual(kw["gate_scores"], {"c1": 0.9, "c2": 0.8, "c3": 0.7})
        self.assertEqual(kw["mmr_lambda"], 0.7)
        self.assertEqual(kw["mmr_max_per_source"], 6)
        self.assertEqual(kw["mmr_max_per_month"], 0)
        self.assertEqual(kw["max_reports"], 25)
        self.assertEqual(
            kw["chunk_embeddings"],
            {"c1": [1.0, 0.0], "c2": [1.0, 0.0], "c3": [1.0, 0.0]},
        )
        self.assertIn("embed", t.marks)
        self.assertIn("retrieve", t.marks)

    async def test_single_query_uses_caller_dense_scan(self):
        import app.services.retrieval_pipeline as rp

        scans = []

        async def _fake_hybrid(session, q, vec, **kw):
            scans.append(kw.get("dense_scan"))
            return [(0, 0.5, _row("c1", "r1"))]

        def _fake_build(scored, **kw):
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題"], mmr_lambda=0.0, **self._KNOBS,
            )
        self.assertEqual(scans, [400])

    async def test_individual_subquery_failure_is_skipped(self):
        import app.services.retrieval_pipeline as rp

        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            if q == "壞":
                raise RuntimeError("db down")
            return [(0, 0.5, _row("c1", "r1"))]

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            out = await rp.retrieve_context_multi(
                "原題", ["原題", "壞"], mmr_lambda=0.0, **self._KNOBS,
            )
        self.assertEqual(out, (["S"], "CTX"))
        self.assertEqual(
            [r.chunk_id for (_t, _f, r) in seen["build_scored"]], ["c1"]
        )

    async def test_all_subqueries_failed_reraises(self):
        import app.services.retrieval_pipeline as rp

        async def _fake_hybrid(session, q, vec, **kw):
            raise RuntimeError("db down")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid):
            with self.assertRaises(RuntimeError):
                await rp.retrieve_context_multi(
                    "原題", ["原題", "面向A"], mmr_lambda=0.0, **self._KNOBS,
                )

    async def test_cancellation_propagates_without_partial_results(self):
        import app.services.retrieval_pipeline as rp

        started = asyncio.Event()
        built = {"n": 0}

        async def _fake_hybrid(session, q, vec, **kw):
            started.set()
            await asyncio.sleep(30)
            return []

        def _fake_build(scored, **kw):
            built["n"] += 1
            return ([], "")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            task = asyncio.create_task(
                rp.retrieve_context_multi(
                    "原題", ["原題", "面向A"], mmr_lambda=0.0, **self._KNOBS,
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(built["n"], 0)  # 不以部分結果續跑

    async def test_multiquery_rerank_disabled_degrades_to_primary(self):
        # 緊急退場 REPORT_RERANK_ENABLED=0 → rerank_top_m=0：rerank（唯一以原題對
        # 子查詢召回打分的防線）整段跳過。多查詢必須降級回原題單查詢結果，而非讓
        # 異質 (tier, fused) 合併序＋max-over-subqueries gate 直接進 build_context。
        import app.services.retrieval_pipeline as rp

        primary = [(0, 0.5, _row("c1", "r1"))]
        sub = [(2, 0.9, _row("c2", "r2"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return primary if q == "原題" else sub

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["gate"] = kw.get("gate_scores")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題", "面向A"], mmr_lambda=0.0,
                rerank_top_m=0, **self._KNOBS,
            )
        self.assertIs(seen["build_scored"], primary)  # 原題單查詢原始結果
        self.assertIsNone(seen["gate"])               # gate 快照不傳

    async def test_offtopic_subquery_hit_retiered_against_question(self):
        # 離題子查詢的字面命中（子查詢端 tier 2）合併後以原始主題重算 tier：
        # 內容不含原題詞 → 降為 TIER_SEMANTIC，不再冒充 tier 2 霸佔頂部/繞過 floor；
        # rerank 有套用（同 tier 內重排）也無法把跨 tier 垃圾壓下去——重算才是防線。
        import app.services.retrieval_pipeline as rp
        from app.services.retrieval import TIER_PHRASE, TIER_SEMANTIC

        on_topic = [(2, 0.90, _row("c_on", "r_on", "台積電先進製程展望"))]
        off_topic = [(2, 0.95, _row("c_off", "r_off", "虛擬貨幣詐騙頻傳"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return on_topic if q == "台積電" else off_topic

        def _identity_content_rerank(question, scored, *, top_m, timer=None, deadline=None):
            return list(scored)  # 新物件＝套用；不動 tier（同 tier 內重排語意）

        def _fake_build(scored, **kw):
            seen["scored"] = list(scored)
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _identity_content_rerank), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "台積電", ["台積電", "虛擬貨幣 詐騙"], mmr_lambda=0.0,
                rerank_top_m=50, rerank_timeout=5.0, **self._KNOBS,
            )
        tiers = {r.chunk_id: t for (t, _f, r) in seen["scored"]}
        self.assertEqual(tiers["c_on"], TIER_PHRASE)     # 原題字面命中保留
        self.assertEqual(tiers["c_off"], TIER_SEMANTIC)  # 離題命中降為語意層

    async def test_build_context_runs_off_event_loop(self):
        # MMR cosine 為純 Python CPU；build_context 必須卸載到執行緒，否則在單一
        # event loop 同步跑會凍結全站併發（M2 教訓，比照 rerank/embed 卸載）。
        import threading

        import app.services.retrieval_pipeline as rp

        main_thread = threading.current_thread()
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return [(0, 0.5, _row("c1", "r1"))]

        def _fake_build(scored, **kw):
            seen["thread"] = threading.current_thread()
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題"], mmr_lambda=0.0, **self._KNOBS,
            )
        self.assertIsNot(seen["thread"], main_thread)

    async def test_cancellation_with_partial_completion_does_not_continue(self):
        # 混合 fixture：第一條查詢已完成、第二條掛起時取消——外層取消必須經
        # asyncio.gather 穿透，且不得以「已完成的部分結果」續跑 build_context。
        # （守的是外層取消傳播；個別 task 的 except Exception vs BaseException 在
        # gather＋外層取消下等價——兩者取消皆由 gather future 穿透，非此測試標的。）
        import app.services.retrieval_pipeline as rp

        q0_done = asyncio.Event()
        q1_started = asyncio.Event()
        built = {"n": 0}

        async def _fake_hybrid(session, q, vec, **kw):
            if q == "原題":
                q0_done.set()
                return [(0, 0.5, _row("c1", "r1"))]
            q1_started.set()
            await asyncio.sleep(30)
            return []

        def _fake_build(scored, **kw):
            built["n"] += 1
            return ([], "")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            task = asyncio.create_task(
                rp.retrieve_context_multi(
                    "原題", ["原題", "面向A"], mmr_lambda=0.0, **self._KNOBS,
                )
            )
            await q0_done.wait()
            await q1_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(built["n"], 0)  # 部分完成也不得續跑

    async def test_primary_failed_and_rerank_not_applied_keeps_merged(self):
        # 複合故障：原題（首條）檢索失敗＋rerank 未套用（回同一物件）。降級無原題
        # 可退 → else 分支：保留合併序（子查詢結果）、gate 不傳。此分支是研報能否
        # 生成的最後防線，若誤植（NameError／raise）會炸掉整份研報。
        import app.services.retrieval_pipeline as rp

        sub = [(1, 0.8, _row("c2", "r2", "面向內容"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            if q == "原題":
                raise RuntimeError("primary down")
            return sub

        def _identity_rerank(question, scored, *, top_m, timer=None, deadline=None):
            return scored  # 同一物件＝未套用

        def _fake_build(scored, **kw):
            seen["scored"] = list(scored)
            seen["gate"] = kw.get("gate_scores")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _identity_rerank), \
             mock.patch.object(rp, "build_context", _fake_build):
            out = await rp.retrieve_context_multi(
                "原題", ["原題", "面向A"], mmr_lambda=0.0,
                rerank_top_m=50, rerank_timeout=5.0, **self._KNOBS,
            )
        self.assertEqual(out, (["S"], "CTX"))  # 不拋：研報照常生成
        self.assertEqual([r.chunk_id for (_t, _f, r) in seen["scored"]], ["c2"])
        self.assertIsNone(seen["gate"])

    async def test_multiquery_rerank_timeout_degrades_to_primary(self):
        import time as _time

        import app.services.retrieval_pipeline as rp

        primary = [(0, 0.5, _row("c1", "r1"))]
        sub = [(2, 0.9, _row("c2", "r2"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return primary if q == "原題" else sub

        def _slow_rerank(question, scored, *, top_m, timer=None, deadline=None):
            _time.sleep(0.2)
            return list(scored)

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["gate"] = kw.get("gate_scores")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _slow_rerank), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題", "面向A"], mmr_lambda=0.0,
                rerank_top_m=50, rerank_timeout=0.05, **self._KNOBS,
            )
            await asyncio.sleep(0.3)  # 讓背景 task 收尾
        self.assertIs(seen["build_scored"], primary)  # 原題單查詢原始結果（同一物件）
        self.assertIsNone(seen["gate"])               # gate 快照不傳

    async def test_multiquery_rerank_identity_return_degrades_to_primary(self):
        import app.services.retrieval_pipeline as rp

        primary = [(0, 0.5, _row("c1", "r1"))]
        sub = [(2, 0.9, _row("c2", "r2"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return primary if q == "原題" else sub

        def _identity_rerank(question, scored, *, top_m, timer=None, deadline=None):
            return scored  # 同一物件＝fail-open 未套用

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["gate"] = kw.get("gate_scores")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _identity_rerank), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題", "面向A"], mmr_lambda=0.0,
                rerank_top_m=50, rerank_timeout=5.0, **self._KNOBS,
            )
        self.assertIs(seen["build_scored"], primary)
        self.assertIsNone(seen["gate"])

    async def test_single_query_identity_rerank_does_not_degrade(self):
        import app.services.retrieval_pipeline as rp

        primary = [(0, 0.5, _row("c1", "r1"))]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return primary

        def _identity_rerank(question, scored, *, top_m, timer=None, deadline=None):
            return scored

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["gate"] = kw.get("gate_scores")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _identity_rerank), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題"], mmr_lambda=0.0,
                rerank_top_m=50, rerank_timeout=5.0, **self._KNOBS,
            )
        # 單查詢路徑本來就等於現行：不降級、gate 快照照傳（值＝自身 fused）
        self.assertEqual(
            [r.chunk_id for (_t, _f, r) in seen["build_scored"]], ["c1"]
        )
        self.assertEqual(seen["gate"], {"c1": 0.5})

    async def test_representative_chunk_skips_empty_content(self):
        import app.services.retrieval_pipeline as rp

        rows = [
            (2, 0.9, _row("c_empty", "r1", "  \n ")),   # clean_text 後為空
            (2, 0.8, _row("c_good", "r1", "有內容")),
            (1, 0.7, _row("c_other", "r2", "另一篇")),
        ]
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return rows

        async def _fake_fetch(session, chunk_ids):
            seen["fetch_ids"] = list(chunk_ids)
            return {}

        def _fake_build(scored, **kw):
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "fetch_chunk_embeddings", _fake_fetch), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題"], mmr_lambda=0.5, **self._KNOBS,
            )
        # r1 代表 chunk 跳過空內容 c_empty、取次一非空 c_good（與 select_reports
        # 聚合的 best_chunk_id 規則對齊，取錯 chunk 會使冗餘懲罰靜默失效）
        self.assertEqual(seen["fetch_ids"], ["c_good", "c_other"])

    async def test_fetch_embeddings_failure_degrades_mmr(self):
        import app.services.retrieval_pipeline as rp

        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return [(0, 0.5, _row("c1", "r1"))]

        async def _boom_fetch(session, chunk_ids):
            raise RuntimeError("db down")

        def _fake_build(scored, **kw):
            seen["chunk_embeddings"] = kw.get("chunk_embeddings")
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "fetch_chunk_embeddings", _boom_fetch), \
             mock.patch.object(rp, "build_context", _fake_build):
            out = await rp.retrieve_context_multi(
                "原題", ["原題"], mmr_lambda=0.5, **self._KNOBS,
            )
        self.assertEqual(out, (["S"], "CTX"))     # 不拋：MMR 退化
        self.assertIn(seen["chunk_embeddings"], (None, {}))

    async def test_knobs_default_from_settings(self):
        from types import SimpleNamespace

        import app.services.retrieval_pipeline as rp

        fake_settings = SimpleNamespace(
            report_subquery_dense_scan=123,
            report_fanout_concurrency=2,
            report_total_candidates=7,
            report_mmr_enabled=True,
            report_mmr_lambda=0.42,
            report_mmr_max_per_source=5,
            report_mmr_max_per_month=2,
        )
        rows_primary = [(0, (10 - i) / 100, _row(f"c{i}", f"r{i}")) for i in range(10)]
        scans = []
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            scans.append((q, kw.get("dense_scan")))
            return rows_primary if q == "原題" else []

        def _fake_rerank(question, scored, *, top_m, timer=None, deadline=None):
            return list(scored)  # 新物件＝套用（避免多查詢降級遮蔽 total_candidates 斷言）

        async def _fake_fetch(session, chunk_ids):
            return {}

        def _fake_build(scored, **kw):
            seen["build_scored"] = scored
            seen["build_kw"] = kw
            return (["S"], "CTX")

        with mock.patch.object(rp, "get_settings", lambda: fake_settings), \
             mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "rerank_scored", _fake_rerank), \
             mock.patch.object(rp, "fetch_chunk_embeddings", _fake_fetch), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題", "面向A"],
                k=30, dense_scan=400, max_reports=25, max_passages=6,
                max_chars=40000, rerank_top_m=50,
            )
        self.assertEqual(dict(scans)["面向A"], 123)               # 子查詢掃描深度
        self.assertEqual(len(seen["build_scored"]), 7)            # total_candidates 截斷
        self.assertEqual(seen["build_kw"]["mmr_lambda"], 0.42)
        self.assertEqual(seen["build_kw"]["mmr_max_per_source"], 5)
        self.assertEqual(seen["build_kw"]["mmr_max_per_month"], 2)

    async def test_mmr_disabled_in_settings_passes_zero_lambda(self):
        from types import SimpleNamespace

        import app.services.retrieval_pipeline as rp

        fake_settings = SimpleNamespace(
            report_subquery_dense_scan=200,
            report_fanout_concurrency=3,
            report_total_candidates=600,
            report_mmr_enabled=False,
            report_mmr_lambda=0.7,
            report_mmr_max_per_source=6,
            report_mmr_max_per_month=0,
        )
        fetched = {"n": 0}
        seen = {}

        async def _fake_hybrid(session, q, vec, **kw):
            return [(0, 0.5, _row("c1", "r1"))]

        async def _fake_fetch(session, chunk_ids):
            fetched["n"] += 1
            return {}

        def _fake_build(scored, **kw):
            seen["build_kw"] = kw
            return (["S"], "CTX")

        with mock.patch.object(rp, "get_settings", lambda: fake_settings), \
             mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "fetch_chunk_embeddings", _fake_fetch), \
             mock.patch.object(rp, "build_context", _fake_build):
            await rp.retrieve_context_multi(
                "原題", ["原題"],
                k=30, dense_scan=400, max_reports=25, max_passages=6,
                max_chars=40000,
            )
        self.assertEqual(seen["build_kw"]["mmr_lambda"], 0.0)  # 總開關關 → λ=0
        self.assertEqual(fetched["n"], 0)                      # 不取 embedding


if __name__ == "__main__":
    unittest.main()
