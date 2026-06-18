"""統一 LLM 串流客戶端：包裝 `claude` CLI 的 headless 串流（stream-json）。

對齊 scripts/tag_all_cli.py 的 CLI 子程序模式（沿用訂閱、不另計費），但改為**非同步逐段串流**，
供 /api/ask 即時回答。隔離設定避免每次呼叫被全域環境拖慢/污染：

- `--system-prompt`：**取代**預設系統提示 → 不載入 superpowers/skills/全域 CLAUDE.md。
- `--setting-sources ''`：排除使用者/專案設定（含 SessionStart hooks）。
- `cwd="/tmp"`：避開專案 CLAUDE.md（同 tag_all_cli）。

stream-json 事件：只取 `content_block_delta` 內 `delta.type == "text_delta"` 的文字；
thinking_delta 等一律忽略。以 `result` 事件或進程結束為終點。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

DEFAULT_MODEL = "claude-sonnet-4-6"

# 串流中表示「模型開始呼叫 WebSearch」的控制標記（NUL 包夾，模型文字不可能等於它）。
# stream_completion 偵測到 WebSearch 工具起點時 yield 此值，供上層顯示「正在搜尋網路」。
SEARCH_EVENT = "\x00WEBSEARCH\x00"


def extract_text_delta(line: str) -> str | None:
    """從一行 stream-json NDJSON 取出文字 delta；非文字事件回 None。

    只認 stream_event → content_block_delta → text_delta；thinking_delta 等忽略。
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "stream_event":
        return None
    ev = obj.get("event")
    if not isinstance(ev, dict):
        return None
    if ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta")
    if not isinstance(delta, dict):
        return None
    if delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return text if isinstance(text, str) and text else None


def is_result_line(line: str) -> bool:
    """串流終點事件：`{"type":"result",...}`。"""
    line = line.strip()
    if not line:
        return False
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and obj.get("type") == "result"


def is_web_search_start(line: str) -> bool:
    """偵測 WebSearch 工具被呼叫的起點：content_block_start 內 tool_use name=WebSearch。"""
    line = line.strip()
    if not line:
        return False
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict) or obj.get("type") != "stream_event":
        return False
    ev = obj.get("event")
    if not isinstance(ev, dict) or ev.get("type") != "content_block_start":
        return False
    cb = ev.get("content_block")
    return (
        isinstance(cb, dict)
        and cb.get("type") == "tool_use"
        and cb.get("name") == "WebSearch"
    )


def _build_cmd(model: str, system: str | None, allow_web: bool) -> list[str]:
    """組 claude CLI headless 串流指令；allow_web 時加 WebSearch 內建工具。"""
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--setting-sources",
        "",  # 排除全域/專案設定（含 SessionStart hooks），每次呼叫乾淨且快
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if allow_web:
        cmd += ["--allowedTools", "WebSearch"]
    if system:
        cmd += ["--system-prompt", system.replace("\x00", "")]
    return cmd


async def stream_completion(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    timeout: float = 120.0,
    allow_web: bool = False,
) -> AsyncIterator[str]:
    """串流呼叫 claude CLI，逐段 yield 回答文字。

    prompt 經 stdin 餵入（避開 argv 單參數 128KB 上限 + NUL byte 問題）。
    allow_web 為真時開放內建 WebSearch 工具（供回答補充即時/外部資料）。
    逾時則 kill 子程序並結束串流（已 yield 的內容保留）。
    """
    prompt = prompt.replace("\x00", "")
    cmd = _build_cmd(model, system, allow_web)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd="/tmp",
    )
    assert proc.stdin is not None and proc.stdout is not None

    async def _drive() -> AsyncIterator[str]:
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "ignore")
            text = extract_text_delta(line)
            if text:
                yield text
            elif is_web_search_start(line):
                yield SEARCH_EVENT  # 上層據此顯示「正在搜尋網路」
            elif is_result_line(line):
                break

    try:
        async with asyncio.timeout(timeout):
            async for chunk in _drive():
                yield chunk
    except (TimeoutError, asyncio.TimeoutError):
        pass
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
