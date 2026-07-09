import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.rerank as rr  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
