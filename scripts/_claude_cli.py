"""批次共用的 `claude -p` 呼叫層：**失敗原因必須說得出口**。

存在的理由是一次實際的四天停擺（2026-08-08 → 08-12）：所有 spawn `claude` 的批次
100% 失敗，而每一支的 `call_cli` 都長這樣——

    return r.stdout if r.returncode == 0 else None
    except Exception: return None          # ← stderr 從未被讀取

於是「claude 不在 PATH」「額度耗盡」「CLI 崩潰」「真的逾時」全部塌縮成同一句
「CLI 無回應或逾時」（`data/signal_failures.log` 累積 9,273 筆全是這句），事後完全
無法診斷該修 PATH、該調 timeout、還是該去看帳號。更糟的是連鎖效應：標註失敗 ⇒
`skip_untagged` ⇒ 研報不入庫，而排程 log 只印一行「本次無新研報入庫」，與「NAS 真的
沒有新檔」在畫面上一模一樣。

設計取自 `extract_takeaways.py`（本 repo 最早修好這件事的地方），這裡把它抽成單一
來源，避免下一支腳本再抄到錯的那份。

**兩類失敗刻意分流**：
  - `CliNotFoundError` **往上拋**。那是環境壞了、每一篇都會踩到，重試與續跑都沒有
    意義——「跑完 549 次註定失敗的呼叫、印 ok=0 rejected=549、然後 exit 0」是最糟的
    結局。呼叫端應在 main 接住它、印訊息、以非零碼收場。
      **判定依 errno 而不是例外型別。** 初版只接 `FileNotFoundError`（ENOENT），於是
      2026-08-20 那次「檔案在、但不是可執行檔」完全漏接：claude CLI 自我更新到缺
      native artifact 的版本，`bin/claude.exe` 成了 500 bytes、無 shebang 的佔位腳本，
      exec 拋的是 `OSError [Errno 8] ENOEXEC`——不是 `FileNotFoundError`，於是落到
      下面「回具體訊息」那條路徑、被當成單篇失敗。後果是 7 篇研報記成 skip_untagged
      而整批 rc=0。**概念對、述詞太窄**：要問的是「這顆二進位在這個環境裡有沒有可能
      跑起來」，不是「它存不存在」。
  - **claude CLI 認證失效**（OAuth 過期、金鑰無效、要重新 /login）同樣往上拋（`LlmEnvironmentError`）。
    2026-09-23 起 OAuth 過期，`claude -p` 一律「退出碼 1、stderr 空、訊息在 stdout」，於是每一篇都被
    記成「CLI 退出碼 1：（無 stderr）」的單篇失敗、腳本層再重試兩次、整批 rc=0——四天停擺的同一型態，
    而且環境檔沒生效（該切 DeepSeek 卻還在用 Claude）時看起來一模一樣。辨識樣式與線上共用
    （`llm_models.looks_like_cli_auth_error`），stdout 與 stderr 都看。
  - 其餘（逾時／非零退出／OSError…）回具體訊息，讓各自的 *_failures.log 說得出真因。

## DeepSeek 分派（遷移 PR-12）

`run_claude` 依模型名分派：DeepSeek 白名單（`llm_models.is_http_model`）走
`app.services.llm_http.complete_chat`，其餘照舊 spawn claude CLI（CLI 路徑的程式碼不動，
搬進 `_run_cli`）。名稱沿用：呼叫端、測試 patch 點與 `tests/test_claude_lock.py` 的
LOCKED_SCRIPTS 都認這個名字，改名的連動留給 PR-M 決定。

HTTP 路徑的規則（第二版計畫 §4.3、§4.6）：
  - 只送一則 user 訊息、prompt 原樣不動（A/B 只有一個變因）；thinking 兩個開關都關由
    `llm_http.build_body` 負責；`max_tokens` 由每個呼叫點帶（值見第二版 §8，
    tests/test_claude_cli.py 逐點釘住），沒帶是程式錯誤、直接拋 `ValueError`。
  - `timeout` 沿用各批次現行值，在 HTTP 路徑是**涵蓋傳輸層重試的總期限**（`complete_chat`
    以 `time.monotonic()` 逐 chunk 檢查；排隊時的 keep-alive 會一直重置 httpx 的 read 逾時）。
    已吐字後才到期歸 `truncated`（不計入斷路器、1 次就跳過），沒吐字就到期才是 `timeout`。
  - 失敗回 `CliResult(None, "API[<kind>] <固定措辭>：<細節>")`（`llm_http.error_string`，單行、
    不含 TAB）。前綴 `API[` 是契約：`is_retryable` 靠它分辨「傳輸層已重試過」。
  - **帳號層級（401 auth／402 quota／config：404 或模型不存在）拋 `LlmEnvironmentError`**
    （`CliNotFoundError` 的子類），沿用各批次 main「接 `CliNotFoundError` → 整批 rc=2」的接法。
    這類錯誤每一篇都會踩到，記成 N 筆單篇失敗後 exit 0 正是四天停擺的型態；它們也**不記**
    `research.llm_task_failure`（那不是研報的問題），更**絕不改走 Claude**——402 的處置是儲值，
    換成 Claude 等於繞過預算（docs/production_resilience.md「整批中止後的重放」）。

## 重試分層（第二版計畫 §4.7）

HTTP 路徑的暫時性錯誤（429／5xx／網路）已在傳輸層依 `Retry-After` 退避重試過；截斷、審查、
空回應、400 則是決定性的，重打同一個 prompt 只是再付一次錢。所以各批次的腳本層重試迴圈要加
`if res.text is None and not is_retryable(res): break`——`API[` 開頭的失敗一律不在腳本層重試。
**例外**是「回應成功但解析失敗」（unparseable）：那時 `res.text` 有值，腳本層照舊最多 3 次。

不變量：**每篇每輪最多 3 個會產生輸出（已計費）的請求；截斷與審查只會 1 個。** 它靠兩層一起成立：
傳輸層只重試**還沒吐字**的失敗（`complete_chat` 在 `outcome.streamed` 時不重試：串流中途的錯誤物件、
`insufficient_system_resource`、中途斷線都直接以該 kind 回傳，這裡當單篇失敗、`is_retryable` 為
False、不進 unparseable 重試），腳本層只重試解析失敗。審查中3 之前傳輸層不看 `streamed`，實測
「每次都吐了字再斷」的一篇一輪打出 9 個已計費請求。tests/test_batch_http_dispatch.py 的
`BilledRequestsPerFileTests` 釘住這個上限。CLI 的失敗（`CLI 逾時`、`CLI 退出碼 …`）語意不變，照舊重試。

`failure_kind(res)` 把 HTTP 的內容型失敗對應到 `llm_failures` 的 reason（content_filter、
truncated、empty、bad_request），各批次記跳過名單時用它；環境型（timeout、overloaded、network）
回 None——那不是研報的問題。

## 斷路器（只擋 HTTP backend，審查 L9）

DeepSeek 整體變慢或過載時，每篇都要等到總期限才失敗，一段批次可以拖上數小時、每篇還記一筆
「單篇失敗」。行程範圍的斷路器看**最近 `BREAKER_WINDOW` 次 HTTP 呼叫**，其中逾時／過載／網路
（`BREAKER_KINDS`）達 `BREAKER_TRIP` 次就拋 `LlmEnvironmentError`（整批 rc=2），並寫
`data/.llm_breaker`（帶 sync 輪次 id `round=`，審查中4）；同一輪 sync 其餘會用到 HTTP model
的段（手動執行：30 分鐘內）在 `require_llm_key` 就以 rc=2 拒跑，下一輪不受影響
（`scripts/_llm_env.py`）。CLI 呼叫不進窗、也不受標記影響：遷移期間還在用 Claude 的段不該因為
DeepSeek 出事而停。有執行緒鎖（批次以 `asyncio.to_thread`／執行緒池並行呼叫）。

## 400 升級（審查 H2）

一般的 400（`bad_request`）多半是單篇輸入造成的（超長、怪字元），算單篇失敗、記跳過名單
（連續 3 輪才跳過）。**只有**同一行程裡 ≥2 個不同 `file_hash` 收到**正規化後相同**的 400 訊息，
才判定是請求本身或設定壞了（每一篇都會踩到），升級成 `BadRequestEscalation`（config 型、整批
rc=2）。刻意沒有「本輪第一個請求就 400 → 升級」：那篇研報若排在最前面，每一輪都會中止整批、
而中止不記跳過名單，它永遠不會被跳過——匯入段就等於全站停止入庫。升級前，觸發的那幾篇要先記入
跳過名單（各批次 main 呼叫 `record_escalation`），而且**直接記到 `SKIP_AFTER_ROUNDS`**
（`FailureRecorder.record(..., escalated=True)`）：只記一筆的話要連續 3 輪整段 rc=2 才跳得過去。
匯入段另把它們寫進保留檔（`scripts/sync_new_reports.py`）。修好請求或設定之後，這幾篇要加
`--retry-blocked` 才會再打。身分由 `run_claude(meta={"file_hash": …})` 傳入，沒有 file_hash 的呼叫
（簡報）不參與。

比對鍵是**正規化**的訊息（`_escalation_key`）：小寫、長 hex／request id 換成 `<id>`、數字換成 `#`。
逐字比對的話，DeepSeek 反序列化錯誤帶的 `at line 1 column N`（N 隨 prompt 長度變）會讓全面性的
400 永遠湊不到兩篇相同，連續 3 輪後把整個工作集打進跳過名單。反過來，**本質上是單篇輸入造成的
訊息**（上下文長度、輸入過長：`_INPUT_SPECIFIC`）不參與升級——兩篇超長研報正規化後是同一句，
升級會讓整批為了兩篇研報中止。

## 用量記錄（第二版計畫 §4.8）

每次呼叫（HTTP 與 CLI 都寫）追加一行 JSON 到 `data/llm_usage.jsonl`（ROOT 錨點；`LLM_USAGE_LOG`
只給測試用）：`ts, task, file_hash, report_id, backend, model_req, model_resp, prompt_sha256,
tokens{hit,miss,completion,reasoning}, finish_reason, kind, attempts, ttft_ms, total_ms`。
摘要、標題、標籤不在 DB 記產出模型，靠這裡的 `file_hash` 回溯；費用真值看餘額差分，這份只拿來
歸因。規則：
  - CLI 路徑 `tokens` 為 null（CLI 不回報用量）；`kind` 是 null（成功）、`timeout`、`unrunnable`
    （`CliNotFoundError`）、`auth`（CLI 認證失效）或 `cli_error`。
  - HTTP 路徑沒收到 usage（失敗在第一個 chunk 之前）時 `tokens` 為 null；收到了但沒有
    `completion_tokens_details`（thinking 關時就是這樣，9/24 探測實測）時 `reasoning` 記 **0**
    ——thinking 關著，推理 token 就是 0，不是「不知道」。
  - 不記 prompt 本身，只記 sha256（研報全文不外流到 log）。
  - 有執行緒鎖（一行一次 write，行不交錯）；寫入失敗 fail-open，只警告一次。
"""
import errno
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

