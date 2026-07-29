import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.rerank as rr  # noqa: E402

from datetime import datetime, timezone  # noqa: E402

from app.services.rows import ChunkRow  # noqa: E402


def _row(rid, content):
    # 位置一律由 _fields 推導，不要寫死數字：ChunkRow 中段插欄（file_hash、title…）
    # 時寫死的索引會無聲指向錯欄——scripts/eval_retrieval.py 曾因此讓評測分數變垃圾。
    vals = [None] * len(ChunkRow._fields)
    vals[ChunkRow._fields.index("report_id")] = rid
    vals[ChunkRow._fields.index("content")] = content
    vals[ChunkRow._fields.index("distance")] = 0.0
    return ChunkRow._make(vals)


def _scored(*items):
    """items 為 (tier, fused, report_id, content)。"""
    return [(t, f, _row(rid, c)) for (t, f, rid, c) in items]


class _FakeModel:
    def __init__(self, scores):
        self._scores = scores
        self.calls = []

    def compute_score(self, pairs, normalize=False):
        self.calls.append((list(pairs), normalize))
        # FlagReranker：單一 pair 回 float、多 pair 回 list
        return self._scores if len(pairs) != 1 else self._scores[0]


class RerankScoresTests(unittest.TestCase):
    def test_returns_score_per_passage(self):
        fake = _FakeModel([0.3, 0.7])
        with mock.patch.object(rr, "_get_model", lambda: fake):
            out = rr.rerank_scores("q", ["a", "b"])
        self.assertEqual(out, [0.3, 0.7])
        # 一次批次、normalize=True（sigmoid [0,1]）
        self.assertEqual(len(fake.calls), 1)
        pairs, normalize = fake.calls[0]
        self.assertEqual(pairs, [("q", "a"), ("q", "b")])
        self.assertTrue(normalize)

    def test_single_passage_float_wrapped_to_list(self):
        fake = _FakeModel([0.42])
        with mock.patch.object(rr, "_get_model", lambda: fake):
            out = rr.rerank_scores("q", ["only"])
        self.assertEqual(out, [0.42])

    def test_empty_passages_returns_empty_without_model(self):
        called = {"n": 0}

        def _boom():
            called["n"] += 1
            raise AssertionError("model should not load for empty passages")

        with mock.patch.object(rr, "_get_model", _boom):
            out = rr.rerank_scores("q", [])
        self.assertEqual(out, [])
        self.assertEqual(called["n"], 0)


