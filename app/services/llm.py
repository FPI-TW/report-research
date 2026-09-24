"""統一 LLM 串流客戶端：包裝 `claude` CLI 的 headless 串流（stream-json）。

對齊 scripts/tag_all_cli.py 的 CLI 子程序模式（沿用訂閱、不另計費），但改為**非同步逐段串流**，
供 /api/ask 即時回答。隔離設定避免每次呼叫被全域環境拖慢/污染：

- `--system-prompt`：**取代**預設系統提示 → 不載入 superpowers/skills/全域 CLAUDE.md。
- `--setting-sources ''`：排除使用者/專案設定（含 SessionStart hooks）。
- `cwd="/tmp"`：避開專案 CLAUDE.md（同 tag_all_cli）。
- 工具：開網搜時 `--tools WebSearch --allowedTools WebSearch`，不開時 `--tools ""`（CLI `--help`
  寫明 `""` 停用全部工具）。`--allowedTools` 只管「免核可」，不限縮可用工具；單用它時 Read、
  Bash 等內建工具仍在模型手上（headless 下讀 cwd 的檔免核可）。刻意不用 `--disallowedTools "*"`：
  本機 CLI 未記載萬用字元語意，看來是逐字比對工具名，很可能無效。旗標若失效，
  `system/init` 事件的 `tools` 會對不上預期，`check_init_tools` 記 WARNING（不中斷）。
- `--strict-mcp-config`（開不開網搜都加）：`--tools` 只管內建工具，管不到 MCP 伺服器；這個旗標
  讓 CLI 只用 `--mcp-config` 給的 MCP，而我們不帶 `--mcp-config`＝一個 MCP 都不載。
  `--setting-sources ''` 擋得住使用者／專案設定檔裡的 MCP，擋不住其他來源（`--help` 2.1.260
  在 `--restricted` 條目明寫要另加此旗標才略過 MCP）。它是布林旗標、不吃引數，放在工具旗標之前。

stream-json 事件：只取 `content_block_delta` 內 `delta.type == "text_delta"` 的文字；
thinking_delta 等一律忽略。以 `result` 事件或進程結束為終點。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator

from app.services.llm_models import TASK_ASK_ANSWER, resolve_model

logger = logging.getLogger(__name__)

# 主答模型（ASK_ANSWER_MODEL，未設時查 LLM_PROVIDER 的預設表；claude_cli 下是 claude-sonnet-5）。
# 名稱保留：answer.py 的總覽／主答、faithfulness 的預設參數、eval/run_ragas 的生成端都讀它。
# import 期解析：web/server.py 在本模組被 import 之前就先載入 repo 根 .env；批次與評測入口則先呼叫
# scripts/_llm_env.load_llm_env()（/etc/default/report-mark-llm）。
DEFAULT_MODEL = resolve_model(TASK_ASK_ANSWER)

# 串流中表示「模型開始呼叫 WebSearch」的控制標記（NUL 包夾，模型文字不可能等於它）。
# stream_completion 偵測到 WebSearch 工具起點時 yield 此值，供上層顯示「正在搜尋網路」。
SEARCH_EVENT = "\x00WEBSEARCH\x00"

# claude CLI 的 stream-json 為 NDJSON，逐行讀取。asyncio StreamReader 預設單行上限僅
# 64KB，但單一事件行（尤其結尾 result 事件含全文）於長篇回答可遠超過，會觸發
# LimitOverrunError 使串流中斷、回答無法完成。提高上限至 16MB 以容納長輸出。
_STDOUT_LINE_LIMIT = 16 * 1024 * 1024


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


def looks_like_api_error(text: str | None) -> bool:
    """判斷文字是否為 claude CLI 透傳的 API 錯誤（如 529 Overloaded）。

    headless 模式下 API 失敗時，CLI 會把 `API Error: ...` 當成回答文字輸出，
    不能拿來當答案；偵測到即觸發重試。
    """
    t = (text or "").lstrip()
    return t.startswith("API Error") or "Overloaded" in t[:80]


def extract_assistant_text(line: str) -> str | None:
    """從 `assistant` 完整訊息行取出 text 區塊文字（result 為空時的次要 fallback）。"""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    parts = [
        b.get("text")
        for b in (msg.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    text = "".join(parts)
    return text or None


def result_line_info(line: str) -> tuple[str | None, bool]:
    """解析終點 `result` 行：回 (最終答案文字, 是否為錯誤)。

    允許工具（WebSearch）時，claude CLI 2.1.x 常不吐 text_delta 分段，最終答案只在
    `{"type":"result","result":"..."}`；故串流無 text_delta 時以此 fallback 取回。
    is_error / subtype!=success / 文字像 API Error，皆視為錯誤供上層重試。
    """
    line = line.strip()
    if not line:
        return None, False
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None, False
    if not isinstance(obj, dict) or obj.get("type") != "result":
        return None, False
    text = obj.get("result")
    text = text if isinstance(text, str) and text else None
    is_err = bool(obj.get("is_error")) or obj.get("subtype") not in (None, "success")
    if looks_like_api_error(text):
        is_err = True
    return text, is_err


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


def check_init_tools(line: str, allow_web: bool) -> str | None:
    """`{"type":"system","subtype":"init","tools":[...]}` 的工具集與預期不符時回說明，否則 None。

    預期：不開網搜＝空清單；開網搜＝恰好 `["WebSearch"]`。非 init 事件、沒帶 `tools` 欄位或
    欄位不是 list 一律回 None（CLI 事件格式可能變，這裡只做防禦性觀測，不當閘門）。
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or obj.get("type") != "system" or obj.get("subtype") != "init":
        return None
    tools = obj.get("tools")
    if not isinstance(tools, list):
        return None
    expected = ["WebSearch"] if allow_web else []
    if tools == expected:
        return None
    return f"claude CLI 工具集與預期不符（allow_web={allow_web}，預期 {expected}，實際 {tools[:20]}）"


