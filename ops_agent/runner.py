"""子行程執行：固定 argv（不經 shell）、逾時、輸出上限、最小環境。

測試一律換成假的 runner（同樣的 `run` 簽章），**不得真的呼叫 systemctl／journalctl／docker**。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RunResult:
    returncode: int | None   # None＝逾時被砍
    stdout: bytes
    stderr: bytes
    truncated: bool = False  # stdout 超過 max_bytes：keep_tail 時只留最後 max_bytes，否則只留前 max_bytes
    timed_out: bool = False


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """子行程只拿到這幾個變數：代理自己的環境（含 systemd 給的任何東西）不往下傳。"""
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SYSTEMD_COLORS": "0",
        "SYSTEMD_PAGER": "",
        "SYSTEMD_LESS": "",
    }
    if os.environ.get("TZ"):
        env["TZ"] = os.environ["TZ"]
    env.update(extra or {})
    return env


class SubprocessRunner:
    async def run(self, argv: list[str], *, timeout: float, max_bytes: int,
                  merge_stderr: bool = False, keep_tail: bool = False,
                  env: dict[str, str] | None = None) -> RunResult:
        """keep_tail＝讀到結束、只留最後 max_bytes（日誌要的是最新的那端）；否則超過就砍掉子行程。"""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
            env=child_env(env),
            close_fds=True,
        )
        out = bytearray()
        truncated = False

        async def _read_stdout():
            nonlocal truncated
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    return
                if keep_tail:
                    out.extend(chunk)
                    if len(out) > max_bytes:
                        del out[:len(out) - max_bytes]
                        truncated = True
                    continue
                room = max_bytes - len(out)
                if room > 0:
                    out.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
                    # 已經夠了：不再讀也不等它寫完，直接砍掉（stderr 因此關閉，下面的 gather 不會卡住）。
                    _kill(proc)
                    return

        async def _read_stderr() -> bytes:
            if proc.stderr is None:
                return b""
            data = await proc.stderr.read(16 * 1024)
            # 剩下的丟掉，但要讀乾淨，否則子行程可能卡在寫 stderr
            while await proc.stderr.read(65536):
                pass
            return data

        try:
            async with asyncio.timeout(timeout):
                _, err = await asyncio.gather(_read_stdout(), _read_stderr())
                await proc.wait()
        except TimeoutError:
            _kill(proc)
            await proc.wait()
            return RunResult(None, bytes(out), b"", truncated=truncated, timed_out=True)
        return RunResult(proc.returncode, bytes(out), err, truncated=truncated)


def _kill(proc) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass
