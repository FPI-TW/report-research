"""scripts/judge_agreement.py：judge 描述性校準（DeepSeek 遷移 PR-18）。

不連 DB、不連網、不載模型：統計與取樣是純函式；執行迴圈注入假的 retrieve／check／ground。
釘住的是「只描述」這個性質背後的機制——CI 端點的方向、預算上限真的會停、帳號錯誤整批中止、
重建脈絡的篩選不把路由遙測鍵傳進檢索。
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import faithfulness as F  # noqa: E402
from scripts import judge_agreement as ja  # noqa: E402

FMIN = 0.9
PRICES = ja.Prices(4.0, 16.0)


def row(i: int, score: float, claims=None, *, web=False, answer="營收年增 30%") -> dict:
    claims = claims if claims is not None else [
        {"text": "營收年增 30%", "is_numeric": True, "verdict": "supported"},
        {"text": "毛利率 50%", "is_numeric": True, "verdict": "unsupported"},
    ]
    return {"id": f"00000000-0000-0000-0000-{i:012d}", "question": f"Q{i}", "answer": answer,
            "filters": {"market": "TW", "path": "corpus_qa", "decided_by": "llm", "web": web,
                        "llm_model": "deepseek-flash"},
            "evaluation": {"faithfulness_score": score, "claims": claims}, "score": score}


class PureFunctionTests(unittest.TestCase):
    def test_band(self):
        self.assertEqual([ja.band_of(s, FMIN) for s in (0.5, 0.9, 0.94, 0.95, 1.0)],
                         ["below", "edge", "edge", "above", "above"])

    def test_pick_cases_reserves_a_share_for_high_scores(self):
        rows = [row(i, 0.5) for i in range(50)] + [row(100 + i, 1.0) for i in range(50)]
        picked = ja.pick_cases(rows, 60, FMIN)
        self.assertEqual(len(picked), 60)
        self.assertEqual(sum(1 for r in picked if r["score"] >= 0.95), 20)
        # 高分帶不夠時名額讓給低分帶
        picked = ja.pick_cases([row(i, 0.5) for i in range(80)] + [row(200, 1.0)], 60, FMIN)
        self.assertEqual((len(picked), sum(1 for r in picked if r["score"] >= 0.95)), (60, 1))
        # 總數不足時全取
        self.assertEqual(len(ja.pick_cases([row(1, 0.5), row(2, 1.0)], 60, FMIN)), 2)

    def test_retrieval_filters_drop_route_telemetry(self):
        self.assertEqual(ja.retrieval_filters(row(1, 0.5)["filters"]), {"market": "TW"})
        self.assertEqual(ja.retrieval_filters(None), {})
        self.assertEqual(ja.retrieval_filters({"relates_stock": False, "market": None}), {"relates_stock": False})

    def test_haiku_claims_skip_no_source_and_malformed(self):
        texts, verdicts = ja.haiku_claims({"claims": [
            {"text": "a", "verdict": "supported"}, {"text": "b", "verdict": "no_source"},
            {"text": "", "verdict": "supported"}, "x", {"text": "c", "verdict": "unsupported"},
        ]})
        self.assertEqual((texts, verdicts), (["a", "c"], [True, False]))
        self.assertEqual(ja.haiku_claims(None), ([], []))

    def test_kappa(self):
        self.assertAlmostEqual(ja.cohen_kappa([(True, True), (False, False)] * 5), 1.0)
        self.assertAlmostEqual(ja.cohen_kappa([(True, False), (False, True)] * 5), -1.0)
        # 經典例：po=0.7、pe=0.5 → κ=0.4
        pairs = [(True, True)] * 35 + [(False, False)] * 35 + [(True, False)] * 15 + [(False, True)] * 15
        self.assertAlmostEqual(ja.cohen_kappa(pairs), 0.4)
        self.assertIsNone(ja.cohen_kappa([]))
        self.assertIsNone(ja.cohen_kappa([(True, True)] * 4), "兩邊全同一類：κ 無定義，不能回 1 假裝完全一致")

    def test_wilson_upper(self):
        self.assertIsNone(ja.wilson_upper(0, 0))
        self.assertAlmostEqual(ja.wilson_upper(0, 60), 0.0602, places=3)
        self.assertGreater(ja.wilson_upper(3, 60), 3 / 60)

    def test_bootstrap_is_seeded_and_brackets_the_mean(self):
        xs = [0.1, -0.05, 0.02, 0.0, 0.03, -0.01, 0.04, 0.06]
        a = ja.bootstrap_ci(xs, ja._mean, n_boot=500, seed=7)
        b = ja.bootstrap_ci(xs, ja._mean, n_boot=500, seed=7)
        self.assertEqual(a, b)
        self.assertLess(a[0], sum(xs) / len(xs))
        self.assertGreater(a[1], sum(xs) / len(xs))
        self.assertIsNone(ja.bootstrap_ci([0.1], ja._mean, n_boot=100, seed=1))

    def test_costs(self):
        self.assertAlmostEqual(ja.usage_cost({"prompt_tokens": 1_000_000, "completion_tokens": 500_000}, PRICES),
                               4.0 + 8.0)
        self.assertEqual(ja.usage_cost(None, PRICES), 0.0)
        small = ja.estimate_case_cost(100, 1000, 50, 2, PRICES)
        big = ja.estimate_case_cost(4000, 20000, 2000, 30, PRICES)
        self.assertGreater(big, small)
        self.assertLess(big, 1.0, "一題的估算應在幾毛錢以內，否則預算邏輯單位錯了")


class SummarizeTests(unittest.TestCase):
    def _case(self, h, d, hv, gv, *, web=False, band="edge", d_claims=None):
        return ja.CaseResult(qa_id="x", band=band, web=web, h_score=h, h_verdicts=hv, d_score=d,
                             d_n_claims=d_claims if d_claims is not None else len(hv), g_verdicts=gv)

    def test_directional_endpoints_and_flips(self):
        cases = [
            self._case(0.85, 0.95, [True, False], [True, True]),    # haiku 待複核、DeepSeek 不是（寬鬆）
            self._case(0.95, 0.85, [True, True], [True, False]),    # 反向（嚴格）
            self._case(1.0, 1.0, [True, True], [True, True], web=True),
            self._case(0.5, 0.6, [False, True], [False, True]),
        ]
        s = ja.summarize(cases, fmin=FMIN, n_boot=300, seed=1)
        e, g = s["e_mode"], s["g_mode"]
        self.assertEqual((e["flag_flips"], e["haiku_flag_only"], e["deepseek_flag_only"]), (2, 1, 1))
        self.assertAlmostEqual(e["flag_flip_rate"], 0.5)
        self.assertAlmostEqual(e["mean_shift"], 0.025)
        # |偏移| 取 CI 上界（§判準方向）：不小於 |點估計|
        self.assertGreaterEqual(e["abs_shift_ci_upper"], abs(e["mean_shift"]))
        self.assertEqual(e["abs_shift_ci_upper"], max(abs(e["shift_ci"][0]), abs(e["shift_ci"][1])))
        # κ 取 CI 下界
        self.assertLessEqual(g["kappa_ci_lower"], g["kappa"])
        self.assertEqual(g["n_verdicts"], 8)
        self.assertAlmostEqual(g["raw_agreement"], 6 / 8)
        self.assertAlmostEqual(g["lenient_rate"], 1 / 2)     # haiku 不支持 2 條，DeepSeek 支持其中 1 條
        self.assertAlmostEqual(g["strict_rate"], 1 / 6)      # haiku 支持 6 條，DeepSeek 否決 1 條
        self.assertEqual(e["by_web"]["web"]["n"], 1)
        self.assertEqual(s["reference"]["kappa_ci_lower_min"], 0.60)

    def test_missing_verdicts_are_excluded_from_kappa_but_count_as_unsupported_in_score(self):
        c = self._case(1.0, 1.0, [True, True], [True, None])
        self.assertAlmostEqual(c.g_score, 0.5)
        s = ja.summarize([c, c], fmin=FMIN, n_boot=50, seed=1)
        self.assertEqual(s["g_mode"]["n_verdicts"], 2)

    def test_degraded_cases_are_counted_not_scored(self):
        c = ja.CaseResult(qa_id="x", band="below", web=False, h_score=0.5, h_verdicts=[True],
                          d_degraded="content_risk", g_degraded="content_risk")
        s = ja.summarize([c], fmin=FMIN, n_boot=50, seed=1)
        self.assertEqual(s["degraded"], {"content_risk": 2})
        self.assertEqual(s["e_mode"]["n"], 0)
        self.assertIsNone(s["g_mode"]["kappa"])


class _FakeResult:
    def __init__(self, score=None, claims=2, degraded=None, usage=None):
        self.faithfulness_score = score
        self.claims = [object()] * claims
        self.degraded = degraded is not None
        self.degraded_reason = degraded
        self.usage = usage or {"prompt_tokens": 10_000, "completion_tokens": 1_000}
        self.judge_fingerprint = "fp1"
        self.judge_model_resp = "deepseek-v4.1-flash"


class RunCasesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.retrieved: list[tuple[str, dict]] = []
        self.logs: list[str] = []

    async def retrieve(self, question, filters):
        self.retrieved.append((question, filters))
        return "" if question == "Q-empty" else "[1] 報告：x\n營收年增 30%"

    async def run_cases(self, rows, *, check, ground, max_cny=100.0):
        return await ja.run_cases(rows, model="deepseek-flash", timeout=5, fmin=FMIN, prices=PRICES, max_cny=max_cny,
                                  retrieve=self.retrieve, check=check, ground=ground, log=self.logs.append)

    async def test_e_and_g_modes(self):
        async def check(answer, contexts, *, model, timeout):
            self.assertEqual(model, "deepseek-flash")
            return _FakeResult(score=1.0, claims=3)

        async def ground(texts, contexts, *, judge, strict):
            self.assertFalse(strict, "G 模式要用生產那把尺（缺 idx 計 unsupported）")
            return {0: True, 1: True}

        cases, meta = await self.run_cases([row(1, 0.5), dict(row(2, 0.5), question="Q-empty")],
                                           check=check, ground=ground)
        self.assertEqual(len(cases), 1)
        self.assertEqual(meta["skipped"], {"no_context": 1})
        c = cases[0]
        self.assertEqual((c.d_score, c.d_n_claims, c.g_verdicts), (1.0, 3, [True, True]))
        self.assertEqual(c.fingerprints, ["fp1"])
        self.assertGreater(c.cost_cny, 0)
        self.assertEqual(self.retrieved[0], ("Q1", {"market": "TW"}))

    async def test_budget_stops_before_overspending(self):
        async def check(answer, contexts, *, model, timeout):
            return _FakeResult(score=1.0, usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})  # ¥4／題

        async def ground(texts, contexts, *, judge, strict):
            return {0: True, 1: False}

        cases, meta = await self.run_cases([row(i, 0.5) for i in range(10)], check=check, ground=ground,
                                           max_cny=10.0)
        self.assertEqual(len(cases), 2, "第 3 題以已完成題平均 ¥4 估算會超過 ¥10，要在開跑前停")
        self.assertIn("預算上限", meta["stop_reason"])
        self.assertLessEqual(meta["spent_cny_est"], 10.0)

    async def test_account_error_aborts_the_batch(self):
        async def check(answer, contexts, *, model, timeout):
            return _FakeResult(degraded=F.DEGRADED_ACCOUNT)

        async def ground(*a, **k):
            raise AssertionError("帳號錯誤之後不該再打")

        with self.assertRaises(ja.AccountError):
            await self.run_cases([row(1, 0.5), row(2, 0.5)], check=check, ground=ground)

    async def test_content_risk_is_recorded_and_the_batch_goes_on(self):
        async def check(answer, contexts, *, model, timeout):
            return _FakeResult(degraded=F.DEGRADED_CONTENT_RISK)

        async def ground(texts, contexts, *, judge, strict):
            raise F.JudgeSchemaError("idx 越界")

        cases, _meta = await self.run_cases([row(1, 0.5), row(2, 0.5)], check=check, ground=ground)
        self.assertEqual([(c.d_degraded, c.g_degraded) for c in cases], [("content_risk", "schema")] * 2)

    async def test_real_default_judge_objects_are_wired(self):
        """預設的 check／ground 就是生產的 check_faithfulness／ground_statements（量的是同一把尺）。"""
        self.assertIs(ja.run_cases.__kwdefaults__["check"], F.check_faithfulness)
        self.assertIs(ja.run_cases.__kwdefaults__["ground"], F.ground_statements)
        self.assertTrue(math.isclose(ja.EDGE, 0.95))


if __name__ == "__main__":
    unittest.main()