from app.services import llm_failures, llm_http
from app.services.llm_models import is_http_model, looks_like_cli_auth_error
from scripts._llm_env import BREAKER_TTL_S, ROOT, breaker_path, sync_round_id

# stderr 只留尾巴：完整 stderr 可能很長，而失敗記錄是給人掃讀的。200 字元夠容納
# 「usage: unknown flag」「Credit balance too low」這類真正有資訊量的那一行。
STDERR_TAIL_CHARS = 200


# 「這顆二進位在這個環境裡永遠跑不起來」的 errno。值是給人看的名字，會印進錯誤訊息。
_UNRUNNABLE_ERRNOS = {
    errno.ENOENT: "ENOENT 檔案不存在",
    errno.ENOEXEC: "ENOEXEC 不是可執行格式",
    errno.EACCES: "EACCES 沒有執行權限",
    errno.EPERM: "EPERM 不被允許執行",
    errno.EISDIR: "EISDIR 路徑是目錄",
}


class CliNotFoundError(RuntimeError):
    """`claude` 無法執行。整批註定全滅 → 由 main 提早中止。

    **名稱是歷史值，語意比名字寬**：涵蓋「不在 PATH」與「檔案在但跑不起來」。
    兩者在本專案都實際發生過——
    - ENOENT：systemd 的環境與登入 shell 不同，沒有 claude 的 PATH（修法是
      deploy/systemd/report-mark-web.service.d/path.conf 那種 drop-in）。
    - ENOEXEC：2026-08-20，claude CLI 自我更新到 2.1.237，而該版本的 native
      artifact 上游沒發布，postinstall 留下 500 bytes、無 shebang 的佔位腳本。
    """


