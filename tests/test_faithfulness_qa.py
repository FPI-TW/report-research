# tests/test_faithfulness_qa.py
"""M8c 問答迷你忠實度抽查（answer.py）。

閘門（啟用/含數字/抽樣）、_faithfulness_spot_check 落庫、_update_evaluation、fail-open。
全用假物件（零 LLM/DB）。抽查掛在 done 之後，故只測 helper 與閘門邏輯，不驅動整條
answer_question（那由既有 test_answer 的 SSE 契約覆蓋）。
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import answer as A  # noqa: E402
from app.services.faithfulness import ClaimVerdict, FaithfulnessResult  # noqa: E402


class SpotCheckGateTests(unittest.TestCase):
    """複刻 answer_question 的閘門條件，確保只在該查時查。"""

    def _should_check(self, *, enabled, qa_id, body, roll, rate):
        return bool(
            enabled and qa_id
            and A.is_numeric_claim(body)
            and roll < rate
        )

    def test_skips_when_no_numbers(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id="q", body="台積電看好 AI 前景", roll=0.0, rate=1.0))

    def test_runs_when_numeric_and_sampled(self):
        self.assertTrue(self._should_check(
            enabled=True, qa_id="q", body="營收年增 30%", roll=0.0, rate=1.0))

    def test_sampling_excludes(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id="q", body="營收年增 30%", roll=0.9, rate=0.5))

    def test_disabled_skips(self):
        self.assertFalse(self._should_check(
            enabled=False, qa_id="q", body="營收年增 30%", roll=0.0, rate=1.0))

    def test_no_qa_id_skips(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id=None, body="營收年增 30%", roll=0.0, rate=1.0))


class UpdateEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_evaluation_writes_jsonb(self):
        captured = {}

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
            async def commit(self): captured["committed"] = True

        with patch.object(A, "SessionFactory", lambda: _S()):
            await A._update_evaluation("qa-1", {"faithfulness_score": 0.5})
        self.assertTrue(captured["committed"])
        self.assertIn("evaluation", captured["sql"])
        import json
        self.assertEqual(json.loads(captured["params"]["e"])["faithfulness_score"], 0.5)

    async def test_update_evaluation_swallows_errors(self):
        class _Boom:
            async def __aenter__(self): raise RuntimeError("DB down")
            async def __aexit__(self, *a): return False

        with patch.object(A, "SessionFactory", lambda: _Boom()):
            # 不得拋出（best-effort）
            await A._update_evaluation("qa-1", {"x": 1})


_CTX = "[1] 某券商研報片段：本季營收年增 30%。"


class SpotCheckTests(unittest.IsolatedAsyncioTestCase):
    """抽查的比對基準＝模型當時看到的 context，不是 evidence 帳本回查。

    先前是回查帳本，兩個缺陷疊在一起（2026-08-21 以生產原始輸入重跑實測）：
    每份證據 4000 字 × `MAX_REPORTS` 15 ＝ 60,000 字的 payload，三次都精準停在
    `FAITHFULNESS_TIMEOUT` 的 60.0 秒（生產 12 筆評估有 5 筆因此 degraded）；
    而且回查取的是**整篇研報的前 4000 字**，答案卻可能生成自第 20,000 字附近的片段——
    比對的根本不是同一段文字，主張被判 unsupported 也不會有任何訊號。
    """

    async def _run(self, context, *, check=None, update=None):
        seen = {}

        async def fake_check(answer, context_texts, **kw):
            seen["contexts"] = context_texts
            seen["answer"] = answer
            return FaithfulnessResult(
                1.0, 1.0, [ClaimVerdict("營收年增 30%", True, "supported")],
                degraded=False,
            )

        async def fake_update(qa_id, evaluation):
            seen["qa_id"] = qa_id
            seen["eval"] = evaluation

        with patch.object(A, "check_faithfulness", check or fake_check), \
             patch.object(A, "_update_evaluation", update or fake_update):
            await A._faithfulness_spot_check("qa-1", "營收年增 30%", context)
        return seen

    async def test_spot_check_grounds_and_persists(self):
        seen = await self._run(_CTX)
        self.assertEqual(seen["qa_id"], "qa-1")
        self.assertEqual(seen["eval"]["numeric_support_rate"], 1.0)
        self.assertFalse(seen["eval"]["degraded"])

    async def test_grounds_against_the_context_the_model_saw(self):
        """這是修正的核心：judge 拿到的就是生成時的那份 context，逐字相同。"""
        seen = await self._run(_CTX)
        self.assertEqual(seen["contexts"], [_CTX])

    async def test_blank_context_does_not_fabricate_evidence(self):
        """沒有 context 就是沒有——不得回頭去帳本撈出一份「看起來像證據」的文字。"""
        for blank in ("", "   \n "):
            with self.subTest(blank=repr(blank)):
                seen = await self._run(blank)
                self.assertEqual(seen["contexts"], [])

    async def test_no_longer_resolves_evidence_texts(self):
        """帳本回查那條路是逾時的來源，抽查這條路徑不得再碰它。

        `resolve_evidence_texts` 本身仍留著——研報逐節查核靠它，且它只餵該節分配到的
        證據，payload 天生就小。
        """
        self.assertFalse(hasattr(A, "resolve_evidence_texts"))

    async def test_spot_check_failopen_on_grounding_error(self):
        async def boom(*a, **k):
            raise RuntimeError("grounding 炸了")

        updated = []

        async def fake_update(qa_id, evaluation):
            updated.append(qa_id)

        # 不得拋出；grounding 炸 → 不落庫（fail-open）
        await self._run(_CTX, check=boom, update=fake_update)
        self.assertEqual(updated, [])


class BackgroundSpawnTests(unittest.IsolatedAsyncioTestCase):
    """抽查改成背景任務——為的是放掉 `/api/ask` 的併發名額。

    那道閘門只有 3 個名額，由 request handler 持有到 SSE generator 耗盡為止。抽查掛在
    `done` 之後、對使用者完全不可見，卻會把名額一路佔到查完：實測單次 grounding 要
    48–142 秒，等於一題抽查吃掉三分之一的問答容量三分鐘。
    """

    async def test_task_is_strongly_referenced_until_it_finishes(self):
        """**沒有這個強引用，任務會被 GC 掉。**

        asyncio 只對執行中的 task 持弱引用，`create_task` 的回傳值沒人拿著就可能在任意
        await 點被回收——症狀是抽查隨機做到一半消失，而且完全沒有訊息。
        """
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow():
            started.set()
            await release.wait()

        task = A._spawn_background(slow(), name="t")
        await started.wait()
        self.assertIn(task, A._BACKGROUND_TASKS)   # 執行中被抓著
        release.set()
        await task
        await asyncio.sleep(0)                     # 讓 done callback 跑
        self.assertNotIn(task, A._BACKGROUND_TASKS)  # 結束後移除，不會無限長大

    async def test_exception_does_not_escape_to_the_request(self):
        """背景任務炸掉不得影響任何已交付的答案（fail-open）。"""
        async def boom():
            raise RuntimeError("抽查炸了")

        task = A._spawn_background(boom(), name="t")
        with self.assertRaises(RuntimeError):
            await task                              # 明確 await 才看得到
        self.assertNotIn(task, A._BACKGROUND_TASKS)


class BackgroundCapTests(unittest.IsolatedAsyncioTestCase):
    """背景抽查的上限。脫離併發閘之後這是唯一擋住 `claude` 行程無上界累積的東西。"""

    async def test_inflight_counts_only_running_tasks_with_prefix(self):
        release = asyncio.Event()

        async def wait():
            await release.wait()

        async def done_now():
            return None

        a = A._spawn_background(wait(), name="faithfulness:a")
        b = A._spawn_background(wait(), name="faithfulness:b")
        other = A._spawn_background(wait(), name="followups:x")
        finished = A._spawn_background(done_now(), name="faithfulness:finished")
        await finished
        try:
            self.assertEqual(A._background_inflight("faithfulness:"), 2)  # 不算 other、不算已結束
        finally:
            release.set()
            await asyncio.gather(a, b, other)
        self.assertEqual(A._background_inflight("faithfulness:"), 0)

    def test_cap_knob_is_non_negative_int(self):
        self.assertIsInstance(A.ASK_FAITHFULNESS_MAX_INFLIGHT, int)
        self.assertGreaterEqual(A.ASK_FAITHFULNESS_MAX_INFLIGHT, 0)


class AskTimeoutKnobTests(unittest.TestCase):
    """問答抽查的逾時與 judge 通用逾時分開。"""

    def test_ask_knob_is_not_the_generic_knob(self):
        """`faithfulness_timeout` 是 judge 單次呼叫的通用逾時；
        問答抽查實測需要遠超 60 秒，所以另有一顆、且必須更大。"""
        self.assertIsNot(A.ASK_FAITHFULNESS_TIMEOUT, None)
        self.assertGreater(A.ASK_FAITHFULNESS_TIMEOUT, A.FAITHFULNESS_TIMEOUT)

    def test_ask_knob_covers_the_measured_range(self):
        """下限依 DeepSeek 的實測重寫（PR-26/27；CLI 時代的依據是 ground 單次 48–142 秒、下限 150）。

        第二版計畫 §6.4 的規則 `max(ceil(3×p99), 60)`；judge 還沒有自己的 p99，用 9/24 探測的問答
        （deepseek-flash、thinking 關，脈絡 ≤20k 字，與 grounding payload 同量級）p95＝7.0 秒保守估：
        p99≈2×p95，一次 judge 呼叫最多 `llm_http.JSON_MAX_ATTEMPTS` 個請求，都算在同一個總期限內。
        上線後以 evaluation.elapsed_ms 的真 p99 重量、照同一條公式改這裡。"""
        import math

        from app.services import llm_http

        probe_ask_p95_s = 7.0
        p99_est = 2 * probe_ask_p95_s
        required = max(math.ceil(3 * llm_http.JSON_MAX_ATTEMPTS * p99_est), 60)
        self.assertGreaterEqual(A.ASK_FAITHFULNESS_TIMEOUT, required)

    def test_ask_knob_default_is_the_deepseek_value(self):
        """DeepSeek judge 預設 90：夠寬（上一條），也不再是 CLI 時代的 240——後者讓一次卡住的抽查佔住
        inflight 名額 4 分鐘，而 inflight 上限只有 2。"""
        from app.config import _load

        with patch.dict(os.environ, {"ASK_FAITHFULNESS_TIMEOUT": "", "LLM_PROVIDER": "deepseek"}):
            os.environ.pop("ASK_FAITHFULNESS_TIMEOUT")
            os.environ.pop("FAITHFULNESS_MODEL", None)
            s = _load()
            self.assertEqual(s.faithfulness_model, "deepseek-flash")
            self.assertEqual(s.ask_faithfulness_timeout, 90.0)

    def test_ask_knob_default_follows_the_judge_backend(self):
        """審查低4：預設值依 `is_http_model(faithfulness_model)`——Claude CLI judge 仍是 240（ground 單次
        實測 48–142 秒），不能被 DeepSeek 的 90 一起套上；空字串視同未設；顯式設值一律優先。"""
        from app.config import _load

        cases = [
            ({"LLM_PROVIDER": "claude_cli"}, 240.0),                                     # conftest 的預設表
            ({"LLM_PROVIDER": "deepseek", "FAITHFULNESS_MODEL": "claude-haiku-4-5"}, 240.0),
            ({"LLM_PROVIDER": "claude_cli", "FAITHFULNESS_MODEL": "deepseek-flash"}, 90.0),
            ({"LLM_PROVIDER": "deepseek", "ASK_FAITHFULNESS_TIMEOUT": ""}, 90.0),
            ({"LLM_PROVIDER": "claude_cli", "ASK_FAITHFULNESS_TIMEOUT": "75"}, 75.0),
            ({"LLM_PROVIDER": "deepseek", "ASK_FAITHFULNESS_TIMEOUT": "300"}, 300.0),
        ]
        for env, want in cases:
            with self.subTest(env=env), patch.dict(os.environ, env):
                if "ASK_FAITHFULNESS_TIMEOUT" not in env:
                    os.environ.pop("ASK_FAITHFULNESS_TIMEOUT", None)
                if "FAITHFULNESS_MODEL" not in env:
                    os.environ.pop("FAITHFULNESS_MODEL", None)
                self.assertEqual(_load().ask_faithfulness_timeout, want)


if __name__ == "__main__":
    unittest.main()
