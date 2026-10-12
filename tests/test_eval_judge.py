"""eval/judge.py：離線評測的 judge 原語（PR-M 起只走 DeepSeek，`llm_http.complete_json`）。

HTTP 路徑的行為（請求形狀、階段預算、帳號錯誤、invalid_json）在 tests/test_judge_http.py；這裡只留
與 backend 無關的模組層約定。

PR-M 前這裡大多是 CLI judge 的測試：從串流文字裡容錯解析 JSON（圍欄、前後散文、字串內的括號、[n] 引註），
以及 `judge_json` 自己的逾時重試（CLI 的逾時依運氣呈現為例外或截斷 JSON，兩者同源）。DeepSeek judge 走
JSON 模式（回應要嘛是合法 JSON、要嘛 `invalid_json` → `JudgeError`），重試只在 adapter 那一層、受階段預算
限制，所以那兩組測試隨 CLI 一起刪除；「失敗原因分得出來」與「重試不相乘」的意圖由 test_judge_http.py 的
`EvalJudgeTests`、`StageBudgetTests` 承接。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import judge as judge_mod  # noqa: E402


class JudgeModuleTests(unittest.TestCase):
    def test_default_judge_model_is_deepseek_flash(self):
        """PR-26/27 起 judge 是 deepseek-flash（新量尺系譜）；conftest 清空旋鈕後直接讀到預設表。"""
        self.assertEqual(judge_mod.DEFAULT_JUDGE_MODEL, "deepseek-flash")

    def test_default_timeout_is_evidence_based(self):
        """60s 是失敗的那個值（CLI judge 時代實測）；預設必須明顯高於正常耗時。"""
        self.assertGreaterEqual(judge_mod.DEFAULT_JUDGE_TIMEOUT, 120)

    def test_cli_leftovers_are_gone(self):
        """CLI judge 的殘留（串流 drain、容錯 JSON 解析、自有重試迴圈）不得回來。"""
        for name in ("stream_completion", "_loads_robust", "_is_retryable", "DEFAULT_JUDGE_RETRIES"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(judge_mod, name), name)


if __name__ == "__main__":
    unittest.main()
