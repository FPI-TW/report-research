# tests/test_claude_lock.py
"""claude CLI 跨進程鎖（scripts/_claude_lock.py）的行為與接線守門。

為什麼這一支要存在：規約「批次不可同時跑」以前只寫在 Makefile 註解、docstring 與
CLAUDE.md 裡，而 `report-mark-sync.timer` 每 3 小時自動跑「增量匯入 → 摘要 → 摘錄」，
註解攔不住排程。這裡把三件事機械化：

1. 鎖真的互斥（同進程異 fd 與跨進程都要擋），且**行程被 SIGKILL 後自動釋放**——
   那正是選 flock 而非 PID 檔的唯一理由，沒測到就等於沒選。
2. 所有批次入口都取了鎖（清單見 LOCKED_SCRIPTS）。
3. `app/services/llm.py` **沒有**取鎖。它是 web 線上路徑的 spawn 點，納入鎖等於讓
   一輪 tag_all_cli（數小時）把 /api/ask 鎖死——這條反向斷言比正向的五條更重要。

全程用 tempfile 當鎖檔，絕不碰真實的 data/.claude_cli.lock（那是生產路徑上的檔案，
測試對它上鎖會擋掉正在跑的排程）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts._claude_lock import (  # noqa: E402
    DISABLE_ENV,
    EXIT_LOCK_BUSY,
    ClaudeCliBusyError,
    claude_cli_lock,
    claude_cli_lock_or_exit,
)

# 取鎖的批次入口。少一支就是留一個併發缺口，所以清單寫死在測試裡而非掃目錄。
LOCKED_SCRIPTS = [
    "tag_all_cli.py",
    "generate_summaries.py",
    "generate_titles.py",
    "extract_takeaways.py",
    "extract_signals.py",
    "sync_new_reports.py",
    # 每日簡報：取鎖的位置與其他支不同（在 generate() 內、只包住那一次 CLI 呼叫，
    # 不在 main 進入點）——排程每 3 小時叫它一次而真正呼叫 LLM 的只有一天一次，
    # 在入口取鎖會讓其餘七次 no-op 撞鎖 rc=75、把 unit_failures 灌成雜訊。
    "generate_brief.py",
]


class _TempLock(unittest.TestCase):
    """每個測試用自己的暫存鎖檔，彼此不干擾、也不碰生產鎖檔。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / ".claude_cli.lock"
        self.addCleanup(self._tmp.cleanup)
        # 逃生口是進程層級的環境變數；上一個測試設了沒清會讓後面全部假綠。
        self._orig_disable = os.environ.pop(DISABLE_ENV, None)
        self.addCleanup(self._restore_disable)

    def _restore_disable(self) -> None:
        os.environ.pop(DISABLE_ENV, None)
        if self._orig_disable is not None:
            os.environ[DISABLE_ENV] = self._orig_disable


class MutualExclusionTests(_TempLock):
    def test_second_acquire_fails_while_held(self):
        with claude_cli_lock("first", self.lock_path):
            with self.assertRaises(ClaudeCliBusyError):
                with claude_cli_lock("second", self.lock_path):
                    self.fail("第二個取鎖不該成功——鎖沒有互斥就等於沒有鎖")

    def test_busy_error_names_the_holder(self):
        """訊息要能直接回答「是誰佔著」，否則使用者只能盲等或亂刪鎖檔。"""
        with claude_cli_lock("extract_signals", self.lock_path):
            with self.assertRaises(ClaudeCliBusyError) as ctx:
                with claude_cli_lock("extract_takeaways", self.lock_path):
                    pass
        msg = str(ctx.exception)
        self.assertIn("extract_signals", msg)
        self.assertIn(str(os.getpid()), msg)
        self.assertIsNotNone(ctx.exception.holder)
        self.assertEqual(ctx.exception.holder["script"], "extract_signals")
        self.assertEqual(ctx.exception.holder["pid"], os.getpid())
        self.assertIn("started_at", ctx.exception.holder)

    def test_reacquire_after_release(self):
        with claude_cli_lock("first", self.lock_path):
            pass
        with claude_cli_lock("second", self.lock_path):
            pass  # 沒拋例外就是通過

    def test_reacquire_after_body_raises(self):
        """批次自己炸掉也必須放鎖，否則一次失敗就讓後續每一輪排程都取不到。"""

        class Boom(RuntimeError):
            pass

        with self.assertRaises(Boom):
            with claude_cli_lock("first", self.lock_path):
                raise Boom()
        with claude_cli_lock("second", self.lock_path):
            pass

    def test_stale_payload_is_not_reported_as_the_holder(self):
        """強殺的持有者會留下 payload；此時真正持鎖的是別人，不能指著死人說是他。

        情境是實測出來的：SIGTERM/SIGKILL 不跑 finally，鎖檔內容就留在原地。kernel
        已放掉 flock，所以下一支批次搶得到；在它寫完自己的 payload 之前，第三支若來
        撞鎖就會讀到那筆殘留。
        """
        self.lock_path.write_text(
            '{"pid": 2147483646, "script": "已死的批次", "started_at": "2026-01-01T00:00:00+08:00"}',
            encoding="utf-8",
        )
        with claude_cli_lock("real_holder", self.lock_path):
            # 模擬「已搶到鎖但 payload 還沒寫」：手動塞回殘留內容
            self.lock_path.write_text(
                '{"pid": 2147483646, "script": "已死的批次", "started_at": "2026-01-01T00:00:00+08:00"}',
                encoding="utf-8",
            )
            with self.assertRaises(ClaudeCliBusyError) as ctx:
                with claude_cli_lock("latecomer", self.lock_path):
                    pass
        msg = str(ctx.exception)
        self.assertIn("已結束", msg, f"殘留 payload 應標示為已結束，實際訊息：{msg}")

    def test_holder_payload_cleared_on_release(self):
        """釋放時清掉 payload：留著只會讓下一個取不到鎖的人看到早就結束的持有者。"""
        with claude_cli_lock("first", self.lock_path):
            self.assertIn("first", self.lock_path.read_text(encoding="utf-8"))
        self.assertEqual(self.lock_path.read_text(encoding="utf-8").strip(), "")


class ProcessDeathTests(_TempLock):
    """flock 之於 PID 檔的唯一賣點：行程怎麼死鎖都會放掉。"""

    _CHILD = textwrap.dedent(
        """
        import sys, time
        sys.path.insert(0, sys.argv[1])
        from scripts._claude_lock import claude_cli_lock
        with claude_cli_lock("child_batch", sys.argv[2]):
            print("HELD", flush=True)
            time.sleep(120)
        """
    )

    def _spawn_holder(self) -> subprocess.Popen:
        """起一個持鎖的子行程，等它回報 HELD 才返回。

        子行程的 stderr 導到檔案而非 PIPE：持鎖期間它會 sleep 120 秒，任何對 PIPE 的
        `read()` 都會一路等到它結束——包含寫在 assert 訊息 f-string 裡那種「只想在失敗時
        用」的讀取（f-string 是即時求值，成功路徑也會踩到）。導檔就沒有這個地雷。
        """
        err_path = Path(self._tmp.name) / "child.stderr"
        err_file = err_path.open("w", encoding="utf-8")
        self.addCleanup(err_file.close)
        proc = subprocess.Popen(
            [sys.executable, "-c", self._CHILD, str(REPO_ROOT), str(self.lock_path)],
            stdout=subprocess.PIPE,
            stderr=err_file,
            text=True,
        )
        self.addCleanup(self._reap, proc)
        line = proc.stdout.readline().strip()
        if line != "HELD":
            err_file.flush()
            self.fail(f"子行程沒取到鎖（rc={proc.poll()}）：{err_path.read_text(encoding='utf-8')}")
        return proc

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)

    def test_cross_process_exclusion(self):
        self._spawn_holder()
        with self.assertRaises(ClaudeCliBusyError) as ctx:
            with claude_cli_lock("parent_batch", self.lock_path):
                pass
        self.assertIn("child_batch", str(ctx.exception))

    def test_lock_released_when_holder_is_sigkilled(self):
        """SIGKILL 沒有 trap、沒有 finally——PID 檔模式在這裡會留下永久陳舊鎖。"""
        proc = self._spawn_holder()
        with self.assertRaises(ClaudeCliBusyError):
            with claude_cli_lock("parent_batch", self.lock_path):
                pass

        proc.kill()
        proc.wait(timeout=10)

        # 鎖檔本身還在、payload 還寫著那個已死的 pid，但 kernel 早已放掉 flock。
        self.assertTrue(self.lock_path.exists())
        deadline = time.monotonic() + 10
        while True:
            try:
                with claude_cli_lock("parent_batch", self.lock_path):
                    return  # 取到了＝flock 語意成立
            except ClaudeCliBusyError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)


