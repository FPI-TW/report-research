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
    vals = [None] * 16
    vals[1] = rid       # report_id
    vals[14] = content  # content
    vals[15] = 0.0      # distance
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
    def test_overwrites_fused_keeps_tier_and_row(self):
        scored = _scored((0, 0.9, "A", "aa"), (0, 0.8, "B", "bb"), (0, 0.7, "C", "cc"))
        rows = [r for (_t, _f, r) in scored]
        with mock.patch.object(rr, "rerank_scores", lambda q, ps: [0.2, 0.5, 0.9]):
            out = rr.rerank_scored("q", scored, top_m=3)
        self.assertEqual([t for (t, _f, _r) in out], [0, 0, 0])       # tier 保留
        self.assertEqual([f for (_t, f, _r) in out], [0.2, 0.5, 0.9]) # fused=rerank 分
        self.assertEqual([r for (_t, _f, r) in out], rows)            # row 物件不變

    def test_passes_content_to_rerank(self):
        scored = _scored((0, 0.9, "A", "內容一"), (0, 0.8, "B", "內容二"))
        seen = {}
        def _fake(q, ps):
            seen["q"], seen["ps"] = q, ps
            return [0.1, 0.2]
        with mock.patch.object(rr, "rerank_scores", _fake):
            rr.rerank_scored("我的問題", scored, top_m=2)
        self.assertEqual(seen["q"], "我的問題")
        self.assertEqual(seen["ps"], ["內容一", "內容二"])

    def test_tail_compressed_below_min_rerank_preserves_order(self):
        scored = _scored(
            (0, 0.9, "A", "a"), (0, 0.8, "B", "b"),
            (0, 0.7, "C", "c"), (0, 0.6, "D", "d"),
        )
        with mock.patch.object(rr, "rerank_scores", lambda q, ps: [0.4, 0.6]):
            out = rr.rerank_scored("q", scored, top_m=2)
        head, tail = out[:2], out[2:]
        self.assertEqual([f for (_t, f, _r) in head], [0.4, 0.6])      # 重排分覆蓋
        self.assertEqual([r.content for (_t, _f, r) in head], ["a", "b"])
        min_rr = 0.4
        self.assertTrue(all(f < min_rr for (_t, f, _r) in tail))       # 尾段嚴格低於最低重排分
        self.assertEqual([r.content for (_t, _f, r) in tail], ["c", "d"])  # 保相對序
        self.assertGreaterEqual(tail[0][1], tail[1][1])                # C(原0.7) >= D(原0.6)

    def test_top_m_truncates_reranked_head(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"), (0, 0.7, "C", "c"))
        calls = {}
        def _fake(q, ps):
            calls["ps"] = ps
            return [0.5]  # 僅 1 個（top_m=1）
        with mock.patch.object(rr, "rerank_scores", _fake):
            out = rr.rerank_scored("q", scored, top_m=1)
        self.assertEqual(calls["ps"], ["a"])          # 只重排前 1 個
        self.assertEqual(out[0][1], 0.5)              # A 用重排分
        self.assertEqual([r.content for (_t, _f, r) in out], ["a", "b", "c"])  # 全部保留（保 recall）

    def test_tier_preserved_on_all_items(self):
        scored = _scored((0, 0.9, "A", "a"), (1, 0.5, "B", "b"))  # A tier0, B tier1
        with mock.patch.object(rr, "rerank_scores", lambda q, ps: [0.2]):
            out = rr.rerank_scored("q", scored, top_m=1)          # head=[A], tail=[B]
        self.assertEqual(out[0][0], 0)  # A tier0
        self.assertEqual(out[1][0], 1)  # B tier1 保留

    def test_fail_open_on_rerank_exception_returns_original(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        def _boom(q, ps):
            raise RuntimeError("model down")
        with mock.patch.object(rr, "rerank_scores", _boom):
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIs(out, scored)  # 逐字節不變（同物件）

    def test_shape_mismatch_fails_open(self):
        scored = _scored((0, 0.9, "A", "a"), (0, 0.8, "B", "b"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps: [0.5]):  # 長度不符
            out = rr.rerank_scored("q", scored, top_m=2)
        self.assertIs(out, scored)

    def test_zero_top_m_and_empty_are_passthrough(self):
        scored = _scored((0, 0.9, "A", "a"))
        self.assertIs(rr.rerank_scored("q", scored, top_m=0), scored)
        self.assertEqual(rr.rerank_scored("q", [], top_m=5), [])

    def test_tier_priority_preserved_through_build_context(self):
        # 端到端：tier1 字面命中即使 rerank 分低，仍排在 tier0 語意（rerank 分高）之上
        from app.services.answer import build_context
        scored = _scored((1, 0.5, "lit", "字面"), (0, 0.9, "sem", "語意"))
        with mock.patch.object(rr, "rerank_scores", lambda q, ps: [0.1, 0.95]):
            reranked = rr.rerank_scored("q", scored, top_m=2)
        sources, _ = build_context(reranked, now=datetime(2026, 6, 17, tzinfo=timezone.utc))
        self.assertEqual(sources[0].report_id, "lit")  # tier 硬性優先於 rerank 分


if __name__ == "__main__":
    unittest.main()
