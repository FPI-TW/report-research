"""LLM 模型名稱的單一真相來源：走 HTTP 的白名單、各任務的預設表、`resolve_model`。

**葉模組：只 import 標準函式庫**（tests/test_llm_models.py 以 AST 釘住）。理由有二：

- `app.config` 要解析模型（PR-M 前還要用白名單實作 `claude_only`），而 `llm_http` 又需要白名單；
  白名單若留在 `llm_http`，config ↔ llm_http 就會在 import 期互相拉（審查 L2）。放在這裡，兩邊都只依賴
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
2. 否則查預設表 `DEEPSEEK_DEFAULTS`（第二版計畫 §8）。只有網搜那列**刻意**是空字串：網搜沒有
   後端（見下方 `DEEPSEEK_DEFAULTS` 的註解）。

`LLM_PROVIDER` 只剩一個合法值 `deepseek`（預設；空值＝預設）。**claude CLI 已於 2026-09-23 永久放棄**
（OAuth 過期、不再修復登入，計畫 D-C），遷移終局 PR-M 移除了 CLI backend，連帶退役 `claude_cli`／
`claude_only` 兩個值與 Claude 預設表（`RETIRED_PROVIDERS`）。設了退役值或未知值：web 記 ERROR、當成
`deepseek`（線上要容錯，檢索等不需要 LLM 的功能不能因一個拼字停擺）；批次在預檢以 rc=2 拒跑
（`scripts/_llm_env.require_llm_key`：拒跑不花錢，而想「讓 LLM 停下來」的人設了退役值，不能變成照常計費）。
沒有可回退的後端：402 或 DeepSeek 停機時 LLM 功能全部停擺，處置是儲值或等服務恢復
（docs/production_resilience.md）。

注意：預設表只決定「名稱」。名稱不在白名單內的呼叫一律失敗、不會被送到付費端點：

- 線上與評測經 `llm.stream_completion`：拋 `LLMUnavailableError(kind="config")`。
- 批次經 `scripts/_claude_cli.run_claude`（`generate_brief.call_cli` 也交給它）：拋 `LlmEnvironmentError`
  （整批 rc=2）；入口的 `scripts/_llm_env.require_llm_key` 在取鎖前就先擋。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

# 走 HTTP 的模型**明確白名單**。不在這裡的名稱一律是設定錯誤（PR-M 起沒有其他 backend）：若寫成
# 「看起來像 DeepSeek 就送」，打錯的模型名會被送到付費端點、拿到 400 後被當成帳號錯誤中止整批。
# `deepseek-v4-flash` 是官方保留的舊名（導向 V4.1-Flash、按 Flash 計價）。
# 要加新名稱就發 PR——換模型本來就需要重新評測。
HTTP_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"})


def is_http_model(model: str | None) -> bool:
    return model in HTTP_MODELS


# judge 的量尺系譜（PR-26/27）：judge 從 Claude haiku 換成 DeepSeek 時開了新系譜（計畫 D-J a：沒有 Claude
# 對照組可以重跑，照切、門檻數值不變）。離線評測（`eval/run_ragas.py` 記進 config.judge.lineage 與 notes）
# 與生產忠實度的離線彙總（`scripts/eval_faithfulness.py`）共用，所以放在這個葉模組。
JUDGE_LINEAGE_DEEPSEEK = "deepseek-2026-09"
JUDGE_LINEAGE_CLAUDE = "claude-haiku"


def judge_lineage(model: str | None) -> str:
    """judge 屬於哪個量尺系譜：白名單（DeepSeek）是 PR-26/27 起的新系譜，其餘是 Claude 時代的舊系譜。"""
    return JUDGE_LINEAGE_DEEPSEEK if is_http_model(model) else JUDGE_LINEAGE_CLAUDE


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
# DeepSeek 表（第二版計畫 §8；thinking 一律由 llm_http 關掉）。PR-M 起是唯一的預設表（Claude 表隨 CLI 退役）。
# - 摘錄、訊號要逐字引文：D6 依 9/24 探測與 D-A（主判準＝exact／normalized／prefix 任一方式的
#   錨定成功率）定為 flash——flash 95.0%，同批研報的 Claude 既有摘錄 90.9%。
#   tests/test_extract_takeaways_sql.py 以白名單守門，換成未量過錨定率的模型要先量。
# - judge 兩列（生產忠實度、離線 RAGAS）自 PR-26/27 起是 flash（thinking 關、t=0、JSON 模式，經
#   `llm_http.complete_json`）。換 judge＝換量尺：依計畫 D-J a 直接切換、開新的量尺系譜（門檻數值
#   不變；與 Claude haiku 時代的分數互比不可比，eval_compare 回 2 是預期）。沒有 Claude 對照組可以
#   重跑（CLI 已放棄），校準改用 qa_log 歷史 haiku 判定做描述性比較（scripts/judge_agreement.py）。
#   讀分數的三處只計現行 judge（app/services/judge_schema.py），歷史 haiku 列歸「其他 judge」。
# - 網搜**刻意**是空字串＝沒有模型：Claude CLI 的 WebSearch 隨 PR-M 移除，DeepSeek 網搜（Tavily 工具
#   迴圈）延後到 P9。`llm.stream_completion` 對 `allow_web=True` 不論 model 一律拋 config 錯誤（時效題
#   據此退回 M4 婉拒），填任何名稱都不會讓網搜變可用；`ASK_ENABLE_WEB` 預設也隨之改為關。P9 接上
#   Tavily 時再把這列改成白名單名稱。
DEEPSEEK_DEFAULTS: dict[str, str] = {
    TASK_ASK_ANSWER: "deepseek-flash",
    TASK_ASK_WEB: "",
    TASK_ASK_INTENT: "deepseek-flash",
    TASK_ASK_CONDENSE: "deepseek-flash",
    TASK_QA_PLANNER: "deepseek-flash",
    TASK_ASK_FOLLOWUP: "deepseek-flash",
    TASK_FAITHFULNESS: "deepseek-flash",
    TASK_EVAL_JUDGE: "deepseek-flash",
    TASK_TAG: "deepseek-flash",
    TASK_SUMMARY: "deepseek-flash",
    TASK_TITLE: "deepseek-flash",
    TASK_TAKEAWAY: "deepseek-flash",
    TASK_SIGNAL: "deepseek-flash",
    TASK_BRIEF: "deepseek-flash",
}

PROVIDER_DEEPSEEK = "deepseek"
PROVIDERS = (PROVIDER_DEEPSEEK,)
DEFAULT_PROVIDER = PROVIDER_DEEPSEEK
# PR-M 退役的值（claude CLI 已放棄、Claude 預設表已刪）。與一般拼錯分開列，是為了讓錯誤訊息說得出
# 「這個值曾經合法、現在沒有對應的後端」：web 記 ERROR 並當成 deepseek，批次預檢 rc=2（見模組 docstring）。
RETIRED_PROVIDERS = frozenset({"claude_cli", "claude_only"})

# 同一則警告在一個行程裡只說一次：各模組在 import 期各自解析，不去重會一口氣印十幾行。
_LOGGED: set[tuple[str, ...]] = set()


def _log_once(level: int, key: tuple[str, ...], msg: str, *args: object) -> None:
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    logger.log(level, msg, *args)


def provider(env: Mapping[str, str] | None = None) -> str:
    """`LLM_PROVIDER` 正規化後的值；空值＝預設（`deepseek`），退役值與未知值記 ERROR 並當成預設。"""
    env = os.environ if env is None else env
    raw = (env.get("LLM_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw in RETIRED_PROVIDERS:
        _log_once(
            logging.ERROR, ("provider", raw),
            "LLM_PROVIDER=%r 已隨 claude CLI 退役（PR-M），沒有可回退的後端；改用 %s。"
            "要讓線上 LLM 停下來沒有旋鈕可用——刪掉這一行，處置見 docs/production_resilience.md",
            raw, DEFAULT_PROVIDER,
        )
        return DEFAULT_PROVIDER
    if raw not in PROVIDERS:
        _log_once(
            logging.ERROR, ("provider", raw),
            "LLM_PROVIDER=%r 不是合法值（可用：%s），改用 %s",
            raw, "/".join(PROVIDERS), DEFAULT_PROVIDER,
        )
        return DEFAULT_PROVIDER
    return raw


def default_model(task: str) -> str:
    """某任務的預設（不看任務旋鈕）。"""
    return DEEPSEEK_DEFAULTS[task]


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

    仍呼叫 `provider(env)`：它不影響解析結果（只有一張表），但退役值與拼錯的 ERROR 要在第一個解析
    模型的地方就說出來（各模組在 import 期解析，web 行程不一定走到啟動自檢的那一行）。
    """
    env_key = TASK_ENV[task]
    env = os.environ if env is None else env
    raw = override if override is not None else env.get(env_key)
    knob = (raw or "").strip()
    provider(env)
    if knob:
        return knob
    return default_model(task)


