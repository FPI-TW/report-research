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
    """數值主張偵測。

    漏判的代價不對稱：這是問答抽查閘門的唯一依據，
    漏了就等於那道門永遠不開（見 faithfulness.py 的 `_NUMERIC_RE` 註解）。
    所以正例遠多於反例，反例只保留「加了會壞事」的那幾種。
    """

    def test_flags_financial_numbers(self):
        for s in ["營收年增 30%", "毛利率達 55.2%", "目標價 1200 元",
                  "EPS 為 12.5", "上看 2 兆美元", "本益比約 18 倍"]:
            self.assertTrue(F.is_numeric_claim(s), s)

    def test_flags_index_levels_and_bare_decimals(self):
        """指數點位與裸小數（2026-07-29 生產實測的漏判型態之一）。

        `44,454點` 這類沒有任何金融名詞、也沒有 %／貨幣符號，
        原本的名詞白名單完全看不到。
        """
        for s in ["台指期一度失守45,000點關卡", "44,454點是大盤頸線支撐位置",
                  "台湾7月的出口订单动量指数（按订单值计算）为48.7",
                  "revenue reached 1,049,256 million"]:
            self.assertTrue(F.is_numeric_claim(s), s)

    def test_flags_simplified_and_english_terms(self):
        """M10 雙語上線後，judge 會拆出簡體主張、也會有英文主張。"""
        for s in ["UBS预测2026年营收为3,987,833百万", "净利年减 12%",
                  "目标价上调至 215 元", "Wistron历史平均P/E约为10x",
                  "gross margin of 8.0%", "台積電規劃於台灣新建13座晶圓廠"]:
            self.assertTrue(F.is_numeric_claim(s), s)

    def test_keyword_far_from_number_via_unit(self):
        """名詞與數字隔了 12 字以上時，靠單位（個百分點）接住。"""
        self.assertTrue(
            F.is_numeric_claim("台積電2Q26毛利率因海外擴產略低於公司指引上緣1-2個百分點")
        )

    def test_non_numeric(self):
        for s in ["台積電是全球最大晶圓代工廠", "公司看好 AI 前景", "第三點值得注意"]:
            self.assertFalse(F.is_numeric_claim(s), s)

    def test_ordinals_years_and_codenames_not_numeric(self):
        """這些是**刻意**不算數值主張的，加規則時最容易一起誤傷。

        「第 3 點」是原始註解就點名要避免的——所以「點」始終不是單位；
        指數點位改由千分位回退接住，兩者不會互相干擾。
        """
        for s in ["第 3 點提到台積電擴產", "第3點", "報告第 2 章討論產能",
                  "N3 製程量產", "2Q26 營益率符合預期",
                  "台積電於 2026 年新建晶圓廠", "多节点架构预计在2026-2027年扩大规模",
                  "引用來源 [1] 與 [2]", "PERFORMANCE improved in 2026"]:
            self.assertFalse(F.is_numeric_claim(s), s)


class NumericGateConsequenceTests(unittest.TestCase):
    """偵測漏判的**後果**：門檻整個失效。

    只測正規表示式會漏掉真正重要的事——只要一份回答裡一條數值主張都沒偵測到，
    `numeric_support_rate` 就是 None，監控頁的 below_min 與離線評測**無論多不忠實
    都看不到它**。2026-07-29 生產上就是這樣：17/17 主張全判非數值 → 全篇
    faithfulness 0.412 卻沒有任何數值支持率可比。
    """

    @staticmethod
    def _claims(texts, supported):
        return [
            F.ClaimVerdict(text=t, is_numeric=F.is_numeric_claim(t),
                           verdict="supported" if supported else "unsupported")
            for t in texts
        ]

    def test_unsupported_index_levels_now_measurable(self):
        r = F.summarize_claims(
            self._claims(["台指期一度失守45,000點關卡", "44,454點是頸線支撐"], False),
            degraded=False,
        )
        self.assertIsNotNone(r.numeric_support_rate, "偵測不到數值 → 門檻永遠不開")
        self.assertEqual(r.numeric_support_rate, 0.0)

    def test_degraded_flag_is_carried(self):
        """fail-open 那筆是「沒量到」：消費端（監控頁、離線評測）必須先看 degraded 再看分數，
        summarize_claims 只負責把旗標原樣帶出，不替消費端判斷。"""
        r = F.summarize_claims(self._claims(["毛利率 5%"], False), degraded=True)
        self.assertTrue(r.degraded)

    def test_all_supported_rate_is_one(self):
        r = F.summarize_claims(self._claims(["毛利率 55.2%"], True), degraded=False)
        self.assertEqual(r.numeric_support_rate, 1.0)


class DecomposeGroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_decompose_filters_blanks(self):
        j = _judge({"decompose": {"statements": ["a", "  ", "", "b"]}})
        out = await F.decompose_statements("x", judge=j)
        self.assertEqual(out, ["a", "b"])

    async def test_decompose_non_string_element_is_schema_error(self):
        """schema v2：statements 必須是 list[str]。v1 會把 5 靜默丟掉。"""
        j = _judge({"decompose": {"statements": ["a", 5]}})
        with self.assertRaises(F.JudgeSchemaError):
            await F.decompose_statements("x", judge=j)

    async def test_decompose_string_is_not_iterated_per_character(self):
        """v1 的潛在 bug：`{"statements": "一段字串"}` 會逐字迭代，每個字變成一條主張。"""
        j = _judge({"decompose": {"statements": "台積電展望正向"}})
        with self.assertRaises(F.JudgeSchemaError):
            await F.decompose_statements("x", judge=j)

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


class GroundSchemaV2Tests(unittest.IsolatedAsyncioTestCase):
    """schema v2 的 grounding 驗證：idx 覆蓋、排除 bool、supported 必須是布林。"""

    async def _ground(self, verdicts, n=2, strict=True):
        j = _judge({"ground": {"verdicts": verdicts}})
        return await F.ground_statements([f"s{i}" for i in range(n)], ["ctx"], judge=j, strict=strict)

    async def test_bool_idx_is_rejected(self):
        """isinstance(True, int) 為真——v1 會把 True 當成 idx 1。"""
        with self.assertRaises(F.JudgeSchemaError):
            await self._ground([{"idx": 0, "supported": True}, {"idx": True, "supported": True}])

    async def test_string_idx_and_string_supported_are_rejected(self):
        for verdicts in (
            [{"idx": "0", "supported": True}, {"idx": 1, "supported": True}],
            [{"idx": 0, "supported": "true"}, {"idx": 1, "supported": True}],
            [{"idx": 0, "supported": 1}, {"idx": 1, "supported": True}],
        ):
            with self.subTest(verdicts=verdicts), self.assertRaises(F.JudgeSchemaError):
                await self._ground(verdicts)

    async def test_idx_set_must_be_exactly_range_n(self):
        for verdicts in (
            [{"idx": 0, "supported": True}],                                   # 缺
            [{"idx": 0, "supported": True}, {"idx": 0, "supported": True}],    # 重複
            [{"idx": 0, "supported": True}, {"idx": 2, "supported": True}],    # 越界
            [{"idx": 1, "supported": True}, {"idx": 2, "supported": True}],    # 1 起算
        ):
            with self.subTest(verdicts=verdicts), self.assertRaises(F.JudgeSchemaError):
                await self._ground(verdicts)

    async def test_non_strict_only_relaxes_missing_idx(self):
        out = await self._ground([{"idx": 1, "supported": True}], n=3, strict=False)
        self.assertEqual(out, {1: True})
        with self.assertRaises(F.JudgeSchemaError):  # 越界在寬鬆模式下照樣是錯
            await self._ground([{"idx": 7, "supported": True}], n=3, strict=False)

    async def test_schema_error_is_retried_once(self):
        calls = {"n": 0}

        async def judge(system, user):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"verdicts": [{"idx": True, "supported": True}]}
            return {"verdicts": [{"idx": 0, "supported": True}]}

        out = await F.ground_statements(["s0"], ["ctx"], judge=judge)
        self.assertEqual(out, {0: True})
        self.assertEqual(calls["n"], 2)

    async def test_schema_error_retry_is_bounded(self):
        calls = {"n": 0}

        async def judge(system, user):
            calls["n"] += 1
            return {"verdicts": "壞掉"}

        with self.assertRaises(F.JudgeSchemaError):
            await F.ground_statements(["s0"], ["ctx"], judge=judge)
        self.assertEqual(calls["n"], 2)  # 原本 1 次＋重試 1 次


class CheckFaithfulnessSchemaTests(unittest.IsolatedAsyncioTestCase):
    """生產端：schema 錯 → degraded(schema)；缺 idx 仍計 unsupported（審查 L19）。"""

    async def test_missing_idx_still_counts_unsupported_not_degraded(self):
        """L19：把缺 idx 改成 degraded 等於那一題沒查——方向是漏抓。生產端維持計為 unsupported。"""
        j = _judge({
            "decompose": {"statements": ["台積電是晶圓代工廠", "毛利率達 90%"]},
            "ground": {"verdicts": [{"idx": 0, "supported": True}]},
        })
        with self.assertLogs("app.services.judge_schema", level="WARNING") as logs:
            r = await F.check_faithfulness("x", ["ctx"], judge=j)
        self.assertFalse(r.degraded)
        self.assertEqual([c.verdict for c in r.claims], ["supported", "unsupported"])
        self.assertEqual(r.faithfulness_score, 0.5)
        self.assertTrue(any("漏判" in m for m in logs.output))

    async def test_string_statements_degrade_with_schema_reason(self):
        j = _judge({"decompose": {"statements": "一段字串"}})
        r = await F.check_faithfulness("x", ["ctx"], judge=j)
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_SCHEMA)
        self.assertIsNone(r.faithfulness_score)
        self.assertEqual(r.to_evaluation()["degraded_reason"], "schema")

    async def test_bad_verdict_types_degrade_with_schema_reason(self):
        j = _judge({
            "decompose": {"statements": ["a", "b"]},
            "ground": {"verdicts": [{"idx": 0, "supported": "yes"}, {"idx": 1, "supported": True}]},
        })
        r = await F.check_faithfulness("x", ["ctx"], judge=j)
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_SCHEMA)

    async def test_evaluation_records_schema_version_2(self):
        j = _judge({"decompose": {"statements": []}})
        r = await F.check_faithfulness("找不到資料", ["ctx"], judge=j)
        self.assertEqual(r.to_evaluation()["judge_schema_version"], 2)


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
        # 量尺可追溯（PR-07）：judge 身分、解讀規則版本、耗時；未降級時沒有原因。
        self.assertEqual(ev["judge_model"], F.DEFAULT_MODEL)
        self.assertEqual(ev["judge_schema_version"], F.JUDGE_SCHEMA_VERSION)
        self.assertIsNone(ev["degraded_reason"])
        self.assertIsInstance(ev["elapsed_ms"], int)

    async def test_judge_model_is_the_model_argument(self):
        j = _judge({"decompose": {"statements": []}})
        r = await F.check_faithfulness("找不到資料", ["ctx"], judge=j, model="claude-haiku-4-5")
        self.assertEqual(r.to_evaluation()["judge_model"], "claude-haiku-4-5")

    async def test_injected_judge_failure_reason_is_error(self):
        r = await F.check_faithfulness("x", ["ctx"], judge=_judge({"decompose": None}))
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_ERROR)


class DefaultJudgeDegradedReasonTests(unittest.IsolatedAsyncioTestCase):
    """預設 judge 的失敗原因要落到 evaluation.degraded_reason：「服務掛了」與「回了看不懂
    的東西」一個是可用性問題、一個是量尺問題，監控與校準都要分得開。"""

    async def _check(self, stream):
        from unittest import mock

        with mock.patch.object(F, "stream_completion", stream):
            return await F.check_faithfulness("營收年增 30%", ["ctx"], model="claude-haiku-4-5", timeout=1.0)

    async def test_unavailable(self):
        async def stream(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2):
            raise F.LLMUnavailableError("529")
            yield  # pragma: no cover

        r = await self._check(stream)
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_UNAVAILABLE)
        ev = r.to_evaluation()
        self.assertEqual(ev["degraded_reason"], "unavailable")
        self.assertEqual(ev["judge_model"], "claude-haiku-4-5")

    async def test_parse(self):
        async def stream(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2):
            yield '{"statements": ["截斷'

        r = await self._check(stream)
        self.assertEqual(r.degraded_reason, F.DEGRADED_PARSE)

    async def test_empty(self):
        async def stream(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2):
            yield "   "

        r = await self._check(stream)
        self.assertEqual(r.degraded_reason, F.DEGRADED_EMPTY)


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
