"""LLM 模型名稱的單一真相來源：走 HTTP 的白名單、各任務的預設表、`resolve_model`。
另收 claude CLI 認證失效的辨識樣式（`looks_like_cli_auth_error`，批次與線上共用，理由見該段註解）。

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
   - `deepseek`（**預設**，遷移 PR-28 起）：DeepSeek 預設表（第二版計畫 §8）。只有網搜那列**刻意**
     仍是 Claude（生產以 `ASK_ENABLE_WEB=0` 關閉網搜，由 PR-W 處理），見下方 `DEEPSEEK_DEFAULTS` 的註解；
     judge 兩列自 PR-26/27 起是 deepseek-flash（新量尺系譜）。
   - `claude_cli`：遷移前的 Claude 預設表，值與遷移前各呼叫點寫死的字串逐字相同。
   - `claude_only`：遷移期的緊急回退。**任務旋鈕裡白名單內的值一律忽略**，全部用 Claude
     預設表，記 WARNING。旋鈕裡的 Claude 名稱照用（那本來就是 CLI）。
   - 未知值記 ERROR 並當成預設（`deepseek`）：打錯字的效果與沒設相同。
3. 空值（沒設或空字串）＝預設 `deepseek`。所以 `/etc/default/report-mark-llm` 缺檔時批次仍解析
   到 DeepSeek，並因批次不讀 repo 根 `.env`、拿不到金鑰而在預檢以 rc=2 明確失敗
   （`scripts/_llm_env.require_llm_key`），不會退回已失效的 CLI。

**claude CLI 已於 2026-09-23 永久放棄**（OAuth 過期、不再修復登入，計畫 D-C）：`claude_cli` 與
`claude_only` 兩個值仍是合法值、解析規則不變（留到 PR-M 再決定去留），但**實際上已沒有可用的
後端**——設了等於解析到 CLI 的任務全部失敗。回退只剩「修 prompt 或換 `deepseek-v4-pro`」。
測試仍以 `claude_cli` 跑（tests/conftest.py 強制設定，理由見該檔），不代表生產預設。

注意：預設表只決定「名稱」。名稱在白名單內時實際走不走 HTTP，由分派層決定：

- 線上與評測經 `llm.stream_completion` 依白名單分派。
- 批次經 `scripts/_claude_cli.run_claude` 依白名單分派（遷移 PR-12）；`generate_brief.call_cli` 的
  DeepSeek 分支也交給它。入口的 `scripts/_llm_env.require_llm_key` 只擋未知名稱與缺金鑰。
"""

from __future__ import annotations

import logging
import os
import re
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


# judge 的量尺系譜（PR-26/27）：judge 從 Claude haiku 換成 DeepSeek 時開了新系譜（計畫 D-J a：沒有 Claude
# 對照組可以重跑，照切、門檻數值不變）。離線評測（`eval/run_ragas.py` 記進 config.judge.lineage 與 notes）
# 與生產忠實度的離線彙總（`scripts/eval_faithfulness.py`）共用，所以放在這個葉模組。
JUDGE_LINEAGE_DEEPSEEK = "deepseek-2026-09"
JUDGE_LINEAGE_CLAUDE = "claude-haiku"


def judge_lineage(model: str | None) -> str:
    """judge 屬於哪個量尺系譜：白名單（DeepSeek）是 PR-26/27 起的新系譜，其餘是 Claude 時代的舊系譜。"""
    return JUDGE_LINEAGE_DEEPSEEK if is_http_model(model) else JUDGE_LINEAGE_CLAUDE


# ── claude CLI 認證失效的辨識 ────────────────────────────────────────────────
# 2026-09-23 起 CLI 的 OAuth 過期（`Failed to authenticate: OAuth session expired and could not be
# refreshed`），批次的 `claude -p` 一律「退出碼 1、stderr 空、訊息在 stdout」，被當成單篇失敗逐篇
# 記錄、整批 rc=0——與四天停擺同一型態。認證失效每一篇都會踩到，批次（`scripts/_claude_cli.py`、
# `generate_brief.py`）要整批中止、線上（`llm.stream_completion` 的 CLI 路徑）要歸 `kind="auth"`
# 且不重試。放在這個葉模組：批次與線上兩邊都要用，而批次不該為了一個樣式 import `llm.py`。
# 樣式刻意收窄到 CLI／API 的固定措辭（不含單獨的 "401"、"unauthorized"：模型回答裡可能出現）。
_CLI_AUTH_ERROR = re.compile(
    r"failed to authenticate"
    r"|oauth (?:session|token)\b[^\n]{0,60}?\b(?:expired|revoked|invalid)"
    r"|invalid api key"
    r"|please run /login"
    r"|not logged in"
    r"|authentication_error",
    re.IGNORECASE,
)
# 只看開頭這麼多字：認證錯誤訊息都很短；看全文的話，一份碰巧談到 API 金鑰的長回答會被誤判。
_CLI_AUTH_SCAN_CHARS = 600