CLAUDE_BIN = "claude"


def claude_cli_path() -> str | None:
    """`claude` 在目前 PATH 上的完整路徑；找不到回 None。

    給啟動期自檢用：CLI 不在 PATH 上時每一題問答都會回 SSE error，而 `/healthz` 只探 DB
    照樣回 ok（2026-09-02 原生安裝路徑漂移即此型態）。systemd 靠 path.conf drop-in 補 PATH，
    那是部署設定；這裡是行程自己說得出「我找不到」。
    """
    return shutil.which(CLAUDE_BIN)


def _build_cmd(model: str, system: str | None, allow_web: bool) -> list[str]:
    """組 claude CLI headless 串流指令；allow_web 時只開 WebSearch，否則不開任何工具。

    `--tools`／`--allowedTools` 都是可變長度選項，會吞掉後面直到下一個 `--` 選項為止的引數；
    prompt 走 stdin 所以不受影響，但仍一律放在 argv 最後，不要在它們後面接位置引數。
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--model",
        model,
        "--setting-sources",
        "",  # 排除全域/專案設定（含 SessionStart hooks），每次呼叫乾淨且快
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        # 不載任何 MCP：沒有 --mcp-config 時只用它＝空集合（理由見模組 docstring）。
        # 布林旗標，放在可變長度的工具旗標之前，免得被當成 --tools 的值。
        "--strict-mcp-config",
    ]
    if system:
        cmd += ["--system-prompt", system.replace("\x00", "")]
    if allow_web:
        # --tools 把可用工具集縮到只剩 WebSearch；--allowedTools 讓它免核可（headless 無人核可）。
        cmd += ["--tools", "WebSearch", "--allowedTools", "WebSearch"]
    else:
        # `--help`（2.1.260）寫明 `--tools ""` 停用全部工具。list 傳參，空字串是獨立引數
        # （同 `--setting-sources ""`）。不用 `--disallowedTools "*"`：萬用字元語意未記載。
        cmd += ["--tools", ""]
    return cmd


# LLMUnavailableError.reason 的詞彙（最後一次嘗試為什麼失敗）。
UNAVAILABLE_API_ERROR = "api_error"  # API 快速回錯（529 等），已依 retries 重試
UNAVAILABLE_TIMEOUT = "timeout"      # 一個字都沒吐就逾時（不重試）
UNAVAILABLE_EMPTY = "empty"          # 進程結束卻沒有任何文字


# LLMUnavailableError.kind 的預設值：「未分類」。
KIND_OTHER = "other"


class LLMUnavailableError(RuntimeError):
    """LLM 多次重試後仍無有效回應（CLI 多為 Anthropic API 過載 529；HTTP 見 `kind`）。

    三個屬性，粒度不同、並存不互斥：

    - `reason`：CLI 時代的三類（`UNAVAILABLE_*`：服務回錯／沒吐字就逾時／全空），外部直接
      建構時為 None。既有呼叫端靠它分辨「服務回錯」與「沒吐字就逾時」（faithfulness 的
      degraded_reason），HTTP 路徑也照同一套詞彙填（逾時→timeout、空回應→empty、其餘→
      api_error），所以那些呼叫端不必知道底下是哪個 backend。
    - `kind`：細分類，詞彙同 `llm_http` 的錯誤 kind（auth／quota／config／content_filter／
      bad_request／overloaded／network／timeout／empty／other），**只有 HTTP 路徑會填**，
      由狀態碼與 finish_reason 決定、不解析文字。CLI 路徑刻意不填（維持預設 `other`＝
      未分類）：CLI 透傳的訊息格式不在我們控制之內，細分交給 `answer._llm_error_kind`
      既有的文字判斷（只分過載／其他兩類）。
    - `partial`：True＝**已經吐過字**才失敗（目前只有 HTTP 路徑的內容審查截斷會這樣拋）。
      串流型呼叫端（總覽、時效網搜、主答）據此保留已送出的文字、由 Python 附註中斷原因；
      收齊型呼叫端照舊 `except Exception` fail-open。
    """

    def __init__(
        self, *args, reason: str | None = None, kind: str = KIND_OTHER, partial: bool = False,
    ) -> None:
        super().__init__(*args)
        self.reason = reason
        self.kind = kind or KIND_OTHER
        self.partial = partial


async def _run_attempt(
    cmd: list[str], prompt: str, timeout: float, allow_web: bool | None = None,
    meta: dict | None = None,
) -> AsyncIterator[str]:
    """跑一次 claude 子程序並串流文字。

    allow_web 非 None 時，比對 `system/init` 事件回報的工具集與預期，不符記 WARNING
    （fail-open，照常串流）；None＝不比對（直接跑假子程序的測試）。
    meta 給定時，結束後寫入 `meta["timed_out"]`（本次是否撞到逾時）。

    送出值有三類：
    - 一般文字 chunk（text_delta，逐段；或無 text_delta 時於結尾補一段 fallback）。
    - SEARCH_EVENT 控制標記。
    - 結尾以 `("__error__", detail)` tuple 標示「本次無有效回應」（API 錯誤/全空），供重試。
    為與字串 chunk 區分，錯誤以 tuple 形式 yield。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd="/tmp",
        limit=_STDOUT_LINE_LIMIT,  # 避免長回答單行超過預設 64KB 觸發 LimitOverrunError
    )
    assert proc.stdin is not None and proc.stdout is not None

    streamed_any = False
    result_text: str | None = None
    result_error = False
    last_assistant: str | None = None
    timed_out = False
    init_checked = False
    try:
        async with asyncio.timeout(timeout):
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "ignore")
                text = extract_text_delta(line)
                if text:
                    streamed_any = True
                    yield text
                    continue
                if is_web_search_start(line):
                    yield SEARCH_EVENT  # 上層據此顯示「正在搜尋網路」
                    continue
                # init 是第一個事件；開始出字後就不必再逐行比對
                if allow_web is not None and not init_checked and not streamed_any:
                    mismatch = check_init_tools(line, allow_web)
                    if mismatch:
                        init_checked = True
                        logger.warning("%s；工具限縮旗標可能失效", mismatch)
                at = extract_assistant_text(line)
                if at:
                    last_assistant = at
                if is_result_line(line):
                    result_text, result_error = result_line_info(line)
                    break
    except (TimeoutError, asyncio.TimeoutError):
        timed_out = True
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
        if meta is not None:
            meta["timed_out"] = timed_out

    if streamed_any:
        return  # 已逐段送出真實文字，最佳路徑

    # 無任何 text_delta：以 result 文字優先、其次最後一則 assistant 文字作 fallback
    candidate = result_text or last_assistant
    if candidate and not result_error and not looks_like_api_error(candidate):
        yield candidate
        return
    # 失敗：標記原因供上層決定是否重試
    #   api_error = API 快速回錯（如 529 Overloaded）→ 短暫退避後重試多半會過
    #   timeout   = API 無回應拖到逾時 → 再等一輪無益，快速失敗
    if result_error or looks_like_api_error(candidate):
        reason = UNAVAILABLE_API_ERROR
    elif timed_out:
        reason = UNAVAILABLE_TIMEOUT
    else:
        reason = UNAVAILABLE_EMPTY
    yield ("__error__", candidate, reason)


