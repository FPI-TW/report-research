# tests/test_faithfulness.py
"""M8 忠實度查核（app/services/faithfulness.py）單元測試。

judge 全用假物件（決定性、零真 LLM、零 DB）。resolve_evidence_texts 用假 session。
涵蓋：數值主張偵測、decompose/ground primitive、check_faithfulness 各分支、
fail-open（judge None → degraded）、evidence 回查、以及驗收準則「注入錯誤數字被標記」。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import faithfulness as F  # noqa: E402


def _judge(mapping):
    """依 system prompt 前綴回傳固定 dict 的假 judge。mapping: {"decompose"|"ground": value}。"""

    async def judge(system, user):
        if system.startswith("你是 RAG 評測助手"):  # DECOMPOSE_SYS
            return mapping.get("decompose")
        if system.startswith("你是 RAG 忠實度評審"):  # GROUND_SYS
            return mapping.get("ground")
        raise AssertionError(f"未預期的 system: {system[:20]}")

    return judge


class NumericClaimTests(unittest.TestCase):
    def test_flags_financial_numbers(self):
        for s in ["營收年增 30%", "毛利率達 55.2%", "目標價 1200 元",
                  "EPS 為 12.5", "上看 2 兆美元", "本益比約 18 倍"]:
            self.assertTrue(F.is_numeric_claim(s), s)

    def test_non_numeric(self):
        for s in ["台積電是全球最大晶圓代工廠", "公司看好 AI 前景", "第三點值得注意"]:
            self.assertFalse(F.is_numeric_claim(s), s)


class DecomposeGroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_decompose_filters_blanks(self):
        j = _judge({"decompose": {"statements": ["a", "  ", "", "b", 5]}})
        out = await F.decompose_statements("x", judge=j)
        self.assertEqual(out, ["a", "b"])

    async def test_decompose_judge_none_returns_none(self):
        out = await F.decompose_statements("x", judge=_judge({"decompose": None}))
        self.assertIsNone(out)  # fail-open

    async def test_ground_maps_verdicts(self):
        j = _judge({"ground": {"verdicts": [{"idx": 0, "supported": True},
                                            {"idx": 1, "supported": False}]}})
        out = await F.ground_statements(["a", "b"], ["ctx"], judge=j)
        self.assertEqual(out, {0: True, 1: False})

    async def test_ground_empty_statements_short_circuits(self):
        # 空主張不呼叫 judge
        async def boom(system, user):
            raise AssertionError("不該呼叫 judge")
        self.assertEqual(await F.ground_statements([], ["ctx"], judge=boom), {})

    async def test_faithfulness_eval_compat(self):
        j = _judge({"decompose": {"statements": ["a", "b", "c"]},
                    "ground": {"verdicts": [{"idx": 0, "supported": True},
                                            {"idx": 1, "supported": True},
                                            {"idx": 2, "supported": False}]}})
        score = await F.faithfulness("ans", ["ctx"], judge=j)
        self.assertAlmostEqual(score, 2 / 3)

    async def test_faithfulness_no_statements_returns_none(self):
        j = _judge({"decompose": {"statements": []}})
        self.assertIsNone(await F.faithfulness("找不到資料", ["ctx"], judge=j))


class CheckFaithfulnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_when_decompose_fails(self):
        r = await F.check_faithfulness("x", ["ctx"], judge=_judge({"decompose": None}))
        self.assertTrue(r.degraded)
        self.assertIsNone(r.faithfulness_score)
        self.assertEqual(r.claims, [])

    async def test_degraded_when_ground_fails(self):
        j = _judge({"decompose": {"statements": ["a"]}, "ground": None})
        r = await F.check_faithfulness("x", ["ctx"], judge=j)
        self.assertTrue(r.degraded)

    async def test_no_statements_not_degraded(self):
        j = _judge({"decompose": {"statements": []}})
        r = await F.check_faithfulness("找不到資料", ["ctx"], judge=j)
        self.assertFalse(r.degraded)
        self.assertIsNone(r.faithfulness_score)

    async def test_no_context_all_no_source(self):
        # 無可回查證據（如純外部來源）→ 全主張 no_source
        j = _judge({"decompose": {"statements": ["營收年增 30%", "看好前景"]}})
        r = await F.check_faithfulness("x", [], judge=j)
        self.assertTrue(all(c.verdict == "no_source" for c in r.claims))
        self.assertEqual(r.faithfulness_score, 0.0)
        self.assertEqual(r.numeric_support_rate, 0.0)  # 1 個數值主張、0 支持

    async def test_injected_wrong_number_is_flagged(self):
        # 驗收準則：注入含錯誤數字的樣本能被標記為 unsupported。
        # 來源說「毛利率 50%」，主張寫「毛利率 90%」→ ground 判 false。
        stmts = ["台積電是晶圓代工廠", "毛利率達 90%"]
        j = _judge({
            "decompose": {"statements": stmts},
            "ground": {"verdicts": [{"idx": 0, "supported": True},
                                    {"idx": 1, "supported": False}]},  # 錯誤數字未被支持
        })
        r = await F.check_faithfulness("報告內文", ["台積電毛利率 50%"], judge=j)
        self.assertFalse(r.degraded)
        wrong = next(c for c in r.claims if "90%" in c.text)
        self.assertTrue(wrong.is_numeric)
        self.assertEqual(wrong.verdict, "unsupported")
        # 1 數值主張、0 支持 → numeric_support_rate 0；整體 1/2
        self.assertEqual(r.numeric_support_rate, 0.0)
        self.assertEqual(r.faithfulness_score, 0.5)

    async def test_to_evaluation_shape(self):
        j = _judge({"decompose": {"statements": ["營收年增 30%"]},
                    "ground": {"verdicts": [{"idx": 0, "supported": True}]}})
        r = await F.check_faithfulness("x", ["營收年增 30%"], judge=j)
        ev = r.to_evaluation(citation_coverage=0.75)
        self.assertEqual(ev["citation_coverage"], 0.75)
        self.assertEqual(ev["numeric_support_rate"], 1.0)
        self.assertEqual(ev["faithfulness_score"], 1.0)
        self.assertEqual(len(ev["claims"]), 1)
        self.assertFalse(ev["degraded"])
        self.assertIn("非真實性保證", ev["note"])
        self.assertIn("checked_at", ev)


class ResolveEvidenceTextsTests(unittest.IsolatedAsyncioTestCase):
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeSession:
        def __init__(self, by_rid):
            self.by_rid = by_rid
            self.calls = []

        async def execute(self, stmt, params):
            self.calls.append(params["rid"])
            return ResolveEvidenceTextsTests._Result(
                [(c,) for c in self.by_rid.get(params["rid"], [])]
            )

    async def test_resolves_corpus_dedupes_reports_skips_external(self):
        from app.services.evidence import EvidenceLedger

        led = EvidenceLedger()
        led.add_corpus(report_id="r1", file_name="a.pdf")
        led.add_corpus(report_id="r1", file_name="a.pdf")  # 同報告，去重
        led.add_corpus(report_id="r2", file_name="b.pdf")
        sess = self._FakeSession({"r1": ["台積電內文一", "內文二"], "r2": ["聯電內文"]})
        out = await F.resolve_evidence_texts(led, sess)
        self.assertEqual(sess.calls, ["r1", "r2"])  # 每報告一次
        self.assertEqual(out, ["台積電內文一\n內文二", "聯電內文"])

    async def test_caps_length(self):
        from app.services.evidence import EvidenceLedger

        led = EvidenceLedger()
        led.add_corpus(report_id="r1", file_name="a.pdf")
        sess = self._FakeSession({"r1": ["x" * 10000]})
        out = await F.resolve_evidence_texts(led, sess, max_chars=100)
        self.assertEqual(len(out[0]), 100)


if __name__ == "__main__":
    unittest.main()