def looks_like_cli_auth_error(text: str | None) -> bool:
    """claude CLI 的輸出（stdout、stderr 或 stream-json 的 result 文字）是不是認證失效。"""
    return bool(text) and bool(_CLI_AUTH_ERROR.search(text[:_CLI_AUTH_SCAN_CHARS]))


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

# DeepSeek 表（第二版計畫 §8；thinking 一律由 llm_http 關掉）。自 PR-28 起是預設表。
# - 摘錄、訊號要逐字引文：D6 依 9/24 探測與 D-A（主判準＝exact／normalized／prefix 任一方式的
#   錨定成功率）定為 flash——flash 95.0%，同批研報的 Claude 既有摘錄 90.9%。
#   tests/test_extract_takeaways_sql.py 以白名單守門，換成未量過錨定率的模型要先量。
# - judge 兩列（生產忠實度、離線 RAGAS）自 PR-26/27 起是 flash（thinking 關、t=0、JSON 模式，經
#   `llm_http.complete_json`）。換 judge＝換量尺：依計畫 D-J a 直接切換、開新的量尺系譜（門檻數值
#   不變；與 Claude haiku 時代的分數互比不可比，eval_compare 回 2 是預期）。沒有 Claude 對照組可以
#   重跑（CLI 已放棄），校準改用 qa_log 歷史 haiku 判定做描述性比較（scripts/judge_agreement.py）。
#   讀分數的三處只計現行 judge（app/services/judge_schema.py），歷史 haiku 列歸「其他 judge」。
# - 網搜**刻意**維持 Claude：DeepSeek 網搜延後到 P9；`llm.stream_completion` 對
#   `allow_web=True`＋白名單 model 一律拋 config 錯誤，填 DeepSeek 名稱不會讓網搜變可用。
#   生產以 `ASK_ENABLE_WEB=0` 關閉網搜，前端開關的隱藏由 PR-W 處理。
DEEPSEEK_DEFAULTS: dict[str, str] = {
    TASK_ASK_ANSWER: "deepseek-flash",
    TASK_ASK_WEB: "claude-sonnet-5",
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

PROVIDER_CLAUDE_CLI = "claude_cli"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_CLAUDE_ONLY = "claude_only"
PROVIDERS = (PROVIDER_CLAUDE_CLI, PROVIDER_DEEPSEEK, PROVIDER_CLAUDE_ONLY)
# PR-28：預設改 deepseek。CLI 已放棄，預設留在 claude_cli 的話，`/etc/default/report-mark-llm`
# 缺檔（sync unit 是 `EnvironmentFile=-`，缺檔照跑）時批次會靜默退回失效的 CLI。
DEFAULT_PROVIDER = PROVIDER_DEEPSEEK

# 同一則警告在一個行程裡只說一次：各模組在 import 期各自解析，不去重會一口氣印十幾行。
_LOGGED: set[tuple[str, ...]] = set()


def _log_once(level: int, key: tuple[str, ...], msg: str, *args: object) -> None:
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    logger.log(level, msg, *args)


def provider(env: Mapping[str, str] | None = None) -> str:
    """`LLM_PROVIDER` 正規化後的值；空值＝預設（`deepseek`），未知值記 ERROR 並當成預設。"""
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
    - `claude-*` → 沿用既有的 claude CLI 路徑檢查（找不到是 ERROR，找到記 WARNING 留路徑），
      兩者都列出解析到 Claude 的任務。CLI 已放棄，找得到也不代表能用（認證失效由呼叫時的
      `kind="auth"` 回報）；預設 deepseek 下只剩網搜會走到這條。
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
        # 列出是哪些任務：預設 deepseek 下只剩網搜解析到 Claude，「問答全數失敗」不再成立。
        claude_tasks = "、".join(by_kind["claude"])
        if claude_path:
            out.append((logging.WARNING, f"claude CLI：{claude_path}（{claude_tasks}）"))
        else:
            out.append((
                logging.ERROR,
                f"claude CLI 不在 PATH 上，這些任務會失敗：{claude_tasks}"
                f"（檢查 report-mark-web.service.d/path.conf）；PATH={path_env}",
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