async def stream_completion(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    timeout: float = 120.0,
    allow_web: bool = False,
    retries: int = 2,
    meta: dict | None = None,
) -> AsyncIterator[str]:
    """串流呼叫 claude CLI，逐段 yield 回答文字。

    prompt 經 stdin 餵入（避開 argv 單參數 128KB 上限 + NUL byte 問題）。
    allow_web 為真時開放內建 WebSearch 工具（供回答補充即時/外部資料）。
    逾時則 kill 子程序並結束串流（已 yield 的內容保留）。

    韌性處理（允許工具時 CLI 行為多變 + Anthropic API 偶發 529 過載）：
    - 串流期間有任何 text_delta → 直接逐段送出（最佳路徑）。
    - 一段 text_delta 都沒有時，依序以 result 文字 → 最後一則 assistant 文字 fallback。
    - fallback 是 API 錯誤（如 529 Overloaded）或全空 → 短暫退避後重試，最多 retries 次；
      仍失敗則拋 LLMUnavailableError（`reason` 帶最後一次的原因），由上層回友善提示。

    meta（選填、呼叫端給的空 dict）：串流正常結束後寫入 `meta["truncated"]`——成功的那次
    嘗試是否撞到逾時（已吐的字照常送出，但後面被砍掉了）。這是 CLI 路徑唯一的截斷訊號：
    逾時對已串流文字 fail-open，不拋例外。每次嘗試各自計時，前面 529 重試花掉的時間不算。
    撞到逾時但 result 事件剛好已到的邊界情況不會發生（讀到 result 就結束讀取）。
    """
    prompt = prompt.replace("\x00", "")
    cmd = _build_cmd(model, system, allow_web)

    last_detail: str | None = None
    reason = ""
    for attempt in range(retries + 1):
        failed = False
        reason = ""
        attempt_meta: dict = {}
        async for chunk in _run_attempt(cmd, prompt, timeout, allow_web, attempt_meta):
            if isinstance(chunk, tuple):  # ("__error__", detail, reason)
                failed = True
                last_detail = chunk[1]
                reason = chunk[2]
                break
            yield chunk
        if not failed:
            if meta is not None:
                meta["truncated"] = bool(attempt_meta.get("timed_out"))
            return  # 本次有有效輸出（串流或 fallback），完成
        # 只對「快速 API 錯誤（529 等）」重試；逾時=API 無回應，再等無益→快速失敗
        if reason == UNAVAILABLE_API_ERROR and attempt < retries:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        break

    raise LLMUnavailableError(last_detail or "claude 無有效回應", reason=reason or None)
