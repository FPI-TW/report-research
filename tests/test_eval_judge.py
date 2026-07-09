import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import judge as judge_mod  # noqa: E402
from eval.judge import JudgeError, judge_json  # noqa: E402


def _fake_stream(chunks):
    """回一個模擬 stream_completion 的 async generator 工廠（忽略引數）。"""

    async def _gen(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2):
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
        """迴歸測試：JSON 字串值內的括號不應干擾深度計算。"""
        judge_mod.stream_completion = _fake_stream(['結果：{"statements": ["用 {模板} 產生 [注意]"]}, 完成'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["用 {模板} 產生 [注意]"]})


if __name__ == "__main__":
    unittest.main()