class EscapeHatchTests(_TempLock):
    def test_disable_env_bypasses_the_lock(self):
        os.environ[DISABLE_ENV] = "1"
        with claude_cli_lock("first", self.lock_path):
            with claude_cli_lock("second", self.lock_path):
                pass  # 逃生口生效＝兩層都進得去
        self.assertFalse(self.lock_path.exists(), "繞過時不該建立鎖檔")

    def test_disable_env_warns_on_stderr(self):
        """靜默繞過等於把安全網拆掉還不留痕跡——警告是這個逃生口能存在的前提。"""
        import contextlib
        import io

        os.environ[DISABLE_ENV] = "true"
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with claude_cli_lock("noisy", self.lock_path):
                pass
        err = buf.getvalue()
        self.assertIn(DISABLE_ENV, err)
        self.assertIn("noisy", err)

    def test_unset_and_falsy_values_do_not_disable(self):
        """`CLAUDE_LOCK_DISABLE=0` 要照鎖——否則「設了但設成 0」會變成靜默關閉。"""
        for value in ("", "0", "false", "no"):
            with self.subTest(value=value):
                os.environ[DISABLE_ENV] = value
                with claude_cli_lock("first", self.lock_path):
                    with self.assertRaises(ClaudeCliBusyError):
                        with claude_cli_lock("second", self.lock_path):
                            pass


class ExitWrapperTests(_TempLock):
    def test_busy_exits_with_tempfail_code(self):
        with claude_cli_lock("holder", self.lock_path):
            with self.assertRaises(SystemExit) as ctx:
                with claude_cli_lock_or_exit("latecomer", self.lock_path):
                    self.fail("取不到鎖時不該執行本體")
        self.assertEqual(ctx.exception.code, EXIT_LOCK_BUSY)
        self.assertNotEqual(EXIT_LOCK_BUSY, 0, "必須是非零碼，否則排程會當成成功")

    def test_wrapper_releases_lock(self):
        with claude_cli_lock_or_exit("first", self.lock_path):
            pass
        with claude_cli_lock("second", self.lock_path):
            pass


def _uses_lock(source: str) -> bool:
    """AST 判定：模組是否 import 了 _claude_lock，且真的把它用在 `with` 上。

    純字串比對會被註解與 docstring 騙過（本 repo 的腳本註解密度很高，提到鎖但沒取鎖
    完全可能發生），所以走 AST。
    """
    tree = ast.parse(source)
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.endswith("_claude_lock")
        for node in ast.walk(tree)
    )
    used_in_with = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call):
                func = call.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name.startswith("claude_cli_lock"):
                    used_in_with = True
    return imported and used_in_with


class WiringTests(unittest.TestCase):
    def test_all_batch_entrypoints_take_the_lock(self):
        missing = [
            name
            for name in LOCKED_SCRIPTS
            if not _uses_lock((REPO_ROOT / "scripts" / name).read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            missing,
            [],
            "這些批次會 spawn claude CLI 卻沒取鎖，留下併發缺口：" + ", ".join(missing),
        )

    def test_llm_service_never_takes_the_lock(self):
        """反向守門：llm.py 是 /api/ask 的 spawn 點。

        把它納入這把鎖，一輪 tag_all_cli（數小時）就會把線上問答整個鎖死——
        從「批次慢一點」變成「服務中斷數小時」。這條斷言比五條正向的更重要，
        因為誤加的人會覺得自己在把防護做得更完整。
        """
        # llm_http.py 是 web 與批次共用的 HTTP 客戶端：鎖只能在批次腳本的 main 取，
        # 放進客戶端就等於讓 /api/ask 也去搶它。
        for name in ("llm.py", "llm_http.py", "llm_models.py", "llm_failures.py"):
            with self.subTest(module=name):
                src = (REPO_ROOT / "app" / "services" / name).read_text(encoding="utf-8")
                self.assertNotIn("_claude_lock", src)
                self.assertNotIn("claude_cli_lock", src)

    def test_batch_call_layer_never_takes_the_lock(self):
        """`scripts/_claude_cli.py`（run_claude、HTTP 分派、斷路器）是在鎖**裡面**被呼叫的：批次 main
        已持有這把 flock，呼叫層再取一次會在同一行程裡以另一個 fd 等自己——永久卡死。"""
        src = (REPO_ROOT / "scripts" / "_claude_cli.py").read_text(encoding="utf-8")
        self.assertNotIn("claude_cli_lock", src)
        # 只看 import（docstring 會提到 tests/test_claude_lock.py 這個檔名）
        imported = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        self.assertFalse({m for m in imported if m and "_claude_lock" in m}, imported)

    def test_no_web_module_takes_the_lock(self):
        """同理推廣到整個線上路徑：web/ 底下任何檔案都不該取這把鎖。"""
        offenders = [
            str(p.relative_to(REPO_ROOT))
            for p in (REPO_ROOT / "web").rglob("*.py")
            if "claude_cli_lock" in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], "線上路徑取批次鎖會被長批次鎖死：" + ", ".join(offenders))


