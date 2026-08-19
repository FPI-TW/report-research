# tests/test_oneshot_verification.py
"""`scripts/verify_oneshot_ran.sh`——證明一個 oneshot「本輪真的執行過」。

存在理由是 2026-08-19 部署 P1 時的一次假通過：
`systemctl start report-mark-freshness.service` 的終端輸出看起來完全成功
（`Result=success`、`ExecMainStatus=0`，還印出一份 freshness 報告），但 journal 裡
該 unit 當日只有 9 行、全屬 08:31 那次自然觸發，`ExecMainStartTimestamp` 也是 08:31。
**unit 根本沒有執行。**

兩件事疊起來造成的：`Result=success` 與 `ExecMainStatus=0` 既是**上一次**執行留下的值、
也是**從未執行過**的預設值，兩者無法區分；而 oneshot 在無引用時會被 systemd 回收
（`CollectMode=inactive`），回收後屬性一律為空。

所以判準只能是「相對於事前基線的前進」。這裡用假 `systemctl`／`journalctl` 驗那個判準
本身——不需要 root、不需要真的啟動任何東西。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_oneshot_ran.sh"


class _Fake:
    """受控的 systemctl／journalctl。"""

    def __init__(self, start_mono=0, exit_mono=0, journal_lines=0):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.set(start_mono, exit_mono, journal_lines)

    def set(self, start_mono, exit_mono, journal_lines):
        sc = self.bin / "systemctl"
        sc.write_text(
            "#!/usr/bin/env bash\n"
            'for a in "$@"; do case "$a" in\n'
            f'  ExecMainStartTimestampMonotonic) printf \'%s\\n\' "{start_mono}"; exit 0 ;;\n'
            f'  ExecMainExitTimestampMonotonic) printf \'%s\\n\' "{exit_mono}"; exit 0 ;;\n'
            "esac; done\nexit 0\n",
            encoding="utf-8",
        )
        sc.chmod(0o755)
        jc = self.bin / "journalctl"
        jc.write_text(
            "#!/usr/bin/env bash\n"
            f'for i in $(seq 1 {journal_lines}); do echo "line $i"; done\n'
            "exit 0\n",
            encoding="utf-8",
        )
        jc.chmod(0o755)

    def run(self, *args):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        return subprocess.run(
            ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60
        )

    def close(self):
        self.tmp.cleanup()


class OneshotVerificationTests(unittest.TestCase):
    def setUp(self):
        self.f = _Fake(start_mono=1000, exit_mono=1100, journal_lines=10)
        self.addCleanup(self.f.close)

    def _baseline(self):
        r = self.f.run("baseline", "u.service")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_baseline_token_has_four_fields(self):
        """**exit 必須與 exit 比。** 初版 token 只有三段（少了 exit_mono），於是用
        現在的 exit 去比基線的 start——而 exit 本來就晚於 start，那個佐證欄位恆為 yes。
        一個永遠成立的證據等於沒有證據。
        """
        parts = self._baseline().split("|")
        self.assertEqual(len(parts), 4, f"token 應為 epoch|start|exit|lines，實得 {parts}")
        self.assertEqual(parts[1], "1000")
        self.assertEqual(parts[2], "1100")
        self.assertEqual(parts[3], "10")

    def test_no_execution_is_not_proven(self):
        base = self._baseline()
        r = self.f.run("verify", "u.service", base)
        self.assertEqual(r.returncode, 1)
        self.assertIn("EXECUTION_NOT_PROVEN", r.stderr)
        self.assertIn("start_advanced=no", r.stdout)
        self.assertIn("exit_advanced=no", r.stdout, "exit 佐證不得恆為 yes")

    def test_real_execution_is_proven(self):
        base = self._baseline()
        self.f.set(start_mono=2000, exit_mono=2100, journal_lines=15)
        r = self.f.run("verify", "u.service", base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("EXECUTION_PROVEN", r.stdout)
        self.assertIn("start_advanced=yes", r.stdout)
        self.assertIn("journal_grew=yes", r.stdout)

    def test_gc_cleared_properties_are_not_proof(self):
        """**這正是 P1 那次的形狀。** 屬性被回收成 0，不能算「執行過」。"""
        base = self._baseline()
        self.f.set(start_mono=0, exit_mono=0, journal_lines=15)
        r = self.f.run("verify", "u.service", base)
        self.assertEqual(r.returncode, 1)
        self.assertIn("EXECUTION_NOT_PROVEN", r.stderr)

    def test_journal_growth_alone_is_not_enough(self):
        """journal 可能因為別的原因長（例如 timer 的訊息）——啟動時間戳是必要條件。"""
        base = self._baseline()
        self.f.set(start_mono=1000, exit_mono=1100, journal_lines=99)
        r = self.f.run("verify", "u.service", base)
        self.assertEqual(r.returncode, 1)

    def test_start_advance_with_exit_advance_is_enough_without_journal(self):
        """journal 可能被輪替掉，此時 exit 前進仍足以佐證。"""
        base = self._baseline()
        self.f.set(start_mono=2000, exit_mono=2100, journal_lines=10)
        r = self.f.run("verify", "u.service", base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_malformed_token_does_not_crash(self):
        for tok in ("", "garbage", "a|b|c|d", "1|||", "1|x|y|z"):
            with self.subTest(token=tok):
                r = self.f.run("verify", "u.service", tok)
                self.assertIn(r.returncode, (1, 2), r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_usage_errors_use_a_distinct_exit_code(self):
        self.assertEqual(self.f.run().returncode, 2)
        self.assertEqual(self.f.run("baseline").returncode, 2)
        self.assertEqual(self.f.run("verify", "u.service").returncode, 2)
        self.assertEqual(self.f.run("bogus", "u.service").returncode, 2)

    def test_script_is_read_only(self):
        body = SCRIPT.read_text(encoding="utf-8")
        for verb in ("systemctl start", "systemctl restart", "systemctl stop",
                     "systemctl enable", "systemctl disable", "rm ", "mv "):
            with self.subTest(verb=verb):
                code = "\n".join(
                    ln for ln in body.splitlines() if not ln.strip().startswith("#")
                )
                self.assertNotIn(verb, code, f"這支是唯讀驗證器，不得含 {verb}")

    def test_documented_in_runbook(self):
        doc = (REPO_ROOT / "docs" / "production_resilience.md").read_text(encoding="utf-8")
        self.assertIn("verify_oneshot_ran.sh", doc)
        self.assertIn("EXECUTION_NOT_PROVEN", doc)


if __name__ == "__main__":
    unittest.main()