class RerankScoredTests(unittest.TestCase):
    def test_overwrites_fused_and_sorts_head_by_score_desc(self):
        scored = _scored((0, 0.9, "A", "aa"), (0, 0.8, "B", "bb"), (0, 0.7, "C", "cc"))
        rows = {id(r) for (_t, _f, r) in scored}
        # rerank 依 head 原序對應：A→0.2、B→0.5、C→0.9
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.2, 0.5, 0.9]):
            out = rr.rerank_scored("q", scored, top_m=3)
        self.assertEqual([t for (t, _f, _r) in out], [0, 0, 0])            # tier 保留
        # head 依 rerank 分降序：C(0.9) > B(0.5) > A(0.2)，分數隨 row 正確搬移（無錯位）
        self.assertEqual([r.content for (_t, _f, r) in out], ["cc", "bb", "aa"])
        self.assertEqual([f for (_t, f, _r) in out], [0.9, 0.5, 0.2])
        self.assertEqual({id(r) for (_t, _f, r) in out}, rows)             # 仍為原 row 物件

    def test_passes_content_to_rerank(self):
        scored = _scored((0, 0.9, "A", "內容一"), (0, 0.8, "B", "內容二"))
        seen = {}
        def _fake(q, ps, deadline=None):
            seen["q"], seen["ps"] = q, ps
            return [0.1, 0.2]
        with mock.patch.object(rr, "rerank_scores", _fake):
            rr.rerank_scored("我的問題", scored, top_m=2)
        self.assertEqual(seen["q"], "我的問題")
        self.assertEqual(seen["ps"], ["內容一", "內容二"])

    def test_tail_keeps_original_fused_scores_for_recall(self):
        scored = _scored(
            (0, 0.9, "A", "a"), (0, 0.8, "B", "b"),
            (0, 0.7, "C", "c"), (0, 0.6, "D", "d"),
        )
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.4, 0.6]):
            out = rr.rerank_scored("q", scored, top_m=2)
        head, tail = out[:2], out[2:]
        self.assertEqual([f for (_t, f, _r) in head], [0.6, 0.4])      # head 依 rerank 分降序
        self.assertEqual([r.content for (_t, _f, r) in head], ["b", "a"])
        self.assertEqual([f for (_t, f, _r) in tail], [0.7, 0.6])     # 保持與 select_reports 相容的尺度
        self.assertEqual([r.content for (_t, _f, r) in tail], ["c", "d"])  # 保相對序
        self.assertGreaterEqual(tail[0][1], tail[1][1])                # C(原0.7) >= D(原0.6)

    def test_top_m_truncates_reranked_head(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"), (0, 0.7, "C", "c"))
        calls = {}
        def _fake(q, ps, deadline=None):
            calls["ps"] = ps
            return [0.5]  # 僅 1 個（top_m=1）
        with mock.patch.object(rr, "rerank_scores", _fake):
            out = rr.rerank_scored("q", scored, top_m=1)
        self.assertEqual(calls["ps"], ["a"])          # 只重排前 1 個
        self.assertEqual(out[0][1], 0.5)              # A 用重排分
        self.assertEqual([r.content for (_t, _f, r) in out], ["a", "b", "c"])  # 全部保留（保 recall）

    def test_tier_preserved_on_all_items(self):
        scored = _scored((0, 0.9, "A", "a"), (1, 0.5, "B", "b"))  # A tier0, B tier1
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.2]):
            out = rr.rerank_scored("q", scored, top_m=1)          # head=[A], tail=[B]
        self.assertEqual(out[0][0], 0)  # A tier0
        self.assertEqual(out[1][0], 1)  # B tier1 保留

    def test_fail_open_on_rerank_exception_returns_original(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        def _boom(q, ps, deadline=None):
            raise RuntimeError("model down")
        with mock.patch.object(rr, "rerank_scores", _boom):
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIs(out, scored)  # 逐字節不變（同物件）

    def test_shape_mismatch_fails_open(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.5]):  # 長度不符
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIs(out, scored)

    def test_zero_top_m_and_empty_are_passthrough(self):
        scored = _scored((0, 0.9, "A", "a"))
        self.assertIs(rr.rerank_scored("q", scored, top_m=0), scored)
        self.assertEqual(rr.rerank_scored("q", [], top_m=5), [])

    def test_zero_min_rerank_keeps_tail_scores(self):
        scored = _scored(
            (0, 0.9, "A", "a"), (0, 0.8, "B", "b"),
            (0, 0.7, "C", "c"), (0, 0.6, "D", "d"),
        )
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.5, 0.0]):
            out = rr.rerank_scored("q", scored, top_m=2)
        head, tail = out[:2], out[2:]
        self.assertEqual([f for (_t, f, _r) in head], [0.5, 0.0])  # min_rr=0
        self.assertEqual([f for (_t, f, _r) in tail], [0.7, 0.6])
        self.assertEqual([r.content for (_t, _f, r) in tail], ["c", "d"])  # 保相對序
        self.assertGreaterEqual(tail[0][1], tail[1][1])             # C(原0.7) >= D(原0.6)

    def test_tier_priority_preserved_through_build_context(self):
        # 端到端：tier1 字面命中即使 rerank 分低，仍排在 tier0 語意（rerank 分高）之上
        from app.services.answer import build_context
        scored = _scored((1, 0.5, "lit", "字面"), (0, 0.9, "sem", "語意"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.1, 0.95]):
            reranked = rr.rerank_scored("q", scored, top_m=2)
        sources, _ = build_context(reranked, now=datetime(2026, 6, 17, tzinfo=timezone.utc))
        self.assertEqual(sources[0].report_id, "lit")  # tier 硬性優先於 rerank 分

    def test_nan_score_fails_open(self):
        # 模型回非有限分數（NaN）→ fail-open 回原 scored（float(nan) 不會拋，須顯式攔）
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.5, float("nan")]):
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIs(out, scored)

    def test_tail_span_zero_keeps_all_equal_fused(self):
        scored = _scored(
            (0, 0.9, "A", "a"), (0, 0.8, "B", "b"),
            (0, 0.5, "C", "c"), (0, 0.5, "D", "d"),  # tail 同分 → span==0
        )
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.6, 0.4]):
            out = rr.rerank_scored("q", scored, top_m=2)
        tail = out[2:]
        self.assertEqual([f for (_t, f, _r) in tail], [0.5, 0.5])

    def test_load_failure_is_cached_and_disables_rerank(self):
        # 模型載入失敗一次後熔斷：不再重試，_get_model 回 None、rerank_scores 回 []（不拋）
        calls = {"n": 0}

        def _boom_ctor(name):
            calls["n"] += 1
            raise RuntimeError("no network")

        with mock.patch.object(rr, "_model", None), \
             mock.patch.object(rr, "_load_failed", False), \
             mock.patch.object(rr, "_CrossEncoder", _boom_ctor):
            first = rr._get_model()
            second = rr._get_model()
            scores = rr.rerank_scores("q", ["a", "b"])
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls["n"], 1)   # 只嘗試載入一次（熔斷、無重試）
        self.assertEqual(scores, [])      # 模型不可用 → 空 → rerank_scored 以形狀不符 fail-open

    def test_tail_ranked_below_head_through_select_reports(self):
        # 端到端經真實 select_reports：reranked head 報告排在壓縮 tail 之上，且 min_reports
        # 保底仍納入 tail 一篇（守最小 recall）。記錄實際邊界：min_reports 之上、被壓到
        # relevance_floor 之下的 tail 會被剔除（見 M2 審查 finding 3；生產 top_m>>max_reports
        # 使 tail 幾乎不進最終選集，實務影響有限）。
        from app.services.answer import build_context
        scored = _scored(
            (0, 0.90, "r1", "一"), (0, 0.85, "r2", "二"),
            (0, 0.80, "r3", "三"), (0, 0.75, "r4", "四"), (0, 0.70, "r5", "五"),
        )
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.95, 0.90]):
            reranked = rr.rerank_scored("q", scored, top_m=2)  # head=r1,r2；tail=r3,r4,r5
        sources, _ = build_context(reranked, now=datetime(2026, 6, 17, tzinfo=timezone.utc))
        ids = [s.report_id for s in sources]
        self.assertEqual(ids[:2], ["r1", "r2"])       # reranked head 兩篇最前
        self.assertGreaterEqual(len(sources), 3)      # min_reports=3 保底（tail 至少一篇）