def resolve_all(tasks: Iterable[str], env: Mapping[str, str] | None = None) -> dict[str, str]:
    return {t: resolve_model(t, env=env) for t in tasks}


def diagnose(
    resolved: Mapping[str, str],
    *,
    has_key: bool,
    web_enabled: bool = False,
) -> list[tuple[int, str]]:
    """啟動自檢：解析結果 → [(logging level, 訊息)]。純函式，web/server.py 負責記錄。

    - 白名單名稱而 `DEEPSEEK_API_KEY` 為空 → ERROR（那些任務每一次呼叫都會失敗）。
    - 不在白名單的名稱（含 `claude-*`、CLI 別名、打錯字、空字串）→ ERROR：PR-M 起沒有其他 backend，
      `llm.stream_completion` 對這些名稱一律拋 config 錯誤。
    - 網搜任務（`ask_web`）不列入上面兩項：網搜沒有後端（Claude CLI 已移除、DeepSeek 網搜延後到 P9），
      `stream_completion` 對 `allow_web=True` 不論 model 一律拋 config 錯誤，它解析到什麼都一樣。
      要說的是總閘：`web_enabled`（`ASK_ENABLE_WEB`）開著 → ERROR——時效題會退回婉拒、使用者開了網搜
      的主答會以設定錯誤失敗。
    都不擋啟動：檢索、閱讀頁、雷達、簡報的讀取都不需要 LLM。
    """
    out: list[tuple[int, str]] = []
    http: list[str] = []
    other: list[str] = []
    for task, model in resolved.items():
        if task == TASK_ASK_WEB:
            continue
        (http if is_http_model(model) else other).append(f"{task}={model}")
    if http and not has_key:
        out.append((
            logging.ERROR,
            "DEEPSEEK_API_KEY 為空，但這些任務解析到 DeepSeek 模型，呼叫會全數失敗："
            + "、".join(http),
        ))
    if other:
        out.append((
            logging.ERROR,
            "模型名不在 DeepSeek 白名單（claude CLI 已於 PR-M 移除，沒有其他 backend），"
            "這些任務每次呼叫都會以設定錯誤失敗：" + "、".join(other),
        ))
    if web_enabled:
        out.append((
            logging.ERROR,
            "ASK_ENABLE_WEB 開著，但網搜沒有後端（claude CLI 已移除、DeepSeek 網搜延後到 P9）："
            "時效題一律退回婉拒、開了網搜的主答會以設定錯誤失敗；設 ASK_ENABLE_WEB=0",
        ))
    return out
