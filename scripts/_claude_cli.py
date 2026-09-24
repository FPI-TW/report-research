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
  - 其餘（逾時／非零退出／OSError…）回具體訊息，讓各自的 *_failures.log 說得出真因。
"""
import errno
import subprocess
from typing import NamedTuple, Optional

from app.services.llm_models import is_http_model

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


class HttpModelUnsupportedError(CliNotFoundError):
    """批次收到 DeepSeek 白名單的模型名，而批次還沒有 HTTP 分派（PR-12 才接）。

    與 `CliNotFoundError` 同一類：設定錯了，每一篇都會踩到——`claude --model deepseek-flash`
    每次都失敗，行內標註全數 `skip_untagged` 等於新研報停止入庫。所以同樣往上拋、由各批次
    main 以 rc=2 中止整批，**不能**回 `CliResult(None, …)` 被當成單篇失敗：那會讓研報以
    DeepSeek 的 model 名記進跳過名單（`research.llm_task_failure`）。入口的 `require_llm_key`
    已先擋一次，這裡是縱深防禦（例如測試或其他入口直接呼叫）。
    TODO(PR-12)：`run_claude` 依白名單分派到 `llm_http.complete_chat` 後刪掉這個例外。
    """


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


def run_claude(
    prompt: str, model: str, timeout: int = 180, cwd: str = "/tmp"
) -> CliResult:
    """呼叫 `claude -p`。回 (stdout, None) 或 (None, 可辨識的失敗原因)。

    `cwd` 預設 /tmp：避免載入專案 CLAUDE.md 拖慢每次呼叫。
    DeepSeek 白名單的 model 拋 `HttpModelUnsupportedError`（PR-12 之前批次不分派，見該類）。
    """
    if is_http_model(model):
        raise HttpModelUnsupportedError(
            f"批次尚未支援 DeepSeek（待 PR-12）：model={model} 不能交給 claude CLI；"
            "請改回 Claude 或移除該旋鈕"
        )
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
        tail = (r.stderr or "").strip().replace("\n", " ")[-STDERR_TAIL_CHARS:]
        return CliResult(None, f"CLI 退出碼 {r.returncode}：{tail or '（無 stderr）'}")
    return CliResult(r.stdout, None)