class LlmEnvironmentError(CliNotFoundError):
    """HTTP 路徑的帳號層級失敗：金鑰無效（401）、餘額不足（402）、模型或端點設定錯（config）。

    與 `CliNotFoundError` 同一類：每一篇都會踩到，重試與續跑都沒有意義。繼承它是為了讓各批次
    main 既有的 `except CliNotFoundError → rc=2` 原封不動地接住，不必逐支加分支。
    """


class BadRequestEscalation(LlmEnvironmentError):
    """≥2 篇不同研報收到相同的 400 訊息：請求或設定壞了，不是單篇輸入（見模組 docstring）。

    `file_hashes`：觸發升級的研報（排序、去重）。各批次 main 在中止前以 `record_escalation`
    把它們記成 `bad_request`。
    """

    def __init__(self, message: str, file_hashes) -> None:
        super().__init__(message)
        self.file_hashes: tuple[str, ...] = tuple(file_hashes)


class CliResult(NamedTuple):
    """CLI 呼叫結果。text 為 None 時 error 必有值（且要說得出「為什麼」）。"""

    text: Optional[str]
    error: Optional[str]


def build_cli_args(prompt: str, model: str) -> list[str]:
    """組 `claude -p` 的 argv。

    `--setting-sources ""`＝不載入任何 settings 來源（user/project/local），連帶略過
    全域 hooks/plugins/CLAUDE.md —— 每次冷啟動載入它們正是磁碟小檔 I/O 的主因
    （實測加此 flag 後 page fault 降約 74%）。

    去掉 NUL：部分 PDF 抽出的文字含 `\\x00`，POSIX argv 不可含 NUL，否則 subprocess
    直接拋 ValueError('embedded null byte')，該檔會永久失敗。

    輸出格式用 CLI 預設的純文字（各家 parser 直接吃）：**不要加
    `--output-format json`**，那會把回應包進一層 CLI envelope，解析會抓到外層物件。

    `--tools ""`＝不開任何工具（`--help` 寫明 `""` 停用全部工具；list 傳參，空字串是獨立
    引數，同 `--setting-sources ""`）：批次只要模型讀 prompt 回文字，用不到讀檔、執行指令
    或網搜；工具開著時研報內文裡的指示有機會驅動模型去讀 cwd 的檔。刻意不用
    `--disallowedTools "*"`：本機 CLI 未記載萬用字元語意，很可能無效。`--tools` 是可變長度
    選項，會吞掉後面的位置引數，所以必須放在 prompt 之後、argv 的最後。

    `--strict-mcp-config`＝只用 `--mcp-config` 給的 MCP 伺服器；不帶 `--mcp-config` 就是一個
    都不載。`--tools ""` 只停用內建工具、管不到 MCP，兩者要一起給。它是布林旗標，放在
    `--tools` 之前（放在後面會被當成 `--tools` 的值）。
    """
    prompt = prompt.replace("\x00", "")
    return ["claude", "-p", prompt, "--model", model, "--setting-sources", "", "--strict-mcp-config", "--tools", ""]


