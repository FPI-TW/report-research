"""LLM 模型名稱的單一真相來源：走 HTTP 的白名單、各任務的預設表、`resolve_model`。

**葉模組：只 import 標準函式庫**（tests/test_llm_models.py 以 AST 釘住）。理由有二：

- `app.config` 要用白名單實作 `claude_only`，而 `llm_http` 又需要白名單；白名單若留在
  `llm_http`，config ↔ llm_http 就會在 import 期互相拉（審查 L2）。放在這裡，兩邊都只依賴
  一個不依賴任何人的模組。`llm_http` 以原名重新匯出 `HTTP_MODELS`、`is_http_model`。
- 批次腳本的金鑰預檢（`scripts/_llm_env.py`）必須在任何會快取 Settings 的專案模組之前執行，
  它要判斷「這個名稱走不走付費端點」，只能 import 不會牽動其他模組的東西。

`resolve_model` 放在這裡而不是 `app/config.py`：批次腳本（`tag_all_cli.py` 完全不碰 DB）與
`followups.py`、`eval/judge.py` 這類留在原檔讀環境變數的模組也要用它，而 `get_settings()`
會連帶做 R2 設定的 fail-closed 驗證、並在第一次呼叫時快取。線上任務的解析結果仍經
`Settings` 欄位提供（`ask_answer_model` 等），與既有旋鈕同一個入口。

## 解析規則（`resolve_model(task)`）

1. 任務旋鈕（`TASK_ENV`）有非空值就用它。**空字串視同未設**：tests/conftest.py 會把所有
   旋鈕強制設成 `""`，擋住部署目錄 `.env` 的值滲進測試。
2. 否則查 `LLM_PROVIDER` 對應的預設表：
   - `claude_cli`（預設）：現行 Claude 預設表，值與遷移前各呼叫點寫死的字串逐字相同。
   - `deepseek`：DeepSeek 預設表（第二版計畫 §8）。judge 兩列仍是 Claude——judge 要等校準
     後由 PR-26／PR-27 才換；網搜那列也仍是 Claude（DeepSeek 網搜延後到 P9）。
   - `claude_only`：緊急回退。**任務旋鈕裡白名單內的值一律忽略**，全部用 Claude 預設表，
     記 WARNING。所以任何階段只要改這一個鍵就能全部回到 CLI，不必逐一清掉任務旋鈕。
     旋鈕裡的 Claude 名稱照用（那本來就是 CLI）。
   - 未知值記 ERROR 並當成 `claude_cli`：打錯字不得讓任何任務被送到付費端點。

注意：預設表只決定「名稱」。名稱在白名單內時實際走不走 HTTP，由分派層
（`llm.stream_completion`、`scripts/_claude_cli.run_claude`）決定。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

# 走 HTTP 的模型**明確白名單**。不在這裡的名稱一律留給 CLI 路徑：若寫成「`claude-` 以外都走
# HTTP」，CLI 別名（`sonnet`）或打錯的模型名會被送到付費端點、拿到 400 後被當成帳號錯誤
# 中止整批。`deepseek-v4-flash` 是官方保留的舊名（導向 V4.1-Flash、按 Flash 計價）。
# 要加新名稱就發 PR——換模型本來就需要重新評測。
HTTP_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"})


def is_http_model(model: str | None) -> bool:
    return model in HTTP_MODELS


def is_claude_model(model: str | None) -> bool:
    """CLI 路徑認得的正式名稱。CLI 別名（`sonnet`、`haiku`）刻意不算：自檢與批次預檢會把它
    當成未知名稱擋下，免得「換個別名」繞過白名單的評測紀律。"""
    return bool(model) and model.startswith("claude-")


# ── 任務與旋鈕 ───────────────────────────────────────────────────────────────
TASK_ASK_ANSWER = "ask_answer"      # 總覽潤飾、主答（不開網搜）、評測生成的預設
TASK_ASK_WEB = "ask_web"            # 時效題網搜、主答開網搜
TASK_ASK_INTENT = "ask_intent"      # 首輪五類路由
TASK_ASK_CONDENSE = "ask_condense"  # 續問改寫
TASK_QA_PLANNER = "qa_planner"      # 查詢規劃、agentic 證據評估
TASK_ASK_FOLLOWUP = "ask_followup"  # 追問建議
TASK_FAITHFULNESS = "faithfulness"  # 生產忠實度 judge
TASK_EVAL_JUDGE = "eval_judge"      # 離線 RAGAS judge
TASK_TAG = "tag"                    # 行內標註（sync_new_reports）、全語料標註（tag_all_cli）
TASK_SUMMARY = "summary"
TASK_TITLE = "title"
TASK_TAKEAWAY = "takeaway"
TASK_SIGNAL = "signal"
TASK_BRIEF = "brief"

# 任務 → 旋鈕。`ASK_FOLLOWUP_MODEL`、`EVAL_JUDGE_MODEL` 的 os.getenv 刻意留在原檔
# （followups.py、eval/judge.py），那兩處以 `override=` 傳進來；這裡仍列出，讓 conftest、
# 自檢與文件有一份完整清單。
TASK_ENV: dict[str, str] = {
    TASK_ASK_ANSWER: "ASK_ANSWER_MODEL",
    TASK_ASK_WEB: "ASK_WEB_MODEL",
    TASK_ASK_INTENT: "ASK_INTENT_MODEL",
    TASK_ASK_CONDENSE: "ASK_CONDENSE_MODEL",
    TASK_QA_PLANNER: "QA_PLANNER_MODEL",
    TASK_ASK_FOLLOWUP: "ASK_FOLLOWUP_MODEL",
    TASK_FAITHFULNESS: "FAITHFULNESS_MODEL",
    TASK_EVAL_JUDGE: "EVAL_JUDGE_MODEL",
    TASK_TAG: "TAG_MODEL",
    TASK_SUMMARY: "SUMMARY_MODEL",
    TASK_TITLE: "TITLE_MODEL",
    TASK_TAKEAWAY: "TAKEAWAY_MODEL",
    TASK_SIGNAL: "SIGNAL_MODEL",
    TASK_BRIEF: "BRIEF_MODEL",
}

# web 行程會呼叫的任務（啟動自檢的範圍）。評測 judge 與批次不在 web 行程裡跑。
ONLINE_TASKS: tuple[str, ...] = (
    TASK_ASK_ANSWER, TASK_ASK_WEB, TASK_ASK_INTENT, TASK_ASK_CONDENSE,
    TASK_QA_PLANNER, TASK_ASK_FOLLOWUP, TASK_FAITHFULNESS,
)

# ── 預設表 ───────────────────────────────────────────────────────────────────
# Claude 表的每一個值都等於遷移前該呼叫點寫死的字串（tests/test_llm_models.py 逐列釘住）。
# 追問那列帶日期後綴是歷史原樣，刻意不順手統一——改了就是換模型。
CLAUDE_DEFAULTS: dict[str, str] = {
    TASK_ASK_ANSWER: "claude-sonnet-5",
    TASK_ASK_WEB: "claude-sonnet-5",
    TASK_ASK_INTENT: "claude-haiku-4-5",
    TASK_ASK_CONDENSE: "claude-haiku-4-5",
    TASK_QA_PLANNER: "claude-haiku-4-5",
    TASK_ASK_FOLLOWUP: "claude-haiku-4-5-20251001",
    TASK_FAITHFULNESS: "claude-haiku-4-5",
    TASK_EVAL_JUDGE: "claude-haiku-4-5",
    TASK_TAG: "claude-haiku-4-5",
    TASK_SUMMARY: "claude-sonnet-5",
    TASK_TITLE: "claude-sonnet-5",
    TASK_TAKEAWAY: "claude-sonnet-5",
    TASK_SIGNAL: "claude-sonnet-5",
    TASK_BRIEF: "claude-sonnet-5",
}

# DeepSeek 表（第二版計畫 §8；thinking 一律由 llm_http 關掉）。
# - 摘錄、訊號要逐字引文，D6 在 P2 比對後才拍板 flash 或 v4-pro；拍板前先填 flash，
#   PR-28 翻轉預設時依 D6 更新（tests/test_extract_takeaways_sql.py 的「不可退成小模型」
#   屆時改寫意圖）。
# - judge 兩列維持 Claude：換 judge＝換量尺，要等校準（PR-26 離線、PR-27 生產）。
# - 網搜維持 Claude：DeepSeek 網搜（Tavily 工具迴圈）延後到 P9，遷移期網搜仍走 CLI。
DEEPSEEK_DEFAULTS: dict[str, str] = {
    TASK_ASK_ANSWER: "deepseek-flash",
    TASK_ASK_WEB: "claude-sonnet-5",
    TASK_ASK_INTENT: "deepseek-flash",
    TASK_ASK_CONDENSE: "deepseek-flash",
    TASK_QA_PLANNER: "deepseek-flash",
    TASK_ASK_FOLLOWUP: "deepseek-flash",
    TASK_FAITHFULNESS: "claude-haiku-4-5",
    TASK_EVAL_JUDGE: "claude-haiku-4-5",
    TASK_TAG: "deepseek-flash",
    TASK_SUMMARY: "deepseek-flash",
    TASK_TITLE: "deepseek-flash",
    TASK_TAKEAWAY: "deepseek-flash",
    TASK_SIGNAL: "deepseek-flash",
    TASK_BRIEF: "deepseek-flash",
}

PROVIDER_CLAUDE_CLI = "claude_cli"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_CLAUDE_ONLY = "claude_only"
PROVIDERS = (PROVIDER_CLAUDE_CLI, PROVIDER_DEEPSEEK, PROVIDER_CLAUDE_ONLY)
DEFAULT_PROVIDER = PROVIDER_CLAUDE_CLI

# 同一則警告在一個行程裡只說一次：各模組在 import 期各自解析，不去重會一口氣印十幾行。
_LOGGED: set[tuple[str, ...]] = set()


def _log_once(level: int, key: tuple[str, ...], msg: str, *args: object) -> None:
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    logger.log(level, msg, *args)


def provider(env: Mapping[str, str] | None = None) -> str:
    """`LLM_PROVIDER` 正規化後的值；空值＝預設，未知值記 ERROR 並當成 `claude_cli`。"""
    env = os.environ if env is None else env
    raw = (env.get("LLM_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw not in PROVIDERS:
        _log_once(
            logging.ERROR, ("provider", raw),
            "LLM_PROVIDER=%r 不是合法值（可用：%s），改用 %s",
            raw, "/".join(PROVIDERS), DEFAULT_PROVIDER,
        )
        return DEFAULT_PROVIDER
    if raw == PROVIDER_CLAUDE_ONLY:
        _log_once(
            logging.WARNING, ("claude_only",),
            "LLM_PROVIDER=claude_only（緊急回退）：所有任務改用 Claude 預設表，任務旋鈕裡的 DeepSeek 名稱一律忽略",
        )
    return raw


def default_model(task: str, provider_name: str = DEFAULT_PROVIDER) -> str:
    """某任務在某 provider 下的預設（不看任務旋鈕）。`claude_only` 用 Claude 表。"""
    table = DEEPSEEK_DEFAULTS if provider_name == PROVIDER_DEEPSEEK else CLAUDE_DEFAULTS
    return table[task]


def resolve_model(
    task: str,
    *,
    override: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """任務實際要用的模型名稱（規則見模組 docstring）。

    `override`：呼叫端自己讀到的旋鈕值（`followups.py`、`eval/judge.py` 把 os.getenv 留在
    原檔，讀到的值從這裡傳入）；None＝照 `TASK_ENV` 從 env 讀。未知任務拋 KeyError——任務名
    打錯是程式錯誤，不該靜默落到某張表。
    """
    env_key = TASK_ENV[task]
    env = os.environ if env is None else env
    raw = override if override is not None else env.get(env_key)
    knob = (raw or "").strip()
    prov = provider(env)
    if knob:
        if prov == PROVIDER_CLAUDE_ONLY and is_http_model(knob):
            fallback = CLAUDE_DEFAULTS[task]
            _log_once(
                logging.WARNING, ("claude_only_ignored", env_key, knob),
                "LLM_PROVIDER=claude_only：忽略 %s=%s，改用 %s", env_key, knob, fallback,
            )
            return fallback
        return knob
    return default_model(task, prov)


def resolve_all(tasks: Iterable[str], env: Mapping[str, str] | None = None) -> dict[str, str]:
    return {t: resolve_model(t, env=env) for t in tasks}


def diagnose(
    resolved: Mapping[str, str],
    *,
    has_key: bool,
    claude_path: str | None,
    path_env: str = "",
) -> list[tuple[int, str]]:
    """啟動自檢：解析結果 → [(logging level, 訊息)]。純函式，web/server.py 負責記錄。

    - 白名單名稱而 `DEEPSEEK_API_KEY` 為空 → ERROR（那些任務每一次呼叫都會失敗）。
    - `claude-*` → 沿用既有的 claude CLI 路徑檢查（找不到是 ERROR，找到記 WARNING 留路徑）。
    - 其他名稱 → ERROR「未知模型名」（打錯字、CLI 別名）。
    - 網搜任務（`ask_web`）解析到白名單名稱 → ERROR：DeepSeek 網搜延後到 P9，
      `llm.stream_completion` 對 `allow_web=True`＋白名單 model 一律拋 config 錯誤。
    都不擋啟動：檢索、閱讀頁、雷達、簡報的讀取都不需要 LLM。
    """
    out: list[tuple[int, str]] = []
    by_kind: dict[str, list[str]] = {"http": [], "claude": [], "unknown": []}
    for task, model in resolved.items():
        kind = "http" if is_http_model(model) else "claude" if is_claude_model(model) else "unknown"
        by_kind[kind].append(f"{task}={model}")
    if by_kind["http"] and not has_key:
        out.append((
            logging.ERROR,
            "DEEPSEEK_API_KEY 為空，但這些任務解析到 DeepSeek 模型，呼叫會全數失敗："
            + "、".join(by_kind["http"]),
        ))
    if by_kind["claude"]:
        if claude_path:
            out.append((logging.WARNING, f"claude CLI：{claude_path}"))
        else:
            out.append((
                logging.ERROR,
                "claude CLI 不在 PATH 上：問答會全數失敗（檢查 report-mark-web.service.d/path.conf）；"
                f"PATH={path_env}",
            ))
    web_model = resolved.get(TASK_ASK_WEB)
    if is_http_model(web_model):
        out.append((
            logging.ERROR,
            f"{TASK_ENV[TASK_ASK_WEB]}={web_model}：DeepSeek 網搜尚未支援（延後到 P9），"
            "開網搜的問答會全數失敗；網搜模型應維持 Claude",
        ))
    if by_kind["unknown"]:
        out.append((
            logging.ERROR,
            "未知模型名（不在 DeepSeek 白名單、也不是 claude-*），這些任務會失敗："
            + "、".join(by_kind["unknown"]),
        ))
    return out
