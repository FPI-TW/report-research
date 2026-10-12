import copy
import json
import unittest
from pathlib import Path

from eval.question_contract import load_dataset, validate_dataset

DATASET = Path(__file__).resolve().parents[1] / "eval" / "ragas_questions.json"


class QuestionContractTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(DATASET.read_text(encoding="utf-8"))

    def test_frozen_corpus_qa_set(self):
        questions, digest = load_dataset(DATASET)
        self.assertEqual(len(questions), 18)
        self.assertEqual(len(digest), 64)
        self.assertEqual({q["scope"] for q in questions}, {"corpus_qa"})
        self.assertTrue({"TW", "WTX", "MACRO"} <= {q["filters"].get("market") for q in questions})
        self.assertTrue(any("數值單位" in q["question"] for q in questions))
        self.assertTrue(any("兩家券商" in q["question"] for q in questions))

    def test_rejects_shape_duplicates_and_non_corpus_cases(self):
        mutations = [
            lambda d: d.update(count=17),
            lambda d: d.update(version=1),
            lambda d: d.update(generated_at="2026-09-29"),
            lambda d: d["questions"][1].update(id="q001"),
            lambda d: d["questions"][1].update(question=d["questions"][0]["question"] + "？"),
            lambda d: d["questions"][1].update(scope="time_sensitive"),
            lambda d: d["questions"][1].update(filters={"path": "overview"}),
            lambda d: d["questions"][1].update(filters={"market": "BAD"}),
            lambda d: d["questions"][1].update(filters={"market": ["TW"]}),
            lambda d: d["questions"][1].update(question="題目內容'"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                doc = copy.deepcopy(self.doc)
                mutate(doc)
                with self.assertRaises(ValueError):
                    validate_dataset(doc)


if __name__ == "__main__":
    unittest.main()
