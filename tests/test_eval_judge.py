import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import judge as judge_mod  # noqa: E402
from eval.judge import JudgeError, judge_json  # noqa: E402


def _fake_stream(chunks):
    """回一個模擬 stream_completion 的 async generator 工廠（忽略引數）。"""

    async def _gen(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2,
                   max_tokens=None, task=None):
        for c in chunks:
            yield c

    return _gen


class JudgeJsonTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_bare_json_object(self):
        judge_mod.stream_completion = _fake_stream(['{"statements": ', '["a", "b"]}'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["a", "b"]})

    async def test_strips_json_code_fence(self):
        judge_mod.stream_completion = _fake_stream(['```json\n{"verdicts": []}\n```'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"verdicts": []})

    async def test_extracts_json_amid_prose(self):
        judge_mod.stream_completion = _fake_stream(['好的，結果如下：{"questions": ["q1"]} 以上。'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"questions": ["q1"]})

    async def test_parses_json_array(self):
        judge_mod.stream_completion = _fake_stream(['[1, 2, 3]'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, [1, 2, 3])

    async def test_empty_response_raises(self):
        judge_mod.stream_completion = _fake_stream(['   '])
        with self.assertRaises(JudgeError):
            await judge_json("x", system="s")

    async def test_malformed_json_raises(self):
        judge_mod.stream_completion = _fake_stream(['not json at all'])
        with self.assertRaises(JudgeError):
            await judge_json("x", system="s")

    async def test_default_judge_model_is_haiku(self):
        self.assertEqual(judge_mod.DEFAULT_JUDGE_MODEL, "claude-haiku-4-5")

    async def test_json_with_brackets_in_string_value(self):
        """迴歸測試：JSON 字串值內的括號不應干擾深度計算（不平衡括號需靠 in_str 跳過）。"""
        judge_mod.stream_completion = _fake_stream(
            ['結果：{"statements": ["內容含右括號 } 符號"]}, 完成']
        )
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["內容含右括號 } 符號"]})

    async def test_prefers_object_over_leading_citation_bracket(self):
        """迴歸測試：散文中的 [n] 引註括號不應被誤判為 JSON 起點，物件優先於陣列。"""
        judge_mod.stream_completion = _fake_stream(
            ['根據片段 [2] 的內容，此主張成立。\n{"verdicts": [{"idx": 0, "supported": true}]}']
        )
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"verdicts": [{"idx": 0, "supported": True}]})


if __name__ == "__main__":
    unittest.main()


