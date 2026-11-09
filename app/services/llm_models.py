"""LLM 模型名稱的單一真相來源：走 HTTP 的白名單、各任務的預設表、`resolve_model`。

**葉模組：只 import 標準函式庫**（tests/test_llm_models.py 以 AST 釘住）。理由有二：

- `app.config` 要解析模型，而 `llm_http` 又需要白名單；
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
2. 否則 `LLM_DEFAULT_MODEL` 有非空值就用它（網搜那列除外）。
3. 否則查預設表 `MODEL_DEFAULTS`。只有網搜那列**刻意**是空字串：網搜沒有後端（見下方
   `MODEL_DEFAULTS` 的註解）。

`LLM_PROVIDER` 決定**送達路徑**，是每台主機固定的設定（沒有備援、不做 failover）。合法值：

| 值 | 路徑 | 可送的模型 |
|---|---|---|
| `openai`（預設；空值＝預設） | OpenAI 官方 Chat Completions | 內建白名單 `HTTP_MODELS` |
| `opencode_go` | 同一顆模型經 OpenCode Go 的 OpenAI 相容端點 | 內建白名單 |
| `azure_openai` | Azure OpenAI 的 deployment 路徑（`api-key`、`api-version`） | `AZURE_OPENAI_DEPLOYMENTS` 宣告的 |
| `anthropic` | Anthropic 原生 Messages API（`x-api-key`＋`anthropic-version`） | `ANTHROPIC_MODELS` 宣告的 |
| `openai_compatible` | 自架的 OpenAI 相容端點（vLLM 等） | `OPENAI_COMPATIBLE_MODELS` 宣告的 |

廷豐的兩條路徑（`openai`、`opencode_go`）白名單、預設表、judge 系譜一律相同，兩個環境的產出與量尺因此可以互相代表。
後三者的端點、金鑰與模型名都是部署層設定（環境變數，見 `ENDPOINT_ENV`、`DECLARED_MODELS_ENV`），不進 profile。

## 白名單＝內建＋設定宣告（`allowed_models`）

白名單是**安全閘門**：不在白名單的名稱一律在送出前以 config 失敗，絕不送到付費端點；它沒有旋鈕可以關。
內建的 `HTTP_MODELS` 只對 `openai`／`opencode_go` 生效（加名稱要發 PR：換模型要重新評測）。其餘三個 provider
沒有內建名稱，只認**目前 provider** 的宣告旋鈕列出的名稱——宣告別的 provider 的旋鈕不會放行任何名稱。宣告值格式錯
（`config_problems`）的那一筆不放行（fail-closed），啟動自檢與批次預檢會說出是哪個旋鈕。

`LLM_DEFAULT_MODEL`（選填）取代預設表的模型名（網搜那列除外），讓沒有 `gpt-6-luna` 的部署不必逐一設 13 個任務旋鈕；
空值＝`gpt-6-luna`。它只決定名稱，名稱一樣要在白名單內。

退役值（`RETIRED_PROVIDERS`：`deepseek`、`claude_cli`、`claude_only`）與未知值：web 記 ERROR、當成 `openai`（線上要
容錯，檢索等不需要 LLM 的功能不能因一個拼字停擺）；批次在預檢以 rc=2 拒跑（`scripts/_llm_env.require_llm_key`：
拒跑不花錢，而想「讓 LLM 停下來」的人設了退役值，不能變成照常計費）。

注意：預設表只決定「名稱」。名稱不在白名單內的呼叫一律失敗、不會被送到付費端點：

- 線上與評測經 `llm.stream_completion`：拋 `LLMUnavailableError(kind="config")`。
- 批次經 `scripts/_claude_cli.run_claude`（`generate_brief.call_cli` 也交給它）：拋 `LlmEnvironmentError`
  （整批 rc=2）；入口的 `scripts/_llm_env.require_llm_key` 在取鎖前就先擋。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

# 內建的模型**明確白名單**（`openai`／`opencode_go` 用；其他 provider 見模組 docstring「白名單」）。不在白名單的
# 名稱一律是設定錯誤（沒有其他 backend）：打錯的模型名不該被送到付費端點、拿到 400 後被當成帳號錯誤中止整批。
# 要加新名稱就發 PR——換模型本來就需要重新評測。
DEFAULT_MODEL = "gpt-6-luna"
HTTP_MODELS = frozenset({DEFAULT_MODEL})


def is_http_model(model: str | None, env: Mapping[str, str] | None = None) -> bool:
    """這個名稱此刻送不送得出去：在目前 provider 的白名單內（`allowed_models`）。"""
    return bool(model) and model in allowed_models(env)


# judge 的量尺系譜。換 judge＝換量尺：開新系譜、門檻數值不變，與舊系譜的分數不可比。離線評測
# （`eval/run_ragas.py` 記進 config.judge.lineage 與 notes）與生產忠實度的離線彙總
# （`scripts/eval_faithfulness.py`）共用，所以放在這個葉模組。後兩個是歷史系譜：只用來解讀舊的評測結果與
# `qa_log.evaluation.judge_model`，**永遠不跟著現行預設改**。
JUDGE_LINEAGE_LUNA = "gpt-6-luna-2026-11"
JUDGE_LINEAGE_DEEPSEEK_ERA = "deepseek-2026-09"
JUDGE_LINEAGE_CLAUDE = "claude-haiku"


# 設定宣告的模型當 judge 時，量尺系譜以模型名自成一系（`declared:<模型>`）：它與內建模型的系譜不可比，
# 也不該被名稱前綴歸到歷史系譜（例如宣告的 `claude-*` 不是 CLI 時代的 claude-haiku）。
JUDGE_LINEAGE_DECLARED_PREFIX = "declared:"


def judge_lineage(model: str | None, env: Mapping[str, str] | None = None) -> str:
    """judge 屬於哪個量尺系譜：內建白名單是現行系譜，目前 provider 宣告的名稱自成一系，其餘依名稱歸到歷史系譜。

    內建名稱看 `HTTP_MODELS` 本身、不看目前 provider：同一顆模型經哪條路徑送達都是同一把尺。
    """
    if model in HTTP_MODELS:
        return JUDGE_LINEAGE_LUNA
    if model and model in declared_models(env):
        return JUDGE_LINEAGE_DECLARED_PREFIX + model
    return JUDGE_LINEAGE_DEEPSEEK_ERA if (model or "").startswith("deepseek-") else JUDGE_LINEAGE_CLAUDE


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
# 每個任務都是同一顆模型（推理由 llm_http 以 `reasoning_effort=none` 關掉）。
# - 摘錄、訊號要逐字引文：tests/test_extract_takeaways_sql.py 以白名單守門，換成未量過錨定率的模型要先量。
# - judge 兩列（生產忠實度、離線 RAGAS）走 `llm_http.complete_json`（推理關、t=0、JSON 模式）。換 judge＝換量尺：
#   開新的量尺系譜（門檻數值不變；與舊系譜的分數不可比，eval_compare 回 2 是預期）。讀分數的三處只計現行 judge
#   （app/services/judge_schema.py），舊列歸「其他 judge」。
# - 網搜**刻意**是空字串＝沒有模型：網搜沒有後端。`llm.stream_completion` 對 `allow_web=True` 不論 model 一律拋
#   config 錯誤（時效題據此退回 M4 婉拒），填任何名稱都不會讓網搜變可用；`ASK_ENABLE_WEB` 預設也隨之是關。
MODEL_DEFAULTS: dict[str, str] = {
    task: ("" if task == TASK_ASK_WEB else DEFAULT_MODEL) for task in TASK_ENV
}

PROVIDER_OPENAI = "openai"
PROVIDER_OPENCODE_GO = "opencode_go"
PROVIDER_AZURE_OPENAI = "azure_openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDERS = (
    PROVIDER_OPENAI, PROVIDER_OPENCODE_GO, PROVIDER_AZURE_OPENAI, PROVIDER_ANTHROPIC, PROVIDER_OPENAI_COMPATIBLE,
)
# 用內建白名單的 provider；其餘只認宣告（模組 docstring「白名單」）。
BUILTIN_MODEL_PROVIDERS = frozenset({PROVIDER_OPENAI, PROVIDER_OPENCODE_GO})

# 各 provider 的金鑰環境變數（`llm_http._api_key`、批次預檢 `scripts/_llm_env`、啟動自檢共用）。
PROVIDER_KEY_ENV: dict[str, str] = {
    PROVIDER_OPENAI: "OPENAI_API_KEY",
    PROVIDER_OPENCODE_GO: "OPENCODE_GO_API_KEY",
    PROVIDER_AZURE_OPENAI: "AZURE_OPENAI_API_KEY",
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
    PROVIDER_OPENAI_COMPATIBLE: "OPENAI_COMPATIBLE_API_KEY",
}
PROVIDER_LABEL: dict[str, str] = {
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_OPENCODE_GO: "OpenCode Go",
    PROVIDER_AZURE_OPENAI: "Azure OpenAI",
    PROVIDER_ANTHROPIC: "Anthropic",
    PROVIDER_OPENAI_COMPATIBLE: "OpenAI 相容端點",
}

# ── 端點設定（部署層；呼叫時才讀，理由見 llm_http 模組 docstring「金鑰在呼叫時才讀」）──────────────────
# base URL 的旋鈕與預設值。None＝沒有預設、必填：Azure 的資源網址與自架端點只有部署的人知道。
ENDPOINT_ENV: dict[str, str] = {
    PROVIDER_OPENAI: "OPENAI_BASE_URL",
    PROVIDER_OPENCODE_GO: "OPENCODE_GO_BASE_URL",
    PROVIDER_AZURE_OPENAI: "AZURE_OPENAI_ENDPOINT",
    PROVIDER_ANTHROPIC: "ANTHROPIC_BASE_URL",
    PROVIDER_OPENAI_COMPATIBLE: "OPENAI_COMPATIBLE_BASE_URL",
}
DEFAULT_ENDPOINTS: dict[str, str | None] = {
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_OPENCODE_GO: "https://opencode.ai/inference/go/v1",
    PROVIDER_AZURE_OPENAI: None,
    PROVIDER_ANTHROPIC: "https://api.anthropic.com",
    PROVIDER_OPENAI_COMPATIBLE: None,
}
# Azure 的 `api-version` 查詢參數：必填、沒有預設。各版本支援的參數不同（`reasoning_effort`、
# `max_completion_tokens` 只有較新的版本有），猜一個預設值等於替部署的人決定行為。
AZURE_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
# 宣告模型的旋鈕（逗號分隔）。Azure 是 `模型名=deployment名`：請求路徑是 deployment，不是模型名。
DECLARED_MODELS_ENV: dict[str, str] = {
    PROVIDER_AZURE_OPENAI: "AZURE_OPENAI_DEPLOYMENTS",
    PROVIDER_ANTHROPIC: "ANTHROPIC_MODELS",
    PROVIDER_OPENAI_COMPATIBLE: "OPENAI_COMPATIBLE_MODELS",
}
# 端點能力旋鈕（布林，`1/true/yes/on` 或 `0/false/no/off`；空值＝預設）。推理強度的對應規則在
# `llm_http.reasoning_params`：不支援 `reasoning_effort` 的端點不送這個欄位（伺服器預設），而不是讓每個請求 400。
AZURE_REASONING_ENV = "AZURE_OPENAI_SUPPORTS_REASONING_EFFORT"           # 預設 1（與 OpenAI 同一族模型）
COMPAT_REASONING_ENV = "OPENAI_COMPATIBLE_SUPPORTS_REASONING_EFFORT"     # 預設 0（多數自架伺服器不認）
DEFAULT_MODEL_ENV = "LLM_DEFAULT_MODEL"

# 模型名與 deployment 名的字元集：模型名會進請求 body 與 log，deployment 名會進 URL 路徑——只收常見字元，
# 其餘一律當格式錯（不放行）。模型名允許 `/`、`:`、`@`（自架端點常見 `org/model`、`model:tag`）。
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}")
_DEPLOYMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

DEFAULT_PROVIDER = PROVIDER_OPENAI
# 曾經合法、現在沒有對應後端的值。與一般拼錯分開列，是為了讓錯誤訊息說得出這件事：web 記 ERROR 並當成
# 預設 provider，批次預檢 rc=2（見模組 docstring）。
RETIRED_PROVIDERS = frozenset({"deepseek", "claude_cli", "claude_only"})

# 同一則警告在一個行程裡只說一次：各模組在 import 期各自解析，不去重會一口氣印十幾行。
_LOGGED: set[tuple[str, ...]] = set()


def _log_once(level: int, key: tuple[str, ...], msg: str, *args: object) -> None:
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    logger.log(level, msg, *args)


def provider(env: Mapping[str, str] | None = None) -> str:
    """`LLM_PROVIDER` 正規化後的值；空值＝預設（`openai`），退役值與未知值記 ERROR 並當成預設。"""
    env = os.environ if env is None else env
    raw = (env.get("LLM_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw in RETIRED_PROVIDERS:
        _log_once(
            logging.ERROR, ("provider", raw),
            "LLM_PROVIDER=%r 已退役，沒有對應的後端；改用 %s。"
            "它不是停止開關：要讓問答停下來改設 ASK_ANSWER_MODEL 為白名單外名稱或清空 %s"
            "（見 docs/production_resilience.md「刻意讓 LLM 停下來」），否則刪掉這一行",
            raw, DEFAULT_PROVIDER, PROVIDER_KEY_ENV[DEFAULT_PROVIDER],
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


def _env_value(env: Mapping[str, str] | None, key: str) -> str:
    env = os.environ if env is None else env
    return (env.get(key) or "").strip()


def _parse_declared(prov: str, raw: str) -> tuple[dict[str, str], list[str]]:
    """宣告旋鈕的值 → ({模型名: deployment 名（非 Azure 時同模型名）}, 格式錯誤的說明)。格式錯的那一筆不收。"""
    out: dict[str, str] = {}
    problems: list[str] = []
    env_key = DECLARED_MODELS_ENV[prov]
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        if prov == PROVIDER_AZURE_OPENAI:
            name, sep, deployment = (p.strip() for p in item.partition("="))
            if not sep or not deployment:
                problems.append(f"{env_key} 的 {item!r} 不是 `模型名=deployment名`")
                continue
            if not _DEPLOYMENT_RE.fullmatch(deployment):
                problems.append(f"{env_key} 的 deployment 名 {deployment!r} 含不允許的字元（只收英數、`.`、`_`、`-`）")
                continue
        else:
            name, deployment = item, item
        if not _MODEL_NAME_RE.fullmatch(name):
            problems.append(f"{env_key} 的模型名 {name!r} 含不允許的字元")
            continue
        if name in out and out[name] != deployment:
            problems.append(f"{env_key} 把 {name!r} 對到兩個 deployment（{out[name]!r}、{deployment!r}）")
            out.pop(name)
            continue
        out[name] = deployment
    return out, problems


def declared_deployments(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """目前 provider 宣告的 {模型名: deployment 名}（非 Azure 時 deployment＝模型名）；內建 provider 回空。"""
    prov = provider(env)
    env_key = DECLARED_MODELS_ENV.get(prov)
    if env_key is None:
        return {}
    return _parse_declared(prov, _env_value(env, env_key))[0]


def declared_models(env: Mapping[str, str] | None = None) -> frozenset[str]:
    return frozenset(declared_deployments(env))


def allowed_models(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """目前 provider 的白名單（模組 docstring「白名單」）。"""
    if provider(env) in BUILTIN_MODEL_PROVIDERS:
        return HTTP_MODELS
    return declared_models(env)


def allowed_models_label(env: Mapping[str, str] | None = None) -> str:
    """白名單給人看的寫法（錯誤訊息用）；目前 provider 沒有宣告任何模型時說出這件事。"""
    return "、".join(sorted(allowed_models(env))) or f"LLM_PROVIDER={provider(env)} 沒有宣告任何模型"


def endpoint_base(env: Mapping[str, str] | None = None) -> str:
    """目前 provider 的 base URL（去尾 `/`）；必填而沒設時回空字串（`config_problems` 會說）。"""
    prov = provider(env)
    return (_env_value(env, ENDPOINT_ENV[prov]) or DEFAULT_ENDPOINTS[prov] or "").rstrip("/")


def _bool_knob(env: Mapping[str, str] | None, key: str, default: bool) -> bool | None:
    """布林旋鈕；空值＝預設，看不懂的值回 None（`config_problems` 會說，呼叫端當預設）。"""
    raw = _env_value(env, key).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def supports_reasoning_effort(env: Mapping[str, str] | None = None) -> bool:
    """目前 provider 的端點認不認 `reasoning_effort` 欄位（Anthropic 用 `thinking`，不適用，回 False）。"""
    prov = provider(env)
    if prov in BUILTIN_MODEL_PROVIDERS:
        return True
    if prov == PROVIDER_AZURE_OPENAI:
        return _bool_knob(env, AZURE_REASONING_ENV, True) is not False
    if prov == PROVIDER_OPENAI_COMPATIBLE:
        return _bool_knob(env, COMPAT_REASONING_ENV, False) is True
    return False


def config_problems(env: Mapping[str, str] | None = None) -> list[str]:
    """目前 provider 的端點設定錯誤（給人看的句子，空＝沒問題）。純讀環境、不探端點、不看金鑰。

    內建兩個 provider 的端點有預設值，這裡只在旋鈕被設成不合法網址時才說；其餘三個另檢查必填旋鈕與宣告。
    同一份判準用在三處：啟動自檢（web 記 ERROR、不擋啟動）、批次預檢（rc=2）、`llm_http` 送出前（config 失敗）。
    """
    prov = provider(env)
    out: list[str] = []
    url_env = ENDPOINT_ENV[prov]
    base = endpoint_base(env)
    if not base:
        out.append(f"缺 {url_env}（LLM_PROVIDER={prov} 沒有預設端點）")
    elif not re.match(r"https?://[^/\s]+", base):
        out.append(f"{url_env} 必須是 http(s):// 開頭的網址")
    if prov == PROVIDER_AZURE_OPENAI and not _env_value(env, AZURE_API_VERSION_ENV):
        out.append(f"缺 {AZURE_API_VERSION_ENV}（Azure 的 api-version 沒有預設值）")
    env_key = DECLARED_MODELS_ENV.get(prov)
    if env_key is not None:
        declared, problems = _parse_declared(prov, _env_value(env, env_key))
        out.extend(problems)
        if not declared and not problems:
            out.append(f"{env_key} 沒有宣告任何模型（LLM_PROVIDER={prov} 沒有內建白名單）")
    capability = {PROVIDER_AZURE_OPENAI: AZURE_REASONING_ENV, PROVIDER_OPENAI_COMPATIBLE: COMPAT_REASONING_ENV}
    key = capability.get(prov)
    if key is not None and _bool_knob(env, key, False) is None:
        out.append(f"{key}={_env_value(env, key)!r} 看不懂（用 1 或 0）")
    return out


def key_env(env: Mapping[str, str] | None = None) -> str:
    """目前 provider 的金鑰環境變數名稱。"""
    return PROVIDER_KEY_ENV[provider(env)]


def default_model(task: str) -> str:
    """某任務的預設（不看任務旋鈕）。"""
    return MODEL_DEFAULTS[task]


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
    fallback = _env_value(env, DEFAULT_MODEL_ENV)
    if fallback and task != TASK_ASK_WEB:
        return fallback
    return default_model(task)


def resolve_all(tasks: Iterable[str], env: Mapping[str, str] | None = None) -> dict[str, str]:
    return {t: resolve_model(t, env=env) for t in tasks}


def diagnose(
    resolved: Mapping[str, str],
    *,
    has_key: bool,
    web_enabled: bool = False,
    prov: str = PROVIDER_OPENAI,
    allowed: frozenset[str] | None = None,
    problems: Iterable[str] = (),
) -> list[tuple[int, str]]:
    """啟動自檢：解析結果 → [(logging level, 訊息)]。純函式，web/server.py 負責記錄。

    - 白名單名稱而目前 provider 的金鑰（`PROVIDER_KEY_ENV[prov]`）為空 → ERROR（那些任務每一次呼叫都會失敗）。
    - 不在白名單的名稱（含舊模型名、打錯字、空字串）→ ERROR：沒有其他 backend，
      `llm.stream_completion` 對這些名稱一律拋 config 錯誤。
    - 網搜任務（`ask_web`）不列入上面兩項：網搜沒有後端，
      `stream_completion` 對 `allow_web=True` 不論 model 一律拋 config 錯誤，它解析到什麼都一樣。
      要說的是總閘：`web_enabled`（`ASK_ENABLE_WEB`）開著 → ERROR——時效題會退回婉拒、使用者開了網搜
      的主答會以設定錯誤失敗。
    - `problems`：目前 provider 的端點設定錯誤（`config_problems`），每一則一行 ERROR。
    `allowed`：目前 provider 的白名單（`allowed_models`；None＝從環境讀）。
    都不擋啟動：檢索、閱讀頁、雷達、簡報的讀取都不需要 LLM。
    """
    allowed = allowed_models() if allowed is None else allowed
    out: list[tuple[int, str]] = []
    http: list[str] = []
    other: list[str] = []
    for task, model in resolved.items():
        if task == TASK_ASK_WEB:
            continue
        (http if model in allowed else other).append(f"{task}={model}")
    if http and not has_key:
        out.append((
            logging.ERROR,
            f"{PROVIDER_KEY_ENV[prov]} 為空（LLM_PROVIDER={prov}），但這些任務解析到白名單模型，"
            "呼叫會全數失敗：" + "、".join(http),
        ))
    if other:
        label = "、".join(sorted(allowed)) or f"LLM_PROVIDER={prov} 沒有宣告任何模型"
        out.append((
            logging.ERROR,
            f"模型名不在白名單（{label}；沒有其他 backend），"
            "這些任務每次呼叫都會以設定錯誤失敗：" + "、".join(other),
        ))
    for problem in problems:
        out.append((logging.ERROR, f"LLM 端點設定（LLM_PROVIDER={prov}）：{problem}"))
    if web_enabled:
        out.append((
            logging.ERROR,
            "ASK_ENABLE_WEB 開著，但網搜沒有後端："
            "時效題一律退回婉拒、開了網搜的主答會以設定錯誤失敗；設 ASK_ENABLE_WEB=0",
        ))
    return out