# claude CLI 認證失效時的中止訊息（`cli_auth_error`）。CLI 已於 2026-09 永久停用：認證失效最常見的
# 真因不是「要重新登入」，而是該切 DeepSeek 的段還解析到 Claude（環境檔沒被讀到）。
CLI_AUTH_HINT = (
    "claude CLI 認證失效；若已切 DeepSeek，檢查 /etc/default/report-mark-llm 是否生效"
    "（LLM_PROVIDER=deepseek；手動執行要以 kashionz 身分、讀得到該檔）"
)


def cli_auth_error(stdout: Optional[str], stderr: Optional[str]) -> Optional["LlmEnvironmentError"]:
    """非零退出的 CLI 輸出若是認證失效，回要拋的例外（呼叫端 `raise`）；否則 None。

    stdout 與 stderr 都看：`claude -p` 純文字模式把認證錯誤印在 stdout（9/23 的 log 全是「無 stderr」）。
    """
    for text in (stderr, stdout):
        if looks_like_cli_auth_error(text):
            tail = (text or "").strip().replace("\n", " ")[:STDERR_TAIL_CHARS]
            return LlmEnvironmentError(f"{CLI_AUTH_HINT}。CLI 輸出：{tail}")
    return None


# 傳輸層退避用的 sleep；測試把它換成記錄器，免得真的等 2／6 秒。
_http_sleep = time.sleep

# 帳號層級錯誤的處置提示（接在 `API[<kind>]` 錯誤字串後面，一起印進中止訊息）。
_ACCOUNT_HINTS = {
    llm_http.AUTH: "檢查 DEEPSEEK_API_KEY（repo 根 .env 與 /etc/default/report-mark-llm 兩份逐字相同）",
    llm_http.QUOTA: "儲值後重跑；不要把 model 改成 Claude 繞過（那等於繞過預算）",
    llm_http.CONFIG: "檢查模型名與 DEEPSEEK_BASE_URL",
}


# ── 重試與失敗分類（批次共用） ─────────────────────────────────────────────
_API_PREFIX = "API["
_API_KIND = re.compile(r"^API\[([a-z_]+)\]")

# HTTP 失敗 kind → `research.llm_task_failure` 的 reason。只列「這篇研報的內容／輸入」造成的；
# 不在表上的（timeout、overloaded、network、other；帳號型早已拋出）一律不記。
_FAILURE_REASONS = {
    llm_http.CONTENT_FILTER: llm_failures.CONTENT_FILTER,
    llm_http.TRUNCATED: llm_failures.TRUNCATED,
    llm_http.EMPTY: llm_failures.EMPTY,
    llm_http.BAD_REQUEST: llm_failures.BAD_REQUEST,
}


def is_retryable(res: CliResult) -> bool:
    """腳本層值不值得再打一次：`API[` 開頭＝HTTP 路徑，傳輸層已重試過或本來就是決定性的 → False。"""
    return not (res.error or "").startswith(_API_PREFIX)


def error_kind(error: Optional[str]) -> Optional[str]:
    """`API[<kind>] …` → kind；CLI 的訊息或 None → None。"""
    m = _API_KIND.match(error or "")
    return m.group(1) if m else None


def failure_kind(res: CliResult) -> Optional[str]:
    """這次失敗要以哪個 reason 記入跳過名單；不該記（成功、CLI、環境型）回 None。"""
    if res.text is not None:
        return None
    return _FAILURE_REASONS.get(error_kind(res.error))


# ── 斷路器 ───────────────────────────────────────────────────────────────────
BREAKER_WINDOW = 10
BREAKER_TRIP = 5
BREAKER_KINDS = frozenset({llm_http.TIMEOUT, llm_http.OVERLOADED, llm_http.NETWORK})


def _warn(msg: str) -> None:
    # 批次腳本的 logger 無聲（logging 只在 web/server.py 初始化），用 stderr。
    print(f"[llm] {msg}", file=sys.stderr, flush=True)


def _write_breaker_marker(message: str) -> None:
    """寫 `data/.llm_breaker`（原子寫入）；失敗只警告——標記是給後續段的，這一段照樣中止。

    在 sync 輪次內（`SYNC_ROUND_ID`）另寫 `round=<id>`：預檢只拿它擋同一輪的段（`_llm_env._fresh_breaker`）。
    """
    path = breaker_path()
    round_id = sync_round_id()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            f"ts={datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            + (f"round={round_id}\n" if round_id else "")
            + f"reason={message}\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        _warn(f"斷路器標記寫入失敗（{path}）：{type(exc).__name__}: {exc}")