class JudgeRetryTests(unittest.IsolatedAsyncioTestCase):
    """judge 逾時是暫時性的，必須重試。

    2026-07-29：evaluation 連續兩次跑出不可用的 baseline（8 題掉 3-5 題），
    真因是 judge 的 60s 上限訂得太貼近中位數。實測 decompose 長答案正常只要 22-33s，
    但偶爾卡住就撞上 60s，而 stream_completion「已吐字就 fail-open、沒吐字就 raise」
    讓**同一個逾時**依運氣呈現為 LLMUnavailableError 或截斷 JSON——兩者同源。

    一次 judge 失敗就讓整題記成 error 被排除，剩下的平均值毫無意義，所以重試不是
    錦上添花，是讓這份工具可用的前提。
    """

    def setUp(self):
        self._orig = judge_mod.stream_completion

    def tearDown(self):
        judge_mod.stream_completion = self._orig

    @staticmethod
    def _flaky(fail_times, exc, then):
        """前 fail_times 次拋 exc，之後吐出 then。回 (gen_factory, 計數器)。"""
        calls = {"n": 0}

        def factory(*a, **k):
            async def _gen(prompt, **kw):
                calls["n"] += 1
                if calls["n"] <= fail_times:
                    raise exc
                for c in then:
                    yield c
            return _gen(*a, **k)

        return factory, calls

    async def test_retries_after_llm_unavailable(self):
        from app.services.llm import UNAVAILABLE_TIMEOUT, LLMUnavailableError

        # CLI 的逾時（kind 未分類、reason=timeout）：這個重試存在的理由
        gen, calls = self._flaky(1, LLMUnavailableError("claude 無有效回應", reason=UNAVAILABLE_TIMEOUT),
                                 ['{"statements": ["a"]}'])
        judge_mod.stream_completion = gen
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["a"]})
        self.assertEqual(calls["n"], 2)   # 失敗一次 + 成功一次

    async def test_retries_after_truncated_json(self):
        """實際觀察到的形態：串流吐到一半就斷，JSON 沒有收尾。"""
        gen, calls = self._flaky(0, None, [])
        # 第一次吐截斷 JSON、第二次吐完整的
        seq = [['```json\n{\n  "statements": [\n    "台股近兩個月'], ['{"statements": ["ok"]}']]
        state = {"i": 0}

        def factory(prompt, **kw):
            async def _gen():
                chunks = seq[min(state["i"], len(seq) - 1)]
                state["i"] += 1
                for c in chunks:
                    yield c
            return _gen()

        judge_mod.stream_completion = factory
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["ok"]})
        self.assertEqual(state["i"], 2)

    async def test_persistent_failure_still_raises(self):
        """重試有界；一直失敗照樣拋，不得把真故障吞成空結果。"""
        judge_mod.stream_completion = _fake_stream([""])
        with self.assertRaises(JudgeError):
            await judge_json("x", system="s")

    async def test_retries_zero_disables_retry(self):
        from app.services.llm import LLMUnavailableError

        gen, calls = self._flaky(1, LLMUnavailableError("boom", reason="api_error"), ['{"a": 1}'])
        judge_mod.stream_completion = gen
        with self.assertRaises(LLMUnavailableError):
            await judge_json("x", system="s", retries=0)
        self.assertEqual(calls["n"], 1)

    async def test_only_transient_llm_failures_are_retried(self):
        """暫時性（過載、網路、逾時、CLI 的 529、CLI 無輸出退出）重試；帳號層級、內容審查、單篇
        輸入錯誤、HTTP 的空回應與無從判斷的不重試——結果不會變，重打只是再付一次錢。

        ("other", "empty") 是 CLI 的形狀（子程序沒吐字就結束），("empty", "empty") 是 HTTP 的形狀
        （API 正常結束卻沒有 content）；兩者只差在 kind，見 eval/judge.py 的 `_is_retryable` 註解。"""
        from app.services.llm import LLMUnavailableError

        retried = [
            ("overloaded", "api_error"), ("network", "api_error"), ("timeout", "timeout"),
            ("other", "api_error"), ("other", "timeout"), ("other", "empty"),
        ]
        not_retried = [
            ("quota", "api_error"), ("auth", "api_error"), ("config", "api_error"),
            ("content_filter", "api_error"), ("bad_request", "api_error"),
            ("empty", "empty"), ("other", None),
        ]
        for (kind, reason), want in [(c, 2) for c in retried] + [(c, 1) for c in not_retried]:
            with self.subTest(kind=kind, reason=reason):
                exc = LLMUnavailableError("x", kind=kind, reason=reason)
                gen, calls = self._flaky(1, exc, ['{"a": 1}'])
                judge_mod.stream_completion = gen
                if want == 2:
                    self.assertEqual(await judge_json("x", system="s", retries=1), {"a": 1})
                else:
                    with self.assertRaises(LLMUnavailableError) as cm:
                        await judge_json("x", system="s", retries=1)
                    self.assertIs(cm.exception, exc)
                self.assertEqual(calls["n"], want)

    def test_default_timeout_is_evidence_based(self):
        """60s 是失敗的那個值；預設必須明顯高於實測的 22-33s 正常耗時。"""
        self.assertGreaterEqual(judge_mod.DEFAULT_JUDGE_TIMEOUT, 120)
