"""LLM 批次與評測入口的環境載入與金鑰預檢（DeepSeek 遷移 PR-10）。

## 為什麼需要這支

DeepSeek 金鑰與批次的模型旋鈕放在 `/etc/default/report-mark-llm`（0640 root:kashionz），
**只有** `report-mark-sync.service` 以 `EnvironmentFile=` 載入它——讓「環境變數裡有金鑰的
行程」從十幾支 unit 縮到一支。手動跑批次（`make summaries`、`uv run python scripts/…`）不經過
systemd，所以每個會呼叫 LLM 的入口要自己讀同一份檔，手動與排程才會用同一組設定。

## 兩個函式、兩個時點

- `load_llm_env()`：**模組層呼叫，必須是 `sys.path.insert` 之後的第一個專案 import 緊接著的
  下一句**。`app/services/db.py` 在 import 期就 `get_settings()` 並快取，各批次的模型常數
  （`MODEL = resolve_model(...)`）也在 import 期解析；晚一步載入，這些值就已經定型成
  「沒讀到檔」的版本，而且不會有任何錯誤。tests/test_llm_env_loading.py 以 AST 掃描所有
  入口檔釘住這個順序（不是寫死清單：新入口只要 import 了 LLM 呼叫層，或 `answer`、
  `retrieval_pipeline` 這類間接呼叫 LLM 的服務層，就會被掃到）。
  讀檔用 `web.env_loader.load_env_file` 的語意：**只補還不存在的鍵**——sync unit 已由
  systemd 載入過同一份檔，這裡等於 no-op；shell 裡顯式 export 的值優先於檔案。
  讀不到檔（不存在、權限、其他 OSError）不拋，只記下原因，交給下面那個函式決定要不要擋。
- `require_llm_key(models)`：在**取 flock 之前**呼叫（撞鎖 rc=75 是「不跑」，缺金鑰卻是
  「跑了也白跑」，後者要先說）。以下情況印出原因並 `SystemExit(2)`：
  - 環境檔裡有重複的鍵（審查 L8）：`load_env_file` 先到先贏、systemd 的 EnvironmentFile 後者
    覆蓋——輪替金鑰時新舊兩行並存，sync unit 與手動批次會拿到**不同**的金鑰。
  - 有未知的模型名（不在 DeepSeek 白名單、也不是 `claude-*`；含 CLI 別名 `sonnet`）。
  - 有白名單模型卻沒有金鑰；依原因提示（環境裡已有空值→先 unset；PermissionError→以
    kashionz 執行；檔案不存在→依範例檔檔頭安裝；檔裡沒填→sudoedit）。訊息帶出是哪個旋鈕
    （或 `LLM_PROVIDER` 的預設、`--model`）解析出來的。批次（`run_claude`、`generate_brief`）與
    評測（`stream_completion`）都依白名單分派到 DeepSeek（遷移 PR-12 起），所以兩種入口同一套規則。
  - （只警告、不中止）全部解析成 Claude：CLI 已於 2026-09-23 停用，而 `LLM_PROVIDER` 自 PR-28 起
    預設 deepseek，所以這只會是有人顯式設成 Claude；印一行 WARNING 說出來源（`_warn_if_all_claude`）。
  - 本段會用到白名單模型，而 `data/.llm_breaker`（批次斷路器的標記，`scripts/_claude_cli.py`）
    還有效：前一段剛因 DeepSeek 大量逾時／過載而中止，這一段再跑只是每篇等到逾時。
    只看「會不會用到 HTTP」，全部用 Claude 的段不受影響（審查 L9）。「有效」的定義見下一節。

## 斷路器標記綁定 sync 輪次（審查中4）

`scripts/sync_new_reports.sh` 每輪 export `SYNC_ROUND_ID`（＝該輪的 `ROUND_TS`），跳脫時寫進標記的
`round=` 行。預檢時：
  - 預檢方與標記**都有**輪次 id：只有**同一輪**的標記才拒跑，不看時間。一輪最長可超過 2.5 小時，
    單靠 30 分鐘有效期擋不住同一輪後段；反過來上一輪末段跳脫的標記會擋下一輪的**匯入**（下一輪
    開頭就是匯入），delta 得靠人工重放——所以跨輪一律放行，下一輪若仍過載，斷路器會再跳一次。
  - 任一方沒有輪次 id（手動執行、或手動執行寫的標記）：維持 `BREAKER_TTL_S`（30 分鐘）規則。
  - 空標記檔（寫到一半被清空之類）沒有輪次 id，照 30 分鐘規則擋，訊息寫「標記是空的」。
  全部解析到 Claude 時不要求金鑰。環境檔缺失**不會**讓批次落到 Claude：`LLM_PROVIDER` 未設＝deepseek，
  而批次不讀 repo 根 `.env`，所以缺檔時是「缺金鑰 rc=2＋安裝提示」的明確失敗。
  通過時印 `fp=<金鑰 sha256 前 8 碼>` 供比對兩份金鑰是否一致，**永遠不印金鑰本身**。

另有 `python -m scripts._llm_env <環境檔> …`（`main`）：比對幾份環境檔的金鑰指紋，與上面同一套
解析（`file_key_fingerprint`），輪替後核對 `.env` 與 llm 檔用（docs/production_resilience.md）。

從 worktree 跑（`ROOT` 不是部署目錄）時另印警告：flock 以 checkout 為範圍，worktree 的批次
不與主 checkout 互斥，會把同一批研報再付一次錢。
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from app.services.llm_models import TASK_ENV, is_claude_model, is_http_model, provider, resolve_model
from web.env_loader import _parse_line, load_env_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLM_ENV_FILE = "/etc/default/report-mark-llm"
EXAMPLE = "deploy/systemd/report-mark-llm.env.example"
KEY = "DEEPSEEK_API_KEY"
RC_CONFIG = 2
# 斷路器標記的有效期（沒有 sync 輪次 id 時）：之內其他用到 DeepSeek 的段預檢拒跑。排程裡的段
# 改看輪次 id（見模組 docstring「斷路器標記綁定 sync 輪次」），這個值只管手動執行。
BREAKER_TTL_S = 30 * 60
# sync 殼每輪 export 的輪次 id（scripts/sync_new_reports.sh 的 ROUND_TS）；手動執行沒有。
ROUND_ENV = "SYNC_ROUND_ID"
_ROUND_PREFIX = "round="

# 上一次 load_llm_env() 的結果；require_llm_key() 據此給提示。模組層狀態是刻意的：
# 載入在 import 期、檢查在 main，中間沒有別的地方能放。
_STATE: dict[str, object] = {}


def env_file_path() -> Path:
    """`LLM_ENV_FILE` 只給測試用（conftest 指到不存在的路徑）；生產一律用預設路徑。"""
    return Path(os.environ.get("LLM_ENV_FILE") or DEFAULT_LLM_ENV_FILE)


def breaker_path() -> Path:
    """批次斷路器的標記（ROOT 錨點）。`LLM_BREAKER_FILE` 只給測試用（conftest 指到不存在的目錄，
    測試跳脫時寫不進部署目錄——repo 根就是部署目錄，寫進去會讓排程 30 分鐘拒跑）。"""
    return Path(os.environ.get("LLM_BREAKER_FILE") or ROOT / "data" / ".llm_breaker")


def sync_round_id() -> str | None:
    """本行程所屬的 sync 輪次 id（`SYNC_ROUND_ID`）；手動執行回 None。只取單行、去空白。"""
    raw = (os.environ.get(ROUND_ENV) or "").strip()
    return raw.splitlines()[0].strip() if raw else None


def _marker_round(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(_ROUND_PREFIX):
            return line[len(_ROUND_PREFIX):].strip() or None
    return None


def _fresh_breaker() -> str | None:
    """標記還有效就回它的內容（給人看），否則 None。讀不到一律當作沒有。

    有效＝同一個 sync 輪次（兩邊都有輪次 id 時），否則 `BREAKER_TTL_S` 內（見模組 docstring）。
    """
    path = breaker_path()
    try:
        age = time.time() - path.stat().st_mtime
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marked, current = _marker_round(text), sync_round_id()
    if marked and current:
        if marked != current:
            return None
    elif age >= BREAKER_TTL_S:
        return None
    return " ".join(text.split())[:400] or "（標記是空的）"


def load_llm_env() -> None:
    path = env_file_path()
    key_before = os.environ.get(KEY)
    state: dict[str, object] = {
        "path": path, "error": None, "duplicates": [], "file_has_key": False,
        "env_key_preset_empty": key_before is not None and not key_before.strip(),
    }
    _STATE.clear()
    _STATE.update(state)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _STATE["error"] = "missing"
        return
    except PermissionError:
        _STATE["error"] = "permission"
        return
    except OSError as exc:
        _STATE["error"] = f"{type(exc).__name__}"
        return
    parsed = [p for p in (_parse_line(line) for line in text.splitlines()) if p is not None]
    counts = Counter(k for k, _ in parsed)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    _STATE["file_has_key"] = any(k == KEY and v.strip() for k, v in parsed)
    if duplicates:
        # 不載入：哪一行生效取決於讀的人（先到先贏 vs 後者覆蓋），任何一個選擇都可能是錯的。
        _STATE["duplicates"] = duplicates
        return
    try:
        load_env_file(path)
    except OSError as exc:  # 讀第二次之間檔案被換掉之類；同樣只記下
        _STATE["error"] = f"{type(exc).__name__}"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def file_key_fingerprint(path: Path | str) -> str | None:
    """環境檔裡 `DEEPSEEK_API_KEY` 的指紋（前 8 碼）；沒有這個鍵或值為空回 None。

    與預檢、web 看到的值同一套解析（`web.env_loader._parse_line`：去 `export `、去成對引號、
    strip），所以引號、尾隨空白、CRLF 都不會讓兩份其實相同的金鑰算出不同指紋。重複鍵取第一行
    （同 `load_env_file`）；`main` 另外把重複當成失敗。讀檔錯誤原樣拋出，交給呼叫端說明
    （見 docs/production_resilience.md）。
    """
    values = _file_key_values(path)
    return fingerprint(values[0]) if values and values[0] else None


def _file_key_values(path: Path | str) -> list[str]:
    """環境檔裡每一行 `DEEPSEEK_API_KEY` 的值（依檔內順序、已 strip）。讀檔錯誤原樣拋出。"""
    text = Path(path).read_text(encoding="utf-8")
    parsed = (_parse_line(line) for line in text.splitlines())
    return [p[1].strip() for p in parsed if p is not None and p[0] == KEY]


def _say(msg: str) -> None:
    print(f"[llm-env] {msg}", file=sys.stderr, flush=True)


def _fail(msg: str) -> None:
    _say(msg)
    raise SystemExit(RC_CONFIG)


def _is_worktree(root: Path) -> bool:
    """linked worktree 的 `.git` 是檔案（指向主 repo），主 checkout 是目錄。"""
    return (root / ".git").is_file()


def _warn_if_not_deploy_root() -> None:
    deploy_root = (os.environ.get("REPORT_MARK_ROOT") or "").strip()
    differs = bool(deploy_root) and Path(deploy_root).resolve() != ROOT.resolve()
    if differs or _is_worktree(ROOT):
        _say(
            f"警告：從 {ROOT} 執行，不是部署目錄"
            f"{f'（REPORT_MARK_ROOT={deploy_root}）' if deploy_root else ''}。"
            "批次鎖不與主 checkout 互斥，同一批研報可能被再付費處理一次。"
        )


def _missing_key_hint(path: Path) -> str:
    err = _STATE.get("error")
    if _STATE.get("env_key_preset_empty") and _STATE.get("file_has_key"):
        return f"環境裡已有空的 {KEY}，它擋住了 {path} 的值（只補不存在的鍵）：先 `unset {KEY}` 再執行"
    if err == "permission":
        return f"讀不到 {path}（PermissionError；檔案是 0640 root:kashionz）：以 kashionz 執行"
    if err == "missing":
        return f"{path} 不存在：依 {EXAMPLE} 檔頭的指令安裝後用 sudoedit 填金鑰"
    if err:
        return f"讀 {path} 失敗（{err}）"
    if _STATE.get("env_key_preset_empty"):
        return f"環境裡的 {KEY} 是空值，且 {path} 也沒有填：先 `unset {KEY}`，再用 sudoedit 填進環境檔"
    return f"{path} 沒有填 {KEY}：用 sudoedit 填（不要 echo／tee，也不要 source 這個檔）"


def _warn_if_all_claude(pairs: list[tuple[str | None, str]], path: object) -> None:
    """本段模型全部解析成 Claude：印一行醒目的 WARNING（不中止）。

    PR-28 之前預設是 `claude_cli`，這一行只在「環境檔缺失或讀不到」時印，用來抓「切 DeepSeek 沒生效」。
    PR-28 起 `LLM_PROVIDER` 預設 deepseek：環境檔缺失時批次解析到 DeepSeek、因缺金鑰 rc=2（明確失敗），
    不會再落到 Claude。所以「全部解析成 Claude」只剩一種來源——有人**顯式**設了 `LLM_PROVIDER=claude_cli`
    ／`claude_only`、`claude-*` 的任務旋鈕或 `--model`（shell export、unit 的 `Environment=`，或環境檔本身），
    與環境檔讀不讀得到無關，因此不再以檔案狀態為條件；檔案沒讀到時一併說明。claude CLI 已於 2026-09-23
    永久放棄，這一段每一篇都會撞認證失效。不中止：這兩個值在 PR-M 之前仍合法，真的撞到認證失效時
    `_claude_cli.cli_auth_error` 會整批 rc=2。
    """
    if not pairs:
        return
    sources = sorted({_model_source(t, m) for t, m in pairs})
    err = _STATE.get("error")
    file_note = ""
    if err:
        why = {"missing": "不存在", "permission": "讀不到（PermissionError）"}.get(str(err), f"讀取失敗（{err}）")
        file_note = f"；另外 {path} {why}"
    _say(
        f"WARNING：本段模型全部解析成 Claude（{'、'.join(sources)}）。claude CLI 已於 2026-09-23 停用，"
        f"這一段每一篇都會失敗；生產應為 LLM_PROVIDER=deepseek——檢查 shell、unit 的 Environment= 或 {path} "
        f"是否把 LLM_PROVIDER 或任務旋鈕設成了 Claude{file_note}"
    )


def _model_source(task: str | None, model: str) -> str:
    """說出這個模型名是從哪裡來的：任務旋鈕、`LLM_PROVIDER` 的預設表，或 `--model`。"""
    knob = TASK_ENV.get(task or "")
    if knob is None:
        return model
    raw = (os.environ.get(knob) or "").strip()
    if raw == model:
        return f"{knob}={model}"
    if not raw and resolve_model(task) == model:
        prov_raw = (os.environ.get("LLM_PROVIDER") or "").strip()
        prov = f"LLM_PROVIDER={provider()}" if prov_raw else f"LLM_PROVIDER（未設，預設 {provider()}）"
        return f"{prov} 的 {task} 預設 {model}"
    return f"--model {model}（任務 {task}）"


def require_llm_key(models: Mapping[str, str | None] | Iterable[str | None]) -> None:
    """預檢本次會用到的模型；不通過就 `SystemExit(2)`（說明見模組 docstring）。

    `models`：`{任務: 模型}`（批次；缺金鑰的訊息才說得出是哪個旋鈕）或模型名清單（評測）。
    """
    pairs = list(models.items()) if isinstance(models, Mapping) else [(None, m) for m in models]
    pairs = [(t, m) for t, m in pairs if m]
    names = sorted({m for _, m in pairs})
    if not _STATE:
        load_llm_env()
    path = _STATE.get("path") or env_file_path()
    duplicates = _STATE.get("duplicates") or []
    if duplicates:
        _fail(
            f"{path} 有重複的鍵：{', '.join(duplicates)}。systemd 取最後一行、手動批次取第一行，"
            "兩邊會用不同的值；刪掉多餘的行再執行"
        )
    unknown = [m for m in names if not (is_http_model(m) or is_claude_model(m))]
    if unknown:
        _fail(f"未知模型名：{', '.join(unknown)}（只接受 DeepSeek 白名單或 claude-*）")
    _warn_if_not_deploy_root()
    http = [m for m in names if is_http_model(m)]
    if not http:
        _warn_if_all_claude(pairs, path)
        return
    tripped = _fresh_breaker()
    if tripped:
        when = f"本輪 sync（{sync_round_id()}）" if sync_round_id() else f"{BREAKER_TTL_S // 60} 分鐘內"
        _fail(
            f"批次斷路器{when}跳脫過（{breaker_path()}：{tripped}）。"
            f"本段要用 {', '.join(http)}，先不跑；確認 DeepSeek 恢復後刪除該檔，或等標記過期"
        )
    key = (os.environ.get(KEY) or "").strip()
    if not key:
        sources = sorted({_model_source(t, m) for t, m in pairs if is_http_model(m)})
        _fail(
            f"{'、'.join(sources)} 需要 {KEY}，但目前沒有值。{_missing_key_hint(Path(str(path)))}"
        )
    _say(f"DeepSeek 金鑰 fp={fingerprint(key)}（模型：{', '.join(http)}）")


def main(argv: list[str] | None = None) -> int:
    """比對幾份環境檔的金鑰指紋（輪替後核對兩份是否逐字相同；只印前 8 碼，不印金鑰）。

    用法：`uv run python -m scripts._llm_env .env /etc/default/report-mark-llm`
    rc=0：每份都有值且指紋相同；rc=1：有缺值、讀不到、不一致，或某份檔裡 `DEEPSEEK_API_KEY`
    不只一行——systemd 的 EnvironmentFile 取最後一行、`load_env_file` 先到先贏，兩邊會拿到
    不同的值（同 `require_llm_key` 的重複鍵拒跑），只比第一行會誤報一致。
    """
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print("用法：python -m scripts._llm_env <環境檔> [<環境檔> …]", file=sys.stderr)
        return RC_CONFIG
    fps: set[str | None] = set()
    duplicated = False
    for p in paths:
        try:
            values = _file_key_values(p)
        except PermissionError:
            print(f"{p}  讀不到（PermissionError：以 kashionz 執行，或加入該檔的群組）")
            fps.add(None)
            continue
        except OSError as exc:
            print(f"{p}  讀不到（{type(exc).__name__}）")
            fps.add(None)
            continue
        fp = fingerprint(values[0]) if values and values[0] else None
        print(f"{p}  fp={fp}" if fp else f"{p}  （沒有 {KEY} 或值為空）")
        fps.add(fp)
        if len(values) > 1:
            duplicated = True
            each = "、".join(f"fp={fingerprint(v)}" if v else "（空值）" for v in values)
            print(
                f"{p}  警告：{KEY} 有 {len(values)} 行（{each}）。systemd 的 EnvironmentFile 取最後一行、"
                "程式（load_env_file）取第一行，兩邊會用不同的金鑰；刪掉多餘的行再核對"
            )
    ok = None not in fps and len(fps) == 1 and not duplicated
    if duplicated:
        print("有重複的鍵，不能判定一致")
    else:
        print("一致" if ok else "不一致或有缺值")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