class _Breaker:
    """行程範圍、執行緒安全的滑動窗。見模組 docstring「斷路器」。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recent: deque[bool] = deque(maxlen=BREAKER_WINDOW)
        self._tripped: Optional[str] = None

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            self._tripped = None

    def check(self) -> None:
        """已跳脫就不再送出任何請求。"""
        if self._tripped is not None:
            raise LlmEnvironmentError(self._tripped)

    def observe(self, kind: Optional[str]) -> None:
        """記一次 HTTP 呼叫的結局；這一次讓窗內壞結局達門檻時拋出並寫標記。"""
        with self._lock:
            if self._tripped is not None:
                return  # 別的執行緒已經跳脫：這一篇的結果照常交回，下一次 check 會擋
            self._recent.append(kind in BREAKER_KINDS)
            bad = sum(self._recent)
            if bad < BREAKER_TRIP:
                return
            self._tripped = message = (
                f"LLM 斷路器：最近 {len(self._recent)} 次 DeepSeek 呼叫有 {bad} 次逾時／過載／連線失敗，"
                f"中止本段。{'本輪 sync' if sync_round_id() else f'{BREAKER_TTL_S // 60} 分鐘內'}"
                f"其他用到 DeepSeek 的段預檢會拒跑（標記 {breaker_path()}）；"
                "確認供應商恢復後可刪除標記"
            )
        _write_breaker_marker(message)
        raise LlmEnvironmentError(message)


_BREAKER = _Breaker()


# ── 400 升級 ─────────────────────────────────────────────────────────────────
BAD_REQUEST_ESCALATE_AT = 2  # 同一訊息出現在幾篇不同研報就升級

# 正規化：先把 id 類的長字串收掉（裡面的數字不該再被拆成 `#`），再把剩下的數字換成 `#`。
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LONG_HEX = re.compile(r"\b[0-9a-f]{16,}\b")
# request id 之類：前綴＋分隔＋英數混合，或 16 字元以上、同時有字母與數字的片段
_REQUEST_ID = re.compile(
    r"\b(?:req|request|trace|span)[-_ ]?id[:=\s]*[a-z0-9_-]+"
    r"|\b(?=[a-z0-9_-]*\d)(?=[a-z0-9_-]*[a-z])[a-z0-9_-]{16,}\b"
)
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")
# 本質上是單篇輸入造成的 400：不參與升級（見模組 docstring「400 升級」）。只收「輸入太長」這一類；
# `max_tokens` 參數本身不合法（例如超過上限）是呼叫點的程式錯、每篇都會踩到，**不能**排除。
_INPUT_SPECIFIC = re.compile(
    r"context[ _-]?length|maximum context|context window|reduce the length"
    r"|(?:prompt|input|message|messages|request) (?:is |are )?too long"
    r"|too many (?:input |prompt )?tokens"
    r"|(?:prompt|input|messages?) (?:length )?exceeds?"
)


def _escalation_key(detail: str) -> Optional[str]:
    """400 細節 → 升級比對鍵；單篇輸入造成的訊息回 None（不參與升級）。"""
    low = (detail or "").lower()
    if _INPUT_SPECIFIC.search(low):
        return None
    low = _UUID.sub("<id>", low)
    low = _REQUEST_ID.sub("<id>", low)
    low = _LONG_HEX.sub("<id>", low)
    low = _DIGITS.sub("#", low)
    return _SPACES.sub(" ", low).strip()


class _BadRequestTracker:
    """400 訊息（正規化後）→ 收到它的 file_hash 集合（行程範圍、執行緒安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, set[str]] = {}

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()

    def observe(self, detail: str, file_hash: Optional[str]) -> None:
        if not file_hash:
            return
        key = _escalation_key(detail)
        if key is None:
            return
        with self._lock:
            hashes = self._seen.setdefault(key, set())
            hashes.add(file_hash)
            if len(hashes) < BAD_REQUEST_ESCALATE_AT:
                return
            triggered = sorted(hashes)
        short = "、".join(h[:12] for h in triggered)
        raise BadRequestEscalation(
            llm_http.error_string(
                llm_http.CONFIG,
                f"{len(triggered)} 篇不同研報收到相同的 400（{detail[:120]}），判定為請求或設定錯誤而非單篇輸入；"
                f"觸發研報 {short}（已記入跳過名單，下一輪起跳過）",
            ),
            triggered,
        )


_BAD_REQUESTS = _BadRequestTracker()


async def record_escalation(exc: BaseException, recorder) -> None:
    """整批中止前：`BadRequestEscalation` 的觸發研報以 `bad_request` 記入跳過名單（審查 H2）。

    以 `escalated=True` 記：計數直接拉到 `SKIP_AFTER_ROUNDS`，下一輪就跳過（只記一筆的話，觸發篇
    下一輪還會再打、再升級，連續 3 輪整段 rc=2）。修好之後這幾篇要加 `--retry-blocked`。
    其他中止（帳號層級、斷路器、CLI 找不到）沒有 `file_hashes`，什麼都不做。`recorder` 為 None
    （表不存在）時只印出來。完整 file_hash 印在這裡，錯誤訊息裡只有前 12 碼。
    """
    hashes = getattr(exc, "file_hashes", ())
    if not hashes:
        return
    print(f"觸發 400 升級的研報：{', '.join(hashes)}", flush=True)
    if recorder is None:
        return
    for h in hashes:
        await recorder.record(h, llm_failures.BAD_REQUEST, escalated=True)