class SyncShellVisibilityTests(unittest.TestCase):
    """排程殼的 best-effort 分支必須把失敗寫進 data/unit_failures.log。

    背景：摘要與摘錄兩段刻意不擋下 sync（匯入已完成），所以 unit 不會變紅；原本
    `|| log "...已略過"` 只寫進當日 sync log，而那個檔沒有任何程式消費端——
    2026-07-28 連續 10 輪失敗就是這樣 24 小時無人察覺。加了鎖之後撞車會更常見，
    這條路徑不可見化就等於把失敗藏得更深。
    """

    def setUp(self) -> None:
        self.src = (REPO_ROOT / "scripts" / "sync_new_reports.sh").read_text(
            encoding="utf-8"
        )

    def test_records_failures_to_unit_failures_log(self):
        """每個 claude 階段都要有 record_unit_failure 呼叫點。

        只斷言「檔案裡出現 unit_failures.log」是不夠的——函式定義留著、呼叫點被拿掉，
        那種斷言照樣全綠（反轉實驗 R7 實測如此）。要釘的是呼叫點。
        """
        self.assertIn("unit_failures.log", self.src)
        for stage in (
            "sync_new_reports(import)",
            "generate_summaries",
            "generate_titles",
            "extract_takeaways",
            "extract_signals",
        ):
            with self.subTest(stage=stage):
                self.assertIn(f'record_unit_failure "{stage}"', self.src)

    def test_best_effort_stages_capture_return_code(self):
        """`|| log ...` 會把 rc 吃掉；要能分辨 rc=75（被鎖擋下）就必須先接住它。"""
        for var in ("SUMMARY_RC", "TITLE_RC", "TAKEAWAY_RC", "SIGNAL_RC"):
            with self.subTest(var=var):
                self.assertIn(f"|| {var}=$?", self.src)

    def test_signal_stage_is_bounded_per_round(self):
        """訊號擷取**必須帶 --limit**：這條是防「排程反過來弄停主資料流」的唯一保險。

        待擷取積壓 5047 份 × 約 100-135s，不設上限就是連續佔住 claude 鎖八十小時以上；
        期間每輪 sync 的匯入都撞鎖 rc=75，而匯入撞鎖會讓那批研報從 delta 消失
        （rsync --size-only 下輪不再列出），得靠 --all-local 手動補。
        """
        self.assertIn("SIGNAL_LIMIT=${SYNC_SIGNAL_LIMIT:-", self.src)
        self.assertIn('--limit "$SIGNAL_LIMIT"', self.src)

    def test_signal_stage_runs_regardless_of_new_imports(self):
        """訊號段刻意在 `$HASHES` 判斷之外——它排的是全語料積壓，不是本輪新檔。

        綁進 if 區塊的話，沒有新研報進來的日子它完全不動，而雷達正是這樣從
        2026-07-16 起靜止兩週。以「出現在 else 分支之後」釘住位置。
        """
        else_branch = self.src.index("本次無新研報入庫")
        self.assertGreater(self.src.index("scripts/extract_signals.py"), else_branch)

    def test_lock_busy_code_matches_python(self):
        """殼層寫死的 75 與 Python 的 EXIT_LOCK_BUSY 漂移了，分支就永遠不成立。"""
        self.assertIn(f"LOCK_BUSY_RC={EXIT_LOCK_BUSY}", self.src)


if __name__ == "__main__":
    unittest.main()
