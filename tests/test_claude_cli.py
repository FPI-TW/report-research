"""`scripts/_claude_cli.py`：批次共用的 claude CLI 呼叫層。

這一組測試釘死的是**失敗原因必須互相區分**。原缺陷是每一支批次各自寫了

    return r.stdout if r.returncode == 0 else None
    except Exception: return None

於是「claude 不在 PATH」「額度耗盡」「CLI 崩潰」「真的逾時」全部塌縮成同一句
「CLI 無回應或逾時」——2026-08-08 起連續四天 100% 失敗，事後完全無法診斷。
（signal_failures.log 累積 9,273 筆全是那一句。）
"""
import errno
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import _claude_cli as cc  # noqa: E402


class BuildCliArgsTests(unittest.TestCase):
    def test_shape_and_flags(self):
        args = cc.build_cli_args("hello", "claude-sonnet-5")
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertEqual(args[2], "hello")
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5")
        # 空字串引數不可省：`--setting-sources` 後面必須真的有一個空字串
        self.assertIn("--setting-sources", args)
        self.assertEqual(args[args.index("--setting-sources") + 1], "")

    def test_disables_all_tools(self):
        """批次只要模型回文字，不開任何工具；旗標是可變長度選項，必須在 prompt 之後、argv 最後。"""
        args = cc.build_cli_args("hello", "m")
        self.assertEqual(args[-2:], ["--tools", ""])  # 空字串是獨立引數，不可省
        self.assertEqual(args[2], "hello")
        self.assertNotIn("--disallowedTools", args)  # "*" 萬用字元語意未記載，很可能無效
        self.assertNotIn("--allowedTools", args)

    def test_no_mcp_servers(self):
        """`--tools ""` 管不到 MCP：另加 `--strict-mcp-config`、不帶 `--mcp-config`＝不載任何 MCP。
        布林旗標要在 `--tools` 之前，不能被當成 `--tools` 的值。"""
        args = cc.build_cli_args("hello", "m")
        self.assertIn("--strict-mcp-config", args)
        self.assertNotIn("--mcp-config", args)
        self.assertLess(args.index("--strict-mcp-config"), args.index("--tools"))
        self.assertEqual(args[2], "hello")

    def test_nul_is_stripped(self):
        """POSIX argv 不可含 NUL，否則 subprocess 直接拋 ValueError，該檔永久失敗。"""
        self.assertEqual(cc.build_cli_args("ab\x00cd", "m")[2], "abcd")

    def test_no_output_format_json(self):
        """加了會把回應包進 CLI envelope，各家 parser 會抓到外層物件而全數解析失敗。"""
        self.assertNotIn("--output-format", cc.build_cli_args("x", "m"))


