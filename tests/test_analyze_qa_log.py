# tests/test_analyze_qa_log.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import analyze_qa_log as an  # noqa: E402


class PercentileTests(unittest.TestCase):
    def test_basic_nearest_rank(self):
        v = list(range(1, 101))  # 1..100
        r = an.percentiles(v, [50, 90, 99])
        self.assertEqual(r[50], 50)
        self.assertEqual(r[90], 90)
        self.assertEqual(r[99], 99)

    def test_unsorted_input(self):
        r = an.percentiles([30, 10, 20], [50])
        self.assertEqual(r[50], 20)

    def test_empty_is_none(self):
        self.assertEqual(an.percentiles([], [50, 90]), {50: None, 90: None})


class BucketizeTests(unittest.TestCase):
    def test_counts_over_thresholds(self):
        ages = [100, 400, 800]
        r = an.bucketize(ages, [365, 540])
        self.assertEqual(r[365], 2)  # 400, 800 > 365
        self.assertEqual(r[540], 1)  # 800 > 540

    def test_empty(self):
        r = an.bucketize([], [365])
        self.assertEqual(r[365], 0)


class PctTests(unittest.TestCase):
    def test_ratio(self):
        self.assertEqual(an.pct(2, 4), 0.5)

    def test_zero_whole_is_zero(self):
        self.assertEqual(an.pct(0, 0), 0.0)


class SummarizeTests(unittest.TestCase):
    OFFTOPIC = {"離題訊息", "無脈絡訊息"}

    def _rows(self):
        return [
            {"latency_ms": 5000, "thinking_ms": 1000, "feedback": "like", "answer": "正常答案"},
            {"latency_ms": 9000, "thinking_ms": 2000, "feedback": None, "answer": "正常答案2"},
            {"latency_ms": 3000, "thinking_ms": 500, "feedback": "dislike", "answer": "離題訊息"},
        ]

    def test_offtopic_and_feedback_counts(self):
        s = an.summarize(self._rows(), [100, 400], self.OFFTOPIC)
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["offtopic"], 1)
        self.assertEqual(s["feedback"]["like"], 1)
        self.assertEqual(s["feedback"]["dislike"], 1)
        self.assertEqual(s["feedback"]["none"], 1)

    def test_cited_age_stats(self):
        s = an.summarize(self._rows(), [100, 400, 800], self.OFFTOPIC)
        self.assertEqual(s["n_cited"], 3)
        self.assertEqual(s["cited_age"]["median"], 400)
        # 400, 800 > 365 → 2/3
        self.assertAlmostEqual(s["cited_age"]["pct_over_365"], 2 / 3)

    def test_empty_rows(self):
        s = an.summarize([], [], self.OFFTOPIC)
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["cited_age"]["median"])


if __name__ == "__main__":
    unittest.main()