class RerankBatchDeadlineTests(unittest.TestCase):
    """rerank 逾時修補：mini-batch 推論 + deadline 中止。

    prod 實測（20 核 CPU）：載入 44-52s、50 對 34s、120 對 93s，而共用逾時 30s →
    兩路徑 rerank 實質全關；且逾時後推論繼續燒 CPU 並持有 semaphore。deadline 使
    被放棄的背景工作在批次邊界提早收手。
    """

    def test_batches_pairs_and_concatenates_scores_in_order(self):
        class _BatchFake:
            def __init__(self):
                self.batch_sizes = []
                self._n = 0

            def compute_score(self, pairs, normalize=False):
                self.batch_sizes.append(len(pairs))
                out = [float(self._n + i) for i in range(len(pairs))]
                self._n += len(pairs)
                return out

        fake = _BatchFake()
        with mock.patch.object(rr, "_get_model", lambda: fake):
            out = rr.rerank_scores("q", [f"p{i}" for i in range(40)])
        self.assertEqual(fake.batch_sizes, [16, 16, 8])   # _BATCH_SIZE=16 分批
        self.assertEqual(out, [float(i) for i in range(40)])  # 分數依原序串接

    def test_deadline_already_expired_skips_inference(self):
        fake = _FakeModel([0.1, 0.2])
        with mock.patch.object(rr, "_get_model", lambda: fake), \
             mock.patch.object(rr, "_monotonic", lambda: 100.0):
            out = rr.rerank_scores("q", ["a", "b"], deadline=99.0)
        self.assertEqual(out, [])          # 空 → rerank_scored 以形狀不符 fail-open
        self.assertEqual(fake.calls, [])   # 完全不推論

    def test_deadline_between_batches_aborts(self):
        class _CountFake:
            def __init__(self):
                self.calls = 0

            def compute_score(self, pairs, normalize=False):
                self.calls += 1
                return [0.5] * len(pairs)

        fake = _CountFake()
        clock = iter([50.0, 150.0])  # 批1檢查點 50（未過期→跑）、批2檢查點 150（過期→中止）
        with mock.patch.object(rr, "_get_model", lambda: fake), \
             mock.patch.object(rr, "_monotonic", lambda: next(clock)):
            out = rr.rerank_scores("q", [f"p{i}" for i in range(20)], deadline=100.0)
        self.assertEqual(fake.calls, 1)  # 只跑第一批，之後停止燒 CPU
        self.assertEqual(out, [])

    def test_no_deadline_runs_all_batches(self):
        class _CountFake:
            def __init__(self):
                self.calls = 0

            def compute_score(self, pairs, normalize=False):
                self.calls += 1
                return [0.5] * len(pairs)

        fake = _CountFake()
        with mock.patch.object(rr, "_get_model", lambda: fake):
            out = rr.rerank_scores("q", [f"p{i}" for i in range(20)])
        self.assertEqual(fake.calls, 2)
        self.assertEqual(len(out), 20)

    def test_rerank_scored_forwards_deadline_and_fails_open_on_abort(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        seen = {}

        def _fake(q, ps, deadline=None):
            seen["deadline"] = deadline
            return []  # deadline 中止 → 空

        with mock.patch.object(rr, "rerank_scores", _fake):
            out = rr.rerank_scored("q", scored, top_m=2, deadline=123.0)
        self.assertEqual(seen["deadline"], 123.0)
        self.assertIs(out, scored)  # fail-open 回原 scored

    def test_warmup_reports_model_availability(self):
        fake = _FakeModel([0.5])
        with mock.patch.object(rr, "_get_model", lambda: fake):
            self.assertTrue(rr.warmup())
        with mock.patch.object(rr, "_get_model", lambda: None):
            self.assertFalse(rr.warmup())  # 載入失敗/熔斷 → False（不拋）


class RerankIdentityContractTests(unittest.TestCase):
    """rerank_scored 回傳物件同一性契約（M6 明文化）：fail-open 路徑一律回傳
    「輸入的同一 list 物件」、成功路徑回新建 list——retrieval_pipeline._rerank_stage
    以 `is` 判定 rerank 是否實際套用（多查詢降級依據），重構不得默默破壞。"""

    def test_success_returns_new_list_object(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.1, 0.2]):
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIsNot(out, scored)

    def test_shape_mismatch_returns_same_object(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: [0.5]):
            self.assertIs(rr.rerank_scored("q", scored, top_m=2), scored)

    def test_model_unavailable_empty_scores_returns_same_object(self):
        scored = _scored((0, 0.9, "A", "a"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps, deadline=None: []):
            self.assertIs(rr.rerank_scored("q", scored, top_m=1), scored)

    def test_nan_returns_same_object(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(
            rr, "rerank_scores", lambda q, ps, deadline=None: [float("nan"), 0.5]
        ):
            self.assertIs(rr.rerank_scored("q", scored, top_m=2), scored)

    def test_exception_returns_same_object(self):
        scored = _scored((0, 0.9, "A", "a"))

        def _boom(q, ps, deadline=None):
            raise RuntimeError("model down")

        with mock.patch.object(rr, "rerank_scores", _boom):
            self.assertIs(rr.rerank_scored("q", scored, top_m=1), scored)

    def test_deadline_abort_returns_same_object(self):
        # deadline 已過期 → rerank_scores 回 [] → 形狀不符 fail-open（同一物件）
        scored = _scored((0, 0.9, "A", "a"))
        fake = _FakeModel([0.1])
        with mock.patch.object(rr, "_get_model", lambda: fake), \
             mock.patch.object(rr, "_monotonic", lambda: 100.0):
            out = rr.rerank_scored("q", scored, top_m=1, deadline=99.0)
        self.assertIs(out, scored)

    def test_passthrough_returns_same_object(self):
        scored = _scored((0, 0.9, "A", "a"))
        self.assertIs(rr.rerank_scored("q", scored, top_m=0), scored)
        empty = []
        self.assertIs(rr.rerank_scored("q", empty, top_m=5), empty)


if __name__ == "__main__":
    unittest.main()