def _reset_state() -> None:
    """僅供測試：清掉行程範圍的狀態（斷路器、400 升級的計數）。"""
    _BREAKER.reset()
    _BAD_REQUESTS.reset()


# ── 用量記錄 ─────────────────────────────────────────────────────────────────
_USAGE_LOCK = threading.Lock()
_usage_warned = False


def usage_log_path() -> Path:
    """`LLM_USAGE_LOG` 只給測試用（conftest 指到 os.devnull）；生產一律 `data/llm_usage.jsonl`。"""
    return Path(os.environ.get("LLM_USAGE_LOG") or ROOT / "data" / "llm_usage.jsonl")


def _tokens(usage: Optional[dict]) -> Optional[dict]:
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details") or {}
    return {
        "hit": usage.get("prompt_cache_hit_tokens"),
        "miss": usage.get("prompt_cache_miss_tokens"),
        "completion": usage.get("completion_tokens"),
        # thinking 關時 DeepSeek 不回 completion_tokens_details：推理 token 就是 0（見模組 docstring）
        "reasoning": int(details.get("reasoning_tokens") or 0),
    }


def record_usage(
    *,
    meta: dict,
    backend: str,
    model_req: str,
    prompt: str,
    kind: Optional[str],
    total_ms: int,
    model_resp: Optional[str] = None,
    usage: Optional[dict] = None,
    finish_reason: Optional[str] = None,
    attempts: int = 1,
    ttft_ms: Optional[int] = None,
) -> None:
    """追加一行用量紀錄；任何失敗都只警告一次、不拋（fail-open）。"""
    global _usage_warned
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "task": meta.get("task"),
            "file_hash": meta.get("file_hash"),
            "report_id": meta.get("report_id"),
            "backend": backend,
            "model_req": model_req,
            "model_resp": model_resp,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8", "surrogatepass")).hexdigest(),
            "tokens": _tokens(usage) if backend == "http" else None,
            "finish_reason": finish_reason,
            "kind": kind,
            "attempts": attempts,
            "ttft_ms": ttft_ms,
            "total_ms": total_ms,
        }
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        path = usage_log_path()
        with _USAGE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:  # noqa: BLE001 — 用量記錄是歸因用的，不能擋批次
        if not _usage_warned:
            _usage_warned = True
            _warn(f"用量記錄寫入失敗（之後不再提示）：{type(exc).__name__}: {exc}")


def _cli_kind(res: CliResult) -> Optional[str]:
    if res.text is not None:
        return None
    return "timeout" if (res.error or "").startswith("CLI 逾時") else "cli_error"


def run_claude(
    prompt: str,
    model: str,
    timeout: int = 180,
    cwd: str = "/tmp",
    *,
    max_tokens: Optional[int] = None,
    meta: Optional[dict] = None,
) -> CliResult:
    """呼叫 LLM。回 (text, None) 或 (None, 可辨識的失敗原因)。

    DeepSeek 白名單的 model 走 HTTP（見模組 docstring「DeepSeek 分派」），其餘 spawn `claude -p`。
    `max_tokens`：HTTP 路徑必填，CLI 忽略。`meta`：`{"task", "file_hash", "report_id"}`，
    HTTP 路徑用 `task` 標 log 與 `user_id`。
    `cwd` 預設 /tmp：避免 CLI 載入專案 CLAUDE.md 拖慢每次呼叫（HTTP 路徑不用）。
    """
    meta = dict(meta or {})
    if is_http_model(model):
        return _run_http(prompt, model, timeout, max_tokens, meta)
    t0 = time.monotonic()
    try:
        res = _run_cli(prompt, model, timeout, cwd)
    except CliNotFoundError as exc:
        # LlmEnvironmentError 在 CLI 路徑只可能是認證失效（`cli_auth_error`）
        kind = "auth" if isinstance(exc, LlmEnvironmentError) else "unrunnable"
        record_usage(meta=meta, backend="cli", model_req=model, prompt=prompt, kind=kind,
                     total_ms=int((time.monotonic() - t0) * 1000))
        raise
    record_usage(meta=meta, backend="cli", model_req=model, prompt=prompt, kind=_cli_kind(res),
                 total_ms=int((time.monotonic() - t0) * 1000))
    return res


