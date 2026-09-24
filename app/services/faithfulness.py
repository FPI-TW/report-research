"""M8 忠實度查核（claim grounding，RAGAS 式 reference-free）。

把生成內容拆成原子主張、標記數值型主張、逐條對「解析後的證據文字」做 grounding
（supported / unsupported / no_source），分開記錄 faithfulness_score 與
numeric_support_rate。分數是「來源支持度／待複核」，非真實性保證。

分層（皆 fail-open：judge 異常／JSON 壞／schema 不合 → 不阻擋交付，僅標 degraded、不加分數）：
- 純 primitive（judge 注入、零 DB）：decompose_statements / ground_statements /
  faithfulness — 可用假 judge 做決定性單測，eval/ragas_metrics.py 亦復用之。
- 生產進入點 check_faithfulness（judge 預設依 model 分派：白名單走 DeepSeek 非串流 JSON 模式、
  其餘走 claude CLI；仍可注入假 judge）。
- resolve_evidence_texts：把 EvidenceLedger 的 corpus 證據回查成文字（碰 DB，另測）。
  帳本只存來源身分不存文字（見 evidence.py），故 grounding 前必須回查；corpus 的
  chunk_id 實務多為 NULL，回查落在 report_id 粒度——v1 已知限制。

契約 judge：async judge(system: str, user: str) -> dict | list | None。

**重試層數與最壞呼叫次數**（依 judge 走哪條路徑而不同；每個階段＝一次 call_validated，即拆解、
grounding、CP 或反推問題各算一次）：
- **HTTP（DeepSeek；adapter 自遷移 PR-18，judge 預設自 PR-26/27）**：只有一層預算，**每個階段最多 3 個請求**
  （`judge_schema.HTTP_STAGE_MAX_REQUESTS`，tests/test_faithfulness.py 與 tests/test_eval_judge.py 斷言）。
  三種重試共用它、不相乘：`llm_http.complete_json` 的截斷（2 倍 max_tokens）／空回應／暫時性重試
  （每次 judge 呼叫最多 2 個請求）、`call_validated` 的 schema 重試（只拿得到剩下的預算），離線
  `eval.judge.judge_json` 的暫時性重試在 HTTP 路徑不跑。逾時、審查、帳號錯誤不重試。
  生產一次抽查（拆解＋grounding）最壞 6 個請求；離線一題（F 兩階段＋CP＋AR）最壞 12 個。
- **CLI（`LLM_PROVIDER=claude_cli`；CLI 已於 2026-09-23 放棄，只剩測試與回退用）**：維持原樣。
  生產每個階段 `call_validated` 的 schema 重試（1 次）×`stream_completion` 的 529 重試（共 3 次）
  ＝最多 6 次 CLI spawn；離線同一指標 `call_validated`（2 次）×`judge_json`（EVAL_JUDGE_RETRIES=1，
  共 2 次）＝最多 4 次 judge 呼叫，每次底下再有 529 重試，CLI spawn 最壞 12 次。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text as sql_text

from app.services import llm_http
from app.services.evidence import EvidenceLedger
from app.services.judge_schema import (
    JUDGE_SCHEMA_VERSION,
    JudgeSchemaError,
    call_validated,
    http_attempts_allowed,
    parse_statements,
    parse_verdicts,
    record_http_requests,
)
from app.services.llm import DEFAULT_MODEL, UNAVAILABLE_TIMEOUT, LLMUnavailableError, stream_completion
from app.services.llm_models import is_http_model
from app.services.query_planner import parse_plan_json

logger = logging.getLogger(__name__)


# ── Prompt 常數（就地共置；一律要求 JSON-only。eval/ragas_metrics.py 復用之）──

DECOMPOSE_SYS = (
    "你是 RAG 評測助手。把下列『回答』拆解成一組獨立、原子的事實主張（statement）。"
    "每個主張須可獨立判斷真偽，不含連接詞堆疊。若回答只是『找不到資料』之類、"
    "未提出任何事實主張，回空陣列。\n"
    '只輸出 JSON，格式：{"statements": ["主張1", "主張2", ...]}，不要任何其他文字。'
)

GROUND_SYS = (
    "你是 RAG 忠實度評審。給定『參考片段』與一組『主張』，逐一判斷每個主張是否"
    "能由參考片段直接佐證支持（supported）。只依片段內容判斷，不用外部知識；片段沒說到、"
    "或與片段矛盾，一律 supported=false。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "supported": true}, ...]}，'
    "idx 對應主張的 0-based 序號，不要任何其他文字。"
)

# grounding 的 user payload 版型。抽成常數是為了讓離線評測把它算進 `judge_prompt_sha`
# （eval/ragas_metrics.py）：payload 的編號與排版跟系統提示一樣是量尺的一部分，
# 改了卻不改雜湊，就會拿兩把不同的尺比出一個看似可信的 delta。
GROUND_ITEM_FMT = "{i}. {s}"
GROUND_PAYLOAD_FMT = "參考片段：\n{contexts}\n\n主張：\n{claims}"


# ── 數值主張偵測（確定性，零 LLM）──
#
# 這個判斷是問答抽查閘門的**唯一依據**，漏判的代價不對稱：
#   - `answer._faithfulness_spot_check` 的入口閘門：整份回答判非數值 → 完全不查核，
#     未獲語料支持的數字就這樣靜默交付，監控頁的 below_min 也永遠看不到它。
# 反之偽陽性只是多跑一次 grounding。所以**寧可多抓，不可漏抓**。
#
# 原本只有繁體中文金融名詞白名單，在 M10 雙語上線後破功（2026-07-29 生產實測）：
# 一份英文提問產出的研報，17 條主張**全部**被判非數值 → 修正輪從未觸發。
# 實測 90 條真實主張中漏判 8 條，型態有四類，各自對應下面一條規則：
#   指數點位/大數字（45,000點、44,454點、1,049,256）→ 千分位回退
#   裸小數（動量指數為 48.7）                      → 小數回退
#   倍數寫成 x、英文 P/E（P/E 约为 10x）            → [xX] 單位與 P/E
#   簡體與英文金融詞（营收/净利/目标价/revenue…）    → 名詞表補齊
#
# **刻意不把「點」列為單位**：指數點位一律帶千分位逗號，已被回退規則涵蓋，
# 而加了它會讓「第 3 點提到…」變成數值主張——正是本判斷從一開始就要避免的誤判。
# 年份同理排除：`2026 年新建晶圓廠` 不是可查核的數值主張。
# 關鍵字到數字的距離維持 12 字：實測放寬到 30 字一條都沒有多抓到。
_NUMERIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*%"                       # 30% / 12.5 %
    r"|[\$￥€]\s*\d"                            # $123
    # 金額／倍數／可數量詞（繁簡並列）
    r"|\d+(?:\.\d+)?\s*(?:元|億|亿|萬|万|兆|百萬|百万|美元|台幣|台币|新台幣|新台币"
    r"|人民幣|人民币|港元|倍|個百分點|个百分点|座|家|bps|BP|[xX]\b)"
    # 金融名詞近旁的數字（繁／簡／英）。裸 PE 不列入：會咬中 PERFORMANCE 這類英文字。
    r"|(?:EPS|每股|營收|营收|收入|毛利率?|營益率|营益率|營業利益率?|营业利润率?"
    r"|淨利|净利|净利润|目標價|目标价|本益比|市盈率|P/E|殖利率|股息率"
    r"|年增|季增|年減|年减|季減|季减|市佔率?|市占率?|出貨量|出货量|產能|产能|資本支出|资本支出"
    r"|revenue|target price|gross margin|operating margin|net income|earnings"
    r"|yield|growth|margin)"
    r"[^。\n]{0,12}?\d"
    # 通用回退：帶千分位或小數點的數字＝量測值，與語言無關。
    r"|(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)"        # 1,049,256 / 45,000
    r"|(?<!\d)(?!19\d\d|20\d\d)\d+\.\d+(?!\d)"  # 48.7（但 2026.07 之類的年份不算）
)


def is_numeric_claim(statement: str) -> bool:
    """主張是否含金融數值（供 numeric_support_rate 分母）。確定性、可單測。"""
    return bool(_NUMERIC_RE.search(statement or ""))


@dataclass(frozen=True)
class ClaimVerdict:
    text: str
    is_numeric: bool
    verdict: str  # "supported" | "unsupported" | "no_source"


# degraded_reason 的詞彙（`evaluation.degraded_reason`；只在 degraded=true 時有值）。
# degraded 只說「沒量到」，這裡說為什麼沒量到。監控與校準要分得出「judge 服務掛了／太慢」
# 與「judge 回了看不懂的東西」——前者是可用性問題，後者是量尺問題：
#   unavailable  LLMUnavailableError：API 錯誤（529 等，已重試）或進程沒吐任何字就結束
#   timeout      一個字都沒吐就逾時（LLMUnavailableError.reason == "timeout"）
#   truncated    吐到一半被逾時截斷（stream_completion 的 meta["truncated"]），剩下的不是完整 JSON
#   empty        回應是空的（只有空白）
#   parse        回應完整但不是 JSON
#   schema       JSON 合法但不合 schema v2（重試 1 次後），含 grounding 全部缺漏
#   content_risk 供應商內容審查拒答（HTTP 400 Content Exists Risk、finish_reason=content_filter；不重試）
#   account      帳號層級：金鑰無效或缺漏（401）、餘額不足（402）、模型或端點設定錯（404）。
#                每一次抽查都會踩到，不重試；告警走 `/healthz/llm`，這裡只記原因
#   error        其他例外，或注入的 judge 回 None
# HTTP 路徑（DeepSeek）的 kind 對應見 `_HTTP_DEGRADED`：截斷是 finish_reason=length（已以 2 倍上限重試），
# 不是逾時截斷，但同樣是「回應不完整」，沿用 truncated。
DEGRADED_UNAVAILABLE = "unavailable"
DEGRADED_TIMEOUT = "timeout"
DEGRADED_TRUNCATED = "truncated"
DEGRADED_EMPTY = "empty"
DEGRADED_PARSE = "parse"
DEGRADED_SCHEMA = "schema"
DEGRADED_CONTENT_RISK = "content_risk"
DEGRADED_ACCOUNT = "account"
DEGRADED_ERROR = "error"
DEGRADED_REASONS = frozenset({
    DEGRADED_UNAVAILABLE, DEGRADED_TIMEOUT, DEGRADED_TRUNCATED, DEGRADED_EMPTY,
    DEGRADED_PARSE, DEGRADED_SCHEMA, DEGRADED_CONTENT_RISK, DEGRADED_ACCOUNT, DEGRADED_ERROR,
})

_HTTP_DEGRADED = {
    llm_http.TIMEOUT: DEGRADED_TIMEOUT,
    llm_http.TRUNCATED: DEGRADED_TRUNCATED,
    llm_http.EMPTY: DEGRADED_EMPTY,
    llm_http.INVALID_JSON: DEGRADED_PARSE,
    llm_http.CONTENT_FILTER: DEGRADED_CONTENT_RISK,
    **{k: DEGRADED_ACCOUNT for k in llm_http.ACCOUNT_KINDS},
}


@dataclass
class FaithfulnessResult:
    """一次查核的結果。degraded=True 代表 fail-open（judge 異常），分數欄位皆 None。

    judge_model／degraded_reason／elapsed_ms 由 check_faithfulness 填入（量尺可追溯，
    DeepSeek 遷移 PR-07）；直接用 summarize_claims 組出來的結果這三個是 None。
    n_missing_verdicts：grounding 漏判（缺 idx）而被計為 unsupported 的條數，無缺漏時 0。
    judge_model_resp／judge_fingerprint／judge_requests／usage：只有 HTTP judge 有值（遷移 PR-18）——
    API 回報的實際模型與 `system_fingerprint`（同名模型換了底層就看得出來）、實際送出的請求數與
    token 加總（所有嘗試）。CLI judge 是 None。
    """

    faithfulness_score: float | None
    numeric_support_rate: float | None
    claims: list[ClaimVerdict] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    judge_model: str | None = None
    elapsed_ms: int | None = None
    n_missing_verdicts: int = 0
    judge_model_resp: str | None = None
    judge_fingerprint: str | None = None
    judge_requests: int | None = None
    usage: dict | None = None

    def to_evaluation(self, *, citation_coverage: float | None = None) -> dict:
        """落庫用 evaluation jsonb（qa_log.evaluation 的形狀）。

        judge_model 與 judge_schema_version 是讀分數時「只計現行 judge」的依據
        （app/services/judge_schema.py）；缺 judge_model 的舊列視為 claude-haiku-4-5。
        """
        return {
            "citation_coverage": citation_coverage,
            "numeric_support_rate": self.numeric_support_rate,
            "faithfulness_score": self.faithfulness_score,
            "claims": [
                {"text": c.text, "is_numeric": c.is_numeric, "verdict": c.verdict}
                for c in self.claims
            ],
            "note": "來源支持度／待複核，非真實性保證",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "judge_model": self.judge_model,
            "judge_schema_version": JUDGE_SCHEMA_VERSION,
            "elapsed_ms": self.elapsed_ms,
            # 漏判而被計為 unsupported 的條數：分數偏低時先看這個，分得出「真的沒佐證」
            # 與「judge 沒判完」（L19 的寬鬆例外留下的痕跡）。
            "n_missing_verdicts": self.n_missing_verdicts,
            # HTTP judge 的實際模型、指紋、請求數與 token（CLI judge 為 None）。
            "judge_model_resp": self.judge_model_resp,
            "judge_fingerprint": self.judge_fingerprint,
            "judge_requests": self.judge_requests,
            "usage": self.usage,
        }


# ── judge primitives（注入式，可假 judge 決定性單測）──


async def decompose_statements(text: str, *, judge) -> list[str] | None:
    """回答 → 原子主張清單。judge 自身失敗（回 None）→ None（fail-open，呼叫端標 degraded）。

    回應不合 schema v2（statements 不是 list[str]）重試 1 次後拋 JudgeSchemaError。
    """
    return await call_validated(judge, DECOMPOSE_SYS, text, parse_statements)


async def ground_statements(
    statements: list[str], contexts: list[str], *, judge, strict: bool = True
) -> dict[int, bool] | None:
    """逐條主張對 contexts 判 supported → {idx: bool}。judge 自身失敗 → None。

    schema v2：idx 集合必須恰好是 range(len(statements))，型別嚴格；不合格重試 1 次後拋
    JudgeSchemaError。strict=False 只放寬「缺 idx」一項——缺的不出現在回傳的 dict 裡，
    由呼叫端計為 unsupported（生產端，審查 L19），並記 WARNING；全部缺漏仍是 schema 錯。

    payload 格式與 eval 版本逐字一致，避免 eval 基準線漂移。
    """
    if not statements:
        return {}
    joined_ctx = "\n\n".join(contexts)
    enumerated = "\n".join(GROUND_ITEM_FMT.format(i=i, s=s) for i, s in enumerate(statements))
    payload = GROUND_PAYLOAD_FMT.format(contexts=joined_ctx, claims=enumerated)
    parsed = await call_validated(
        judge, GROUND_SYS, payload,
        lambda res: parse_verdicts(res, len(statements), "supported", allow_missing=not strict),
    )
    return None if parsed is None else parsed[0]


async def faithfulness(answer: str, contexts: list[str], *, judge) -> float | None:
    """eval 相容：拆解 → 逐條佐證於 contexts。supported/total；total==0 → None。

    離線端一律嚴格（缺 idx 也是 schema 錯）：JudgeSchemaError 往上拋，由 run_ragas 記成
    該指標的 judge_errors。
    """
    statements = await decompose_statements(answer, judge=judge)
    if not statements:
        return None
    supmap = await ground_statements(statements, contexts, judge=judge)
    if supmap is None:
        return None
    supported = sum(1 for ok in supmap.values() if ok)
    return supported / len(statements)


# ── 生產 judge（依 model 分派：DeepSeek 非串流 JSON／claude CLI 串流，fail-open）──

# 各階段的輸出上限（第二版計畫 §6.5）。judge 契約 `judge(system, user)` 分不出階段，所以由系統提示
# 對應（`JUDGE_MAX_TOKENS_BY_SYSTEM`，離線評測另補 CP 與反推問題兩支）。只作用在 HTTP 路徑，CLI 忽略。
# 截斷（finish_reason=length）時 `llm_http.complete_json` 以 2 倍上限重試 1 次。
DECOMPOSE_MAX_TOKENS = 8192
GROUND_MAX_TOKENS = 2048
# 對不上任何系統提示時（直接呼叫 judge 的工具）取最大的那個。
JUDGE_MAX_TOKENS = DECOMPOSE_MAX_TOKENS
JUDGE_MAX_TOKENS_BY_SYSTEM: dict[str, int] = {
    DECOMPOSE_SYS: DECOMPOSE_MAX_TOKENS,
    GROUND_SYS: GROUND_MAX_TOKENS,
}
# DeepSeek 的 `user_id`（內容安全與排程隔離；官方格式 [A-Za-z0-9_-]）。離線評測用 eval-judge。
JUDGE_USER_ID = "web-faithfulness"


@dataclass
class JudgeCallStats:
    """一次查核（或一批校準）裡 HTTP judge 呼叫的累計：請求數、token、API 回報的模型與指紋。"""

    requests: int = 0
    usage: dict = field(default_factory=dict)
    model_resp: str | None = None
    fingerprints: list[str] = field(default_factory=list)

    def add(self, res: llm_http.JsonResult) -> None:
        self.requests += res.attempts
        for k, v in res.usage.items():
            self.usage[k] = self.usage.get(k, 0) + v
        if res.outcome.model_resp:
            self.model_resp = res.outcome.model_resp
        fp = res.outcome.system_fingerprint
        if fp and fp not in self.fingerprints:
            self.fingerprints.append(fp)


async def _http_judge(
    system: str, user: str, *, model: str, timeout: float, max_tokens: int,
    failures: list[str] | None = None, stats: JudgeCallStats | None = None,
) -> dict | list | None:
    """DeepSeek judge：`llm_http.complete_json`（非串流、JSON 模式、t=0、thinking 關）。失敗回 None。

    請求數受本階段預算限制（`judge_schema.http_attempts_allowed`），送出後記帳；這就是 HTTP 路徑
    唯一的重試層（模組 docstring「重試層數」）。
    """
    allowed = http_attempts_allowed(llm_http.JSON_MAX_ATTEMPTS)
    if allowed <= 0:  # call_validated 在預算用完時不會再呼叫；防禦性保留
        if failures is not None:
            failures.append(DEGRADED_ERROR)
        return None
    res = await llm_http.complete_json(
        model, user, system=system, max_tokens=max_tokens, timeout=timeout,
        task="faithfulness", user_id=JUDGE_USER_ID, max_attempts=allowed,
    )
    record_http_requests(res.attempts)
    if stats is not None:
        stats.add(res)
    if res.kind is None:
        return res.data
    reason = _HTTP_DEGRADED.get(res.kind, DEGRADED_UNAVAILABLE)
    log = logger.error if reason == DEGRADED_ACCOUNT else logger.warning
    log("faithfulness judge failed: %s", res.error)
    if failures is not None:
        failures.append(reason)
    return None


async def _default_judge(
    system: str, user: str, *, model: str, timeout: float, failures: list[str] | None = None,
    max_tokens: int = JUDGE_MAX_TOKENS, stats: JudgeCallStats | None = None,
) -> dict | list | None:
    """預設 judge：白名單 model 走 `_http_judge`；其餘走 claude CLI（drain 串流 → parse_plan_json）。
    任何異常 → None（fail-open）。

    failures 給定時，把失敗原因（DEGRADED_* 詞彙）附加進去，供 check_faithfulness 記
    degraded_reason——回傳值維持 None，judge 契約不變。CLI 的逾時分兩種：沒吐字就逾時
    （LLMUnavailableError.reason＝timeout）記 timeout；吐到一半被截斷而解析失敗記 truncated，
    不歸成 parse（parse 留給「回應完整卻不是 JSON」，那才是量尺問題）。
    CLI 路徑維持呼叫模組層的 `stream_completion`（測試的 patch 點不變）。
    """
    if is_http_model(model):
        return await _http_judge(
            system, user, model=model, timeout=timeout, max_tokens=max_tokens, failures=failures, stats=stats,
        )

    def _fail(reason: str) -> None:
        if failures is not None:
            failures.append(reason)

    meta: dict = {}
    try:
        parts: list[str] = []
        async for chunk in stream_completion(
            user, model=model, system=system, timeout=timeout, allow_web=False, meta=meta,
            # CLI 忽略 max_tokens；逐階段上限只作用在 HTTP 路徑（上面的 _http_judge）。這裡維持常數，
            # tests/test_llm.py 的 CallSiteMaxTokensValuesTests 以模組命名空間求值它。
            max_tokens=JUDGE_MAX_TOKENS, task="faithfulness",
        ):
            parts.append(chunk)
        raw = "".join(parts).strip()
        if not raw:
            _fail(DEGRADED_EMPTY)
            return None
        return parse_plan_json(raw)  # {"statements":...} / {"verdicts":...} 皆物件
    except LLMUnavailableError as e:
        logger.exception("faithfulness judge failed")
        _fail(DEGRADED_TIMEOUT if e.reason == UNAVAILABLE_TIMEOUT else DEGRADED_UNAVAILABLE)
        return None
    except ValueError:
        logger.exception("faithfulness judge failed")
        _fail(DEGRADED_TRUNCATED if meta.get("truncated") else DEGRADED_PARSE)
        return None
    except Exception:
        logger.exception("faithfulness judge failed")
        _fail(DEGRADED_ERROR)
        return None


def make_judge(
    *, model: str, timeout: float, failures: list[str] | None = None,
    stats: JudgeCallStats | None = None, max_tokens_by_system: dict[str, int] | None = None,
):
    """組出符合 judge 契約 `judge(system, user)` 的預設 judge：依系統提示給該階段的 max_tokens。

    check_faithfulness 用它；校準工具（scripts/judge_agreement.py）用同一個，確保量的是生產那把尺。
    """
    table = JUDGE_MAX_TOKENS_BY_SYSTEM if max_tokens_by_system is None else max_tokens_by_system

    async def judge(system: str, user: str):
        return await _default_judge(
            system, user, model=model, timeout=timeout, failures=failures,
            max_tokens=table.get(system, JUDGE_MAX_TOKENS), stats=stats,
        )

    return judge


# ── M8 查核進入點 ──


async def check_faithfulness(
    text: str,
    context_texts: list[str],
    *,
    judge=None,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> FaithfulnessResult:
    """對 text 做 grounding 查核。context_texts＝已解析的證據文字（見 resolve_evidence_texts）。

    - judge 未提供 → 走 _default_judge（白名單 model 走 DeepSeek，其餘 claude CLI）。
    - decompose 失敗 → degraded（fail-open，不阻擋交付）。
    - context_texts 空（無可回查的證據）→ 全主張 no_source。
    - 有 context → ground；supported→supported，其餘→unsupported。
    numeric_support_rate 只計數值主張；無數值主張 → None。
    結果一律帶 judge_model（＝model）與 elapsed_ms；degraded 時另帶 degraded_reason。
    HTTP judge 另帶 judge_model_resp／judge_fingerprint／judge_requests／usage。
    """
    t0 = time.monotonic()
    failures: list[str] = []
    stats = JudgeCallStats()
    if judge is None:
        judge = make_judge(model=model, timeout=timeout, failures=failures, stats=stats)

    def _stamp(result: FaithfulnessResult) -> FaithfulnessResult:
        result.judge_model = model
        result.elapsed_ms = int((time.monotonic() - t0) * 1000)
        if result.degraded and result.degraded_reason is None:
            result.degraded_reason = failures[-1] if failures else DEGRADED_ERROR
        if stats.requests:
            result.judge_model_resp = stats.model_resp
            result.judge_fingerprint = ",".join(stats.fingerprints) or None
            result.judge_requests = stats.requests
            result.usage = dict(stats.usage)
        return result

    try:
        statements = await decompose_statements(text, judge=judge)
    except JudgeSchemaError:
        logger.warning("忠實度抽查：拆解結果不合 schema，標 degraded", exc_info=True)
        return _stamp(FaithfulnessResult(None, None, [], degraded=True, degraded_reason=DEGRADED_SCHEMA))
    if statements is None:
        return _stamp(FaithfulnessResult(None, None, [], degraded=True))
    if not statements:
        # 無事實主張（如「找不到資料」）：非降級，但無分可算
        return _stamp(FaithfulnessResult(None, None, [], degraded=False))

    has_context = any((c or "").strip() for c in context_texts)
    if not has_context:
        claims = [
            ClaimVerdict(s, is_numeric_claim(s), "no_source") for s in statements
        ]
        return _stamp(summarize_claims(claims, degraded=False))

    # strict=False：缺 idx 仍計為 unsupported（L19）；越界、重複、型別錯才算 schema 錯。
    try:
        supmap = await ground_statements(statements, context_texts, judge=judge, strict=False)
    except JudgeSchemaError:
        logger.warning("忠實度抽查：grounding 結果不合 schema，標 degraded", exc_info=True)
        return _stamp(FaithfulnessResult(None, None, [], degraded=True, degraded_reason=DEGRADED_SCHEMA))
    if supmap is None:
        return _stamp(FaithfulnessResult(None, None, [], degraded=True))

    claims = [
        ClaimVerdict(
            s,
            is_numeric_claim(s),
            "supported" if supmap.get(i) else "unsupported",
        )
        for i, s in enumerate(statements)
    ]
    result = summarize_claims(claims, degraded=False)
    # 缺 idx 的條數（全部缺漏已在 parse_verdicts 判為 schema 錯，走不到這裡）。
    result.n_missing_verdicts = len(statements) - len(supmap)
    return _stamp(result)


def summarize_claims(claims: list[ClaimVerdict], *, degraded: bool) -> FaithfulnessResult:
    """把逐條 verdict 彙總成 FaithfulnessResult（分數計算單一真相）。

    供研報「逐節查核→合併成 doc-level evaluation」復用（M8b）。
    """
    total = len(claims)
    supported = sum(1 for c in claims if c.verdict == "supported")
    numerics = [c for c in claims if c.is_numeric]
    num_supported = sum(1 for c in numerics if c.verdict == "supported")
    return FaithfulnessResult(
        faithfulness_score=(supported / total) if total else None,
        numeric_support_rate=(num_supported / len(numerics)) if numerics else None,
        claims=claims,
        degraded=degraded,
    )


# ── 證據回查（碰 DB；帳本只存身分，corpus 落 report_id 粒度）──

# 單筆 corpus 證據回查的字元上限：避免整份長研報灌爆 grounding payload。
_MAX_CHARS_PER_EVIDENCE = 4000


async def resolve_evidence_texts(
    ledger: EvidenceLedger,
    session,
    *,
    evidence_ids: list[str] | None = None,
    max_chars: int = _MAX_CHARS_PER_EVIDENCE,
) -> list[str]:
    """把帳本的 corpus 證據回查成文字（依 report_id 取該報告 chunk 串接，capped）。

    evidence_ids 給定時只回查那些證據（供研報逐節查核，只餵該節被分配的證據）；
    None 則回查整份帳本。external 證據的快照內容不在庫內（帳本只有 snapshot_ref/
    content_hash），故略過——無法在庫內驗證的外部數值主張，check_faithfulness 會因缺
    context 判 no_source。
    """
    if evidence_ids is None:
        candidates = list(ledger)
    else:
        candidates = [ev for eid in evidence_ids if (ev := ledger.get(eid)) is not None]
    seen_reports: set[str] = set()
    texts: list[str] = []
    for ev in candidates:
        if ev.kind != "corpus" or not ev.report_id or ev.report_id in seen_reports:
            continue
        seen_reports.add(ev.report_id)
        rows = (
            await session.execute(
                sql_text(
                    "SELECT content FROM research.report_chunk "
                    "WHERE report_id = :rid ORDER BY chunk_index"
                ),
                {"rid": ev.report_id},
            )
        ).all()
        joined = "\n".join(r[0] for r in rows if r[0])
        if joined.strip():
            texts.append(joined[:max_chars])
    return texts