class RunClaudeTests(unittest.TestCase):
    def _raises(self, exc):
        return mock.patch.object(cc.subprocess, "run", side_effect=exc)

    def test_success_returns_stdout_and_no_error(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="OUT", stderr="")
        with mock.patch.object(cc.subprocess, "run", return_value=done):
            res = cc.run_claude("prompt", "model")
        self.assertEqual(res.text, "OUT")
        self.assertIsNone(res.error)

    def test_missing_cli_raises_instead_of_returning_none(self):
        # 環境層級失敗：每篇都會踩到，必須往上拋以中止整批，
        # 而不是靜靜地把每一篇都記成 rejected
        with self._raises(FileNotFoundError(2, "No such file or directory", "claude")):
            with self.assertRaises(cc.CliNotFoundError) as ctx:
                cc.run_claude("prompt", "model")
        msg = str(ctx.exception)
        self.assertIn("claude", msg)
        self.assertIn("PATH", msg)  # 訊息要直接指出真因

    def test_unrunnable_binary_raises_instead_of_becoming_a_per_file_error(self):
        """**2026-08-20 的實際事故。**

        claude CLI 自我更新到 2.1.237，而該版本的 native artifact 上游沒發布，
        postinstall 留下 500 bytes、無 shebang 的佔位腳本 ⇒ exec 拋
        `OSError [Errno 8] ENOEXEC`。初版只接 `FileNotFoundError`（ENOENT），
        於是這顆完全跑不起來的二進位被當成「這一篇失敗」——7 篇研報記成
        skip_untagged、整批 rc=0、殼只印「本次無新研報入庫」。

        述詞要問的是「這顆二進位在這個環境裡有沒有可能跑起來」，不是「它存不存在」。
        """
        cases = {
            errno.ENOENT: "ENOENT",
            errno.ENOEXEC: "ENOEXEC",
            errno.EACCES: "EACCES",
            errno.EPERM: "EPERM",
            errno.EISDIR: "EISDIR",
        }
        for code, name in cases.items():
            with self.subTest(errno=name):
                with self._raises(OSError(code, os.strerror(code), "claude")):
                    with self.assertRaises(cc.CliNotFoundError) as ctx:
                        cc.run_claude("prompt", "model")
                self.assertIn(name, str(ctx.exception), "訊息要說得出是哪一種")

    def test_transient_oserrors_stay_per_file_errors(self):
        """**不可寬泛接 OSError。** 資源壓力是暫時的，中止整批反而讓一次尖峰
        變成一輪完全沒跑——而它下一分鐘可能就好了。
        """
        for code in (errno.ENOMEM, errno.ENFILE, errno.EAGAIN):
            with self.subTest(errno=code):
                with self._raises(OSError(code, os.strerror(code), "claude")):
                    res = cc.run_claude("prompt", "model")
                self.assertIsNone(res.text)
                # 不比對例外類別名：Python 會把部分 errno 映射成 OSError 的子類
                # （EAGAIN → BlockingIOError），比對名稱是在測 CPython 的實作細節。
                self.assertIn("CLI 呼叫失敗", res.error)
                self.assertIn(str(code), res.error)

    def test_timeout_is_reported_as_timeout(self):
        with self._raises(subprocess.TimeoutExpired(cmd="claude", timeout=180)):
            res = cc.run_claude("prompt", "model", timeout=180)
        self.assertIsNone(res.text)
        self.assertIn("逾時", res.error)
        self.assertIn("180", res.error)

    def test_nonzero_exit_reports_code_and_stderr(self):
        fail = subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="usage: unknown flag\n"
        )
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            res = cc.run_claude("prompt", "model")
        self.assertIsNone(res.text)
        self.assertIn("3", res.error)
        self.assertIn("unknown flag", res.error)

    def test_nonzero_exit_without_stderr_still_says_something(self):
        """空 stderr 不可產出「CLI 退出碼 1：」這種尾巴空著的訊息。"""
        fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            res = cc.run_claude("p", "m")
        self.assertIn("無 stderr", res.error)

    def test_stderr_is_tail_truncated_and_single_line(self):
        """log 是給人掃讀的：不可讓一則失敗灌進數十 KB，也不可把行拆散。"""
        fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="x" * 5000 + "\nTAIL_MARKER"
        )
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            res = cc.run_claude("p", "m")
        self.assertIn("TAIL_MARKER", res.error)          # 保留的是尾巴，不是開頭
        self.assertNotIn("\n", res.error)                # 單行，不破壞 log 的一列一筆
        self.assertLess(len(res.error), cc.STDERR_TAIL_CHARS + 60)

    def test_other_exception_is_reported_with_its_type(self):
        with self._raises(OSError("Cannot allocate memory")):
            res = cc.run_claude("prompt", "model")
        self.assertIsNone(res.text)
        self.assertIn("OSError", res.error)
        self.assertNotIn("逾時", res.error)  # 不得與逾時混為一談

    def test_failure_reasons_are_mutually_distinguishable(self):
        """四種失敗不可再塌縮成同一句話（這正是原缺陷的本體）。"""
        with self._raises(subprocess.TimeoutExpired(cmd="claude", timeout=180)):
            timeout_err = cc.run_claude("p", "m").error
        with self._raises(OSError("boom")):
            other_err = cc.run_claude("p", "m").error
        fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="e")
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            exit_err = cc.run_claude("p", "m").error
        rate = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Credit balance is too low"
        )
        with mock.patch.object(cc.subprocess, "run", return_value=rate):
            rate_err = cc.run_claude("p", "m").error
        self.assertEqual(len({timeout_err, other_err, exit_err, rate_err}), 4)
        # 額度耗盡要能從訊息本身看出來，不必再去翻別的地方
        self.assertIn("Credit balance", rate_err)


if __name__ == "__main__":
    unittest.main()