def _run_http(
    prompt: str, model: str, timeout: float, max_tokens: Optional[int], meta: dict
) -> CliResult:
    if max_tokens is None:
        raise ValueError(f"model={model} 走 HTTP，呼叫點必須帶 max_tokens（第二版計畫 §8）")
    task = str(meta.get("task") or "-")
    _BREAKER.check()
    t0 = time.monotonic()
    result = llm_http.complete_chat(
        model, prompt, max_tokens=max_tokens, timeout=float(timeout),
        task=task, user_id=f"batch-{task}", sleep=_http_sleep,
    )
    out = result.outcome
    record_usage(
        meta=meta, backend="http", model_req=model, prompt=prompt, kind=result.kind,
        total_ms=int((time.monotonic() - t0) * 1000), model_resp=out.model_resp, usage=out.usage,
        finish_reason=out.finish_reason, attempts=result.attempts, ttft_ms=out.ttft_ms,
    )
    if result.kind in llm_http.ACCOUNT_KINDS:
        raise LlmEnvironmentError(f"{result.error}。{_ACCOUNT_HINTS[result.kind]}")
    _BREAKER.observe(result.kind)
    if result.kind == llm_http.BAD_REQUEST:
        _BAD_REQUESTS.observe(result.outcome.detail, meta.get("file_hash"))
    if result.text is None:
        return CliResult(None, result.error)
    return CliResult(result.text, None)


def _run_cli(prompt: str, model: str, timeout: int, cwd: str) -> CliResult:
    """spawn `claude -p`（遷移前的 `run_claude` 本體；之後只加了認證失效的中止與 stdout 尾巴）。"""
    try:
        r = subprocess.run(
            build_cli_args(prompt, model),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except OSError as exc:
        # 環境層級的失敗：每一篇都會踩到，重試與續跑都沒有意義 → 中止整批。
        # **逐一列舉 errno，不寬泛接 OSError**：ENFILE／ENOMEM 那類是暫時性的資源
        # 壓力，中止整批反而不對；下列五個則是「這顆二進位永遠跑不起來」。
        if exc.errno in _UNRUNNABLE_ERRNOS:
            raise CliNotFoundError(
                f"`claude` CLI 無法執行（{_UNRUNNABLE_ERRNOS[exc.errno]}）。"
                f"ENOENT＝不在 PATH（systemd 下請補 PATH drop-in）；"
                f"ENOEXEC＝檔案在但不是可執行檔（見 2026-08-20 的 native artifact 缺件）"
            ) from exc
        return CliResult(None, f"CLI 呼叫失敗：{type(exc).__name__}: {exc}")
    except subprocess.TimeoutExpired:
        return CliResult(None, f"CLI 逾時（{timeout}s 內未回應）")
    except Exception as exc:  # noqa: BLE001 — 失敗原因要能寫進 log
        return CliResult(None, f"CLI 呼叫失敗：{type(exc).__name__}: {exc}")
    if r.returncode != 0:
        auth = cli_auth_error(r.stdout, r.stderr)
        if auth is not None:
            raise auth  # 每一篇都會踩到：整批中止（見模組 docstring「兩類失敗刻意分流」）
        tail = (r.stderr or "").strip().replace("\n", " ")[-STDERR_TAIL_CHARS:]
        if not tail:
            # stderr 空時看 stdout：CLI 有些錯誤只印在那裡（9/23 的 log 只剩「無 stderr」）
            out_tail = (r.stdout or "").strip().replace("\n", " ")[-STDERR_TAIL_CHARS:]
            tail = f"（無 stderr）stdout：{out_tail}" if out_tail else ""
        return CliResult(None, f"CLI 退出碼 {r.returncode}：{tail or '（無 stderr）'}")
    return CliResult(r.stdout, None)
