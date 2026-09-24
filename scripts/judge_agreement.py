"""judge 描述性校準：拿 `qa_log` 裡歷史的 haiku 判定當參考，量 DeepSeek judge 差多少（DeepSeek 遷移 PR-18）。

**只描述、不是閘門**（計畫第四版 D-J a）：claude CLI 已於 2026-09-23 放棄，沒有 Claude 對照組可以
重跑，judge 照樣切成 DeepSeek、開新的量尺系譜。這支把「同一批問答、兩把尺」整理成比較表給人看，
程式不判 pass／fail、不改任何門檻（F>0.9／CP>0.8／AR>0.55、`FAITHFULNESS_MIN` 都是政策）。

## 做什麼

1. **唯讀取樣**（`SET TRANSACTION READ ONLY`，經 `SessionFactory`，長查詢 `relax_statement_timeout`）：
   近 `--days` 天、每串對話的**首輪**、有未降級分數、`evaluation.judge_model` 缺值或
   `claude-haiku-4-5`（`judge_schema.JUDGE_MODEL_SQL`）的列。低於門檻與邊界帶（<0.95）優先，
   但至少保留三分之一名額給高分帶，否則翻轉率只看得到一個方向。排序以 `md5(id)` 決定，可重現。
   取回後再篩一輪（`screen_rows`，被排除的列數與原因印在報告與結果檔的 `run.excluded`）：
   - **`--since` 之前的列排除**（`before_since`）。2026-09-02 之前的問答抽查不是對生成時的脈絡判，
     而是從證據帳本回查、比的是整篇研報的**前 4000 字**（`answer._faithfulness_spot_check` docstring；
     2026-08-21 實測、PR #233 於 2026-09-02 09:44 合併）。那些 verdict 與「重建的生成脈絡」不是同一件事，
     拿來比只會把帳本回查的偏差算成 judge 差異。合併當天何時重啟生效不可考，預設取次日 2026-09-03。
   - **haiku 分數重算**：`evaluation.faithfulness_score` 是 supported／全部，分母含 `no_source`
     （脈絡為空時的主張），而 DeepSeek 這邊脈絡為空的題直接略過、不會有 `no_source`——直接拿存的分數
     比，偏移與翻轉率會系統性偏向「DeepSeek 比較寬鬆」。所以 haiku 分數只用 supported／unsupported
     重算（`haiku_score`），全是 `no_source` 的列排除（`all_no_source`），沒有可判主張的列排除
     （`no_graded_claims`）；重算後與存的分數不同的列數記在 `rescored`。帶別也以重算後的分數分。
2. **重建脈絡**：`retrieve_context`（零 LLM；本機嵌入＋rerank），篩選只取檢索認得的鍵
   （`RETRIEVAL_FILTER_KEYS`，路由遙測鍵剔除）。只取首輪：續問的改寫查詢與 agentic 補查脈絡
   都沒落庫，重建不了。
3. **兩種比較**（都用生產那把尺：`faithfulness.make_judge`／`check_faithfulness`，同一個模型、逾時、
   max_tokens、重試預算）：
   - **E 模式**（端到端）：DeepSeek 自己拆解＋grounding → 分數，對歷史 haiku 分數算平均偏移、
     門檻旗標翻轉率、逐題分差、主張數比。
   - **G 模式**（固定主張）：拿 haiku 當年拆出的主張（`evaluation.claims`，排除 no_source），只讓
     DeepSeek 做 grounding → 逐條 verdict 對 haiku 的 verdict 算 Cohen's κ、寬鬆率、嚴格率。
     把「拆解粒度」與「判準」分開看。
4. 依計畫第四版 §判準的**方向**取 CI 端點（只標出，不判定）：κ 取下界、|偏移| 取上界、翻轉率取
   Wilson 上界、門檻旗標取點估計。CI 是以題為單位的 bootstrap（同一題的主張彼此相關，逐條重抽會
   低估變異）。

**已知的偏差（解讀時要記得）**：脈絡是重建的，不是當年生成時看到的那一份——語料與檢索參數都可能
變了。所以差異混著「judge 不同」與「脈絡不同」兩個來源，haiku 當年的 verdict 也是對舊脈絡判的。
這正是它只能當描述、不能當閘門的原因。開網搜的問答（`filters.web`）答案裡有網搜來源的主張，
脈絡裡本來就沒有，分層另列。

## 花費與安全

會呼叫付費 API：每題最多 3 個階段（E 的拆解、grounding，G 的 grounding），每個階段最多 3 個請求
（`judge_schema.HTTP_STAGE_MAX_REQUESTS`）。`--max-cases`（預設 60）限題數，`--max-cny`（預設 15）
限花費：每題開跑前以「已花實額＋下一題的**最壞**估算（每階段都用滿請求上限、每個請求輸出都到 2 倍
`max_tokens`，`worst_case_cost`；取它與已完成題平均的較大者）」檢查，會超過就停。已花實額取 API 回報的
usage；單題出例外時拿不到 usage，以該題最壞估算計入。字元換 token 是估的，所以上限仍是軟性的：
**最多超出約一題的花費**。
單價 `--price-in`／`--price-out`（CNY／百萬 token）預設刻意偏高：9/24 探測 420 次呼叫高峰價約
US$0.761（輸入 236 萬、輸出 9.1 萬 token），依此反推約輸入 2.2、輸出 8.7 CNY／百萬 token，
預設取約 2 倍（4、16），加上最壞估算，預算實務上會提早停。`--dry-run` 只取樣與估價，不呼叫 LLM、
不載嵌入模型、不要求金鑰。

**不取 `scripts/_claude_lock.py` 的 flock**，理由同 `eval/run_ragas.py`：那把鎖防的是批次之間
重複付費處理同一批研報、摘錄的 DELETE+INSERT 互撞、以及以「同時只有一支批次」為前提算的連線數。
這支只讀 `qa_log`、不寫任何表、逐題循序執行（一條連線），三者都不適用；DeepSeek 也沒有 CLI 時代
「互搶同一個子行程」的問題。仍建議離峰執行：它會在本行程載入 BGE-M3（與 sync 的入庫嵌入搶記憶體）。

## 輸出

終端印比較表；完整結果（逐題分數、verdict、統計、參數）寫進 `--out`（預設
`data/judge_agreement/<UTC 時間>.json`，已 gitignore）。檔內只有 qa_id 與分數，不含問題、答案或脈絡。
單題出例外（檢索或 judge）不中止整支：記進 `run.errors`（qa_id、階段、例外），照常跑下一題；只有帳號層級
錯誤（401／402／404）整批中止。

    uv run python scripts/judge_agreement.py --dry-run
    uv run python scripts/judge_agreement.py --max-cases 60 --max-cny 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.config import get_settings  # noqa: E402
from app.services.answer import (  # noqa: E402
    ASK_DENSE_SCAN,
    ASK_RERANK_TIMEOUT,
    ASK_RERANK_TOP_M,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
)
from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.faithfulness import (  # noqa: E402
    DECOMPOSE_SYS,
    DEGRADED_ACCOUNT,
    DEGRADED_ERROR,
    DEGRADED_SCHEMA,
    GROUND_SYS,
    JUDGE_MAX_TOKENS_BY_SYSTEM,
    JudgeCallStats,
    check_faithfulness,
    ground_statements,
    make_judge,
)
from app.services.judge_schema import (  # noqa: E402
    HTTP_STAGE_MAX_REQUESTS,
    JUDGE_MODEL_SQL,
    LEGACY_JUDGE_MODEL,
    JudgeSchemaError,
)
from app.services.llm_models import is_http_model  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RC_CONFIG = 2

# 邊界帶：分數在 [FAITHFULNESS_MIN, EDGE) 的題，換尺最容易翻轉，與低分帶一起優先取樣。
EDGE = 0.95
# 高分帶至少保留的名額比例（見模組 docstring 第 1 點）。
ABOVE_SHARE = 1 / 3
# 估算用：1 個中文字約 0.6 token（第二版計畫 §7 的經驗值），取 0.7 偏保守。
TOKENS_PER_CHAR = 0.7
# 每條主張的 grounding 輸出約這麼多 token（`{"idx": 12, "supported": true}` 加分隔）。
TOKENS_PER_VERDICT = 20
DEFAULT_PRICE_IN = 4.0
DEFAULT_PRICE_OUT = 16.0
# `--since` 的預設（見模組 docstring 第 1 點）：PR #233（抽查改對生成時的脈絡判）2026-09-02 合併，取次日。
DEFAULT_SINCE = date(2026, 9, 3)
# 日期以台北時間的午夜為界（生產與使用者都在 UTC+8）。
_TPE = timezone(timedelta(hours=8))

# hybrid_search 認得的篩選鍵（app/services/retrieval.py）。qa_log.filters 另有路由遙測
# （path、decided_by、web、llm_model、llm_truncated、llm_error…），不能原樣傳進檢索。
RETRIEVAL_FILTER_KEYS = ("market", "instrument_type", "relates_stock", "relates_futures", "report_type")

# 計畫第四版 §判準的 judge 列（只當參考線印出，不判定）。flip 的參考線沒有 Claude 對照組，
# flip_CC 無從得知，取 max(flip_CC, 0.05)+0.05 在 flip_CC≤0.05 時的值。
REF_KAPPA_MIN = 0.60
REF_ABS_SHIFT_MAX = 0.03
REF_FLIP_MAX = 0.10

# `evaluation` 未加前綴：turns 只有 id 與 turn 兩欄，不會與 qa_log 的欄位混淆，JUDGE_MODEL_SQL 可原樣嵌入。
_SAMPLE_SQL = f"""
WITH turns AS (
  SELECT id, row_number() OVER (PARTITION BY COALESCE(conversation_id, id) ORDER BY created_at) AS turn
  FROM research.qa_log
  WHERE active
)
SELECT q.id, q.created_at, q.question, q.answer, q.filters, q.evaluation,
       (q.evaluation->>'faithfulness_score')::float AS stored_score
FROM research.qa_log q JOIN turns t ON t.id = q.id
WHERE t.turn = 1
  AND q.active AND q.stopped IS NOT TRUE AND q.answer IS NOT NULL
  AND q.created_at > now() - make_interval(days => :days)
  AND jsonb_typeof(q.evaluation->'faithfulness_score') = 'number'
  AND q.evaluation->'degraded' IS DISTINCT FROM 'true'::jsonb
  AND jsonb_typeof(q.evaluation->'claims') = 'array'
  AND ({JUDGE_MODEL_SQL}) = :legacy
ORDER BY md5(q.id::text)
"""


# ── 取樣與脈絡（純函式）─────────────────────────────────────────────────────


def band_of(score: float, fmin: float) -> str:
    if score < fmin:
        return "below"
    if score < EDGE:
        return "edge"
    return "above"


def pick_cases(rows: Sequence[dict], max_cases: int, fmin: float) -> list[dict]:
    """低分與邊界帶優先，但高分帶至少保留 ABOVE_SHARE 的名額（不夠就讓給另外兩帶）。保留輸入順序。"""
    low = [r for r in rows if band_of(r["score"], fmin) != "above"]
    high = [r for r in rows if band_of(r["score"], fmin) == "above"]
    reserve = min(len(high), int(max_cases * ABOVE_SHARE))
    chosen = low[: max_cases - reserve]
    chosen += high[: max_cases - len(chosen)]
    order = {id(r): i for i, r in enumerate(rows)}
    return sorted(chosen, key=lambda r: order[id(r)])


def retrieval_filters(filters) -> dict:
    if not isinstance(filters, dict):
        return {}
    return {k: filters[k] for k in RETRIEVAL_FILTER_KEYS if filters.get(k) is not None}


def haiku_score(evaluation) -> float | None:
    """haiku 的分數，只用 supported／unsupported 重算（排除 no_source 與畸形條目）；沒有可判主張回 None。

    不用存的 `faithfulness_score`：它的分母含 no_source（見模組 docstring 第 1 點）。
    """
    _texts, verdicts = haiku_claims(evaluation)
    return sum(verdicts) / len(verdicts) if verdicts else None


def _aware(ts) -> datetime | None:
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def screen_rows(rows: Sequence[dict], since: date) -> tuple[list[dict], dict[str, int]]:
    """取回的列 → (可比的列, 被排除的列數依原因)。可比的列帶重算後的 `score`（見 `haiku_score`）。

    原因：`before_since`（`since` 台北午夜之前，或沒有 created_at）、`all_no_source`、`no_graded_claims`；
    另記 `rescored`（重算後與存的分數不同、仍保留的列數，不是排除原因）。保留輸入順序。
    """
    cutoff = datetime.combine(since, datetime.min.time(), tzinfo=_TPE)
    kept: list[dict] = []
    excluded: dict[str, int] = {}

    def _count(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for r in rows:
        created = _aware(r.get("created_at"))
        if created is None or created < cutoff:
            _count("before_since")
            continue
        score = haiku_score(r.get("evaluation"))
        if score is None:
            claims = (r.get("evaluation") or {}).get("claims") or []
            no_source = [c for c in claims if isinstance(c, dict) and c.get("verdict") == "no_source"]
            _count("all_no_source" if no_source else "no_graded_claims")
            continue
        stored = r.get("stored_score")
        if stored is None or not math.isclose(float(stored), score):
            _count("rescored")
        kept.append({**r, "score": score})
    return kept, excluded


EXCLUDE_REASONS = {
    "before_since": "--since 之前（帳本前 4000 字的舊抽查）",
    "all_no_source": "全是 no_source（當年脈絡為空）",
    "no_graded_claims": "沒有 supported／unsupported 主張",
}


def _fmt_excluded(excluded: dict[str, int]) -> str:
    parts = [f"{EXCLUDE_REASONS[k]} {v}" for k, v in excluded.items() if k in EXCLUDE_REASONS]
    text = "、".join(parts) or "無"
    if excluded.get("rescored"):
        text += f"；另有 {excluded['rescored']} 列保留但重算了 haiku 分數（排除 no_source）"
    return text


def haiku_claims(evaluation) -> tuple[list[str], list[bool]]:
    """歷史 evaluation 的主張與 verdict（True＝supported）。no_source 與畸形條目剔除。"""
    texts: list[str] = []
    verdicts: list[bool] = []
    for c in (evaluation or {}).get("claims") or []:
        if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not c["text"].strip():
            continue
        if c.get("verdict") not in ("supported", "unsupported"):
            continue
        texts.append(c["text"])
        verdicts.append(c["verdict"] == "supported")
    return texts, verdicts


# ── 花費（純函式）───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Prices:
    """CNY／百萬 token。快取命中一律照未命中計（保守）。"""

    input: float = DEFAULT_PRICE_IN
    output: float = DEFAULT_PRICE_OUT


def usage_cost(usage: dict | None, prices: Prices) -> float:
    u = usage or {}
    return (u.get("prompt_tokens", 0) * prices.input + u.get("completion_tokens", 0) * prices.output) / 1e6


def estimate_case_cost(answer_chars: int, context_chars: int, claims_chars: int, n_claims: int,
                       prices: Prices) -> float:
    """一題的估算花費（E 的拆解＋grounding、G 的 grounding；不含重試）。"""
    t = TOKENS_PER_CHAR
    tin = t * (len(DECOMPOSE_SYS) + answer_chars)                       # E 拆解
    tin += t * (len(GROUND_SYS) + context_chars + answer_chars)          # E grounding（主張≈答案長度）
    tin += t * (len(GROUND_SYS) + context_chars + claims_chars)          # G grounding
    tout = t * answer_chars + 2 * TOKENS_PER_VERDICT * max(n_claims, 1)
    return (tin * prices.input + tout * prices.output) / 1e6


def worst_case_cost(answer_chars: int, context_chars: int, claims_chars: int, prices: Prices) -> float:
    """一題的最壞花費：每個階段都用滿 `HTTP_STAGE_MAX_REQUESTS` 個請求，每個請求的輸出都到 2 倍該階段
    `max_tokens`（截斷重試的上限；schema 重試從原上限起算，所以這是上界）。預算檢查用它（審查低7）。"""
    t = TOKENS_PER_CHAR
    reqs = HTTP_STAGE_MAX_REQUESTS
    stages = (
        (t * (len(DECOMPOSE_SYS) + answer_chars), JUDGE_MAX_TOKENS_BY_SYSTEM[DECOMPOSE_SYS]),       # E 拆解
        (t * (len(GROUND_SYS) + context_chars + answer_chars), JUDGE_MAX_TOKENS_BY_SYSTEM[GROUND_SYS]),  # E grounding
        (t * (len(GROUND_SYS) + context_chars + claims_chars), JUDGE_MAX_TOKENS_BY_SYSTEM[GROUND_SYS]),  # G grounding
    )
    return sum(reqs * (tin * prices.input + 2 * max_out * prices.output) for tin, max_out in stages) / 1e6


# ── 統計（純函式）───────────────────────────────────────────────────────────


def cohen_kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """二元 Cohen's κ。沒有資料或期望一致率為 1（兩邊都全同一類）時回 None。"""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def percentile(sorted_vals: Sequence[float], q: float) -> float:
    """線性內插百分位（q∈[0,1]），輸入須已排序且非空。"""
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def bootstrap_ci(items: Sequence, stat: Callable[[Sequence], float | None], *, n_boot: int, seed: int,
                 alpha: float = 0.05) -> tuple[float, float] | None:
    """以 item（題）為單位重抽的百分位 bootstrap CI；統計量回 None 的重抽略過。資料不足回 None。"""
    if len(items) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        sample = [items[rng.randrange(len(items))] for _ in items]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < max(10, n_boot // 10):
        return None
    vals.sort()
    return percentile(vals, alpha / 2), percentile(vals, 1 - alpha / 2)


def wilson_upper(k: int, n: int, z: float = 1.96) -> float | None:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / denom)


@dataclass
class CaseResult:
    qa_id: str
    band: str
    web: bool
    h_score: float
    h_verdicts: list[bool] = field(default_factory=list)
    d_score: float | None = None          # E 模式
    d_n_claims: int | None = None
    d_degraded: str | None = None
    g_verdicts: list[bool | None] = field(default_factory=list)  # G 模式；None＝漏判
    g_degraded: str | None = None
    context_chars: int = 0
    cost_cny: float = 0.0
    fingerprints: list[str] = field(default_factory=list)
    model_resp: str | None = None

    @property
    def g_score(self) -> float | None:
        if self.g_degraded or not self.g_verdicts:
            return None
        return sum(1 for v in self.g_verdicts if v) / len(self.g_verdicts)


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _shift_block(pairs: list[tuple[float, float]], *, n_boot: int, seed: int) -> dict:
    """[(DeepSeek, haiku)] → 平均偏移與 CI；|偏移| 取 CI 上界（§判準的方向）。"""
    diffs = [d - h for d, h in pairs]
    ci = bootstrap_ci(diffs, _mean, n_boot=n_boot, seed=seed)
    return {
        "n": len(diffs),
        "mean_shift": _mean(diffs),
        "shift_ci": list(ci) if ci else None,
        "abs_shift_ci_upper": max(abs(ci[0]), abs(ci[1])) if ci else None,
    }


def summarize(cases: Sequence[CaseResult], *, fmin: float, n_boot: int = 2000, seed: int = 20260924) -> dict:
    """比較表（只描述）。E 模式看分數與門檻旗標，G 模式看逐條 verdict。"""
    e = [c for c in cases if c.d_score is not None]
    e_pairs = [(c.d_score, c.h_score) for c in e]
    flips_h_only = sum(1 for c in e if c.h_score < fmin <= c.d_score)   # haiku 待複核、DeepSeek 不是
    flips_d_only = sum(1 for c in e if c.d_score < fmin <= c.h_score)   # DeepSeek 待複核、haiku 不是
    flips = flips_h_only + flips_d_only
    ratios = [c.d_n_claims / len(c.h_verdicts) for c in e if c.d_n_claims is not None and c.h_verdicts]

    g = [c for c in cases if c.g_score is not None]

    def _pairs(cs: Sequence[CaseResult]) -> list[tuple[bool, bool]]:
        return [(d, h) for c in cs for d, h in zip(c.g_verdicts, c.h_verdicts) if d is not None]

    g_pairs = _pairs(g)
    kappa_ci = bootstrap_ci(g, lambda cs: cohen_kappa(_pairs(cs)), n_boot=n_boot, seed=seed)
    h_unsup = [(d, h) for d, h in g_pairs if not h]
    h_sup = [(d, h) for d, h in g_pairs if h]

    by_web = {}
    for web in (False, True):
        sub = [(c.d_score, c.h_score) for c in e if c.web is web]
        by_web["web" if web else "no_web"] = {"n": len(sub), "mean_shift": _mean([d - h for d, h in sub])}

    degraded: dict[str, int] = {}
    for c in cases:
        for reason in (c.d_degraded, c.g_degraded):
            if reason:
                degraded[reason] = degraded.get(reason, 0) + 1

    return {
        "n_cases": len(cases),
        "bands": {b: sum(1 for c in cases if c.band == b) for b in ("below", "edge", "above")},
        "degraded": degraded,
        "e_mode": {
            **_shift_block(e_pairs, n_boot=n_boot, seed=seed),
            "flag_flips": flips,
            "flag_flip_rate": flips / len(e) if e else None,
            "flag_flip_wilson_upper": wilson_upper(flips, len(e)),
            "haiku_flag_only": flips_h_only,
            "deepseek_flag_only": flips_d_only,
            "claims_ratio_median": statistics.median(ratios) if ratios else None,
            "by_web": by_web,
        },
        "g_mode": {
            "n_cases": len(g),
            "n_verdicts": len(g_pairs),
            "raw_agreement": _mean([1.0 if d == h else 0.0 for d, h in g_pairs]),
            "kappa": cohen_kappa(g_pairs),
            "kappa_ci": list(kappa_ci) if kappa_ci else None,
            "kappa_ci_lower": kappa_ci[0] if kappa_ci else None,
            # 寬鬆率：haiku 判不支持、DeepSeek 判支持；嚴格率反之。
            "lenient_rate": _mean([1.0 if d else 0.0 for d, _ in h_unsup]),
            "strict_rate": _mean([0.0 if d else 1.0 for d, _ in h_sup]),
            **{f"score_{k}": v for k, v in _shift_block(
                [(c.g_score, c.h_score) for c in g], n_boot=n_boot, seed=seed + 1).items()},
        },
        "reference": {
            "note": "計畫第四版 §判準的參考線，只描述、不判定（D-J a：judge 照切、新系譜）",
            "kappa_ci_lower_min": REF_KAPPA_MIN,
            "abs_shift_ci_upper_max": REF_ABS_SHIFT_MAX,
            "flag_flip_wilson_upper_max": REF_FLIP_MAX,
            "flag_flips_point": 0,
        },
    }


# ── 執行 ─────────────────────────────────────────────────────────────────────


class AccountError(Exception):
    """judge 帳號層級錯誤（401／402／404）：每一題都會失敗，整批中止。"""


Retrieve = Callable[[str, dict], Awaitable[str]]


async def default_retrieve(question: str, filters: dict) -> str:
    _sources, context = await retrieve_context(
        question, k=RETRIEVAL_K, dense_scan=ASK_DENSE_SCAN, max_reports=MAX_REPORTS,
        max_passages=MAX_PASSAGES_PER_REPORT, max_chars=MAX_CONTEXT_CHARS, filters=filters,
        rerank_top_m=ASK_RERANK_TOP_M, rerank_timeout=ASK_RERANK_TIMEOUT,
    )
    return context


async def evaluate_case(row: dict, context: str, *, model: str, timeout: float, fmin: float,
                        prices: Prices, check=check_faithfulness, ground=ground_statements) -> CaseResult:
    texts, h_verdicts = haiku_claims(row["evaluation"])
    filters = row.get("filters") if isinstance(row.get("filters"), dict) else {}
    case = CaseResult(
        qa_id=str(row["id"]), band=band_of(row["score"], fmin), web=bool(filters.get("web")),
        h_score=float(row["score"]), h_verdicts=h_verdicts, context_chars=len(context),
    )

    # E 模式：生產同一個入口（拆解＋grounding）。
    res = await check(row["answer"], [context], model=model, timeout=timeout)
    case.cost_cny += usage_cost(res.usage, prices)
    if res.judge_fingerprint:
        case.fingerprints += [f for f in res.judge_fingerprint.split(",") if f not in case.fingerprints]
    case.model_resp = res.judge_model_resp or case.model_resp
    if res.degraded:
        case.d_degraded = res.degraded_reason or DEGRADED_ERROR
    elif res.faithfulness_score is not None:
        case.d_score = res.faithfulness_score
        case.d_n_claims = len(res.claims)
    if case.d_degraded == DEGRADED_ACCOUNT:
        raise AccountError(f"qa_id={case.qa_id} E 模式：judge 帳號層級錯誤")

    # G 模式：固定 haiku 的主張，只做 grounding（生產那把尺：strict=False、同一個 judge 組法）。
    if texts:
        failures: list[str] = []
        stats = JudgeCallStats()
        judge = make_judge(model=model, timeout=timeout, failures=failures, stats=stats)
        try:
            supmap = await ground(texts, [context], judge=judge, strict=False)
        except JudgeSchemaError:
            supmap, case.g_degraded = None, DEGRADED_SCHEMA
        case.cost_cny += usage_cost(stats.usage, prices)
        case.fingerprints += [f for f in stats.fingerprints if f not in case.fingerprints]
        case.model_resp = stats.model_resp or case.model_resp
        if supmap is None:
            case.g_degraded = case.g_degraded or (failures[-1] if failures else DEGRADED_ERROR)
        else:
            case.g_verdicts = [supmap.get(i) for i in range(len(texts))]
        if case.g_degraded == DEGRADED_ACCOUNT:
            raise AccountError(f"qa_id={case.qa_id} G 模式：judge 帳號層級錯誤")
    return case


async def run_cases(rows: Sequence[dict], *, model: str, timeout: float, fmin: float, prices: Prices,
                    max_cny: float, retrieve: Retrieve = default_retrieve, check=check_faithfulness,
                    ground=ground_statements, log=print) -> tuple[list[CaseResult], dict]:
    """逐題循序：重建脈絡 → E＋G。每題開跑前以最壞估算檢查預算；帳號錯誤整批中止（AccountError 往外拋）。

    單題的其他例外（檢索或 judge）記進 `errors`、照常跑下一題：一題的怪輸入不該讓前面已付費的結果
    全部作廢。judge 階段出例外時拿不到 usage，以該題最壞估算計入已花費（寧可提早停）。
    """
    results: list[CaseResult] = []
    skipped: dict[str, int] = {}
    errors: list[dict] = []
    spent = 0.0
    stop_reason = None
    for i, row in enumerate(rows, 1):
        qa_id = str(row.get("id"))
        filters = retrieval_filters(row.get("filters"))
        try:
            context = await retrieve(row["question"], filters)
        except Exception as exc:  # noqa: BLE001 — 單題 fail-open，見 docstring
            errors.append({"qa_id": qa_id, "stage": "retrieve", "error": f"{type(exc).__name__}: {exc}"})
            log(f"[{i}/{len(rows)}] {qa_id[:8]} 重建脈絡失敗：{type(exc).__name__}: {exc}")
            continue
        if not (context or "").strip():
            skipped["no_context"] = skipped.get("no_context", 0) + 1
            continue
        texts, _ = haiku_claims(row["evaluation"])
        worst = worst_case_cost(len(row["answer"]), len(context), sum(map(len, texts)), prices)
        avg = spent / len(results) if results else 0.0
        if spent + max(worst, avg) > max_cny:
            stop_reason = (f"預算上限：已花約 ¥{spent:.3f}，下一題最壞估 ¥{max(worst, avg):.3f}，"
                           f"上限 ¥{max_cny:g}")
            break
        try:
            case = await evaluate_case(row, context, model=model, timeout=timeout, fmin=fmin, prices=prices,
                                       check=check, ground=ground)
        except AccountError:
            raise
        except Exception as exc:  # noqa: BLE001 — 單題 fail-open，見 docstring
            spent += worst
            errors.append({"qa_id": qa_id, "stage": "judge", "error": f"{type(exc).__name__}: {exc}"})
            log(f"[{i}/{len(rows)}] {qa_id[:8]} judge 例外（以最壞估算 ¥{worst:.3f} 計入）："
                f"{type(exc).__name__}: {exc}")
            continue
        spent += case.cost_cny
        results.append(case)
        log(f"[{i}/{len(rows)}] {case.qa_id[:8]} {case.band:<5} haiku={case.h_score:.3f} "
            f"E={_fmt(case.d_score)} G={_fmt(case.g_score)}"
            + (f" E降級={case.d_degraded}" if case.d_degraded else "")
            + (f" G降級={case.g_degraded}" if case.g_degraded else "")
            + f" 累計約 ¥{spent:.3f}")
    return results, {"spent_cny_est": round(spent, 4), "skipped": skipped, "errors": errors,
                     "stop_reason": stop_reason}


def _fmt(v: float | None, spec: str = ".3f") -> str:
    return "—" if v is None else format(v, spec)


def print_report(summary: dict, run_meta: dict, *, model: str, fmin: float) -> None:
    e, g, ref = summary["e_mode"], summary["g_mode"], summary["reference"]
    print("\n=== judge 描述性校準（參考＝歷史 haiku 判定；只描述、不判定）===")
    print(f"judge={model}  門檻 FAITHFULNESS_MIN={fmin}  題數 {summary['n_cases']}  帶別 {summary['bands']}"
          f"  降級 {summary['degraded'] or '無'}")
    print(f"花費估計 ¥{run_meta['spent_cny_est']}  略過 {run_meta['skipped'] or '無'}"
          + (f"  提前停止：{run_meta['stop_reason']}" if run_meta.get("stop_reason") else ""))
    print(f"取樣前排除：{_fmt_excluded(run_meta.get('excluded') or {})}（haiku 分數只計 supported／unsupported）")
    errors = run_meta.get("errors") or []
    if errors:
        by_stage: dict[str, int] = {}
        for err in errors:
            by_stage[err["stage"]] = by_stage.get(err["stage"], 0) + 1
        print(f"單題例外 {len(errors)} 題（{by_stage}，未列入比較；明細在結果檔 run.errors）")
    print(f"API 回報 model={run_meta.get('model_resp')}  system_fingerprint={run_meta.get('fingerprints')}")
    print("\n[E 模式] DeepSeek 端到端分數 − haiku 歷史分數")
    ci = e["shift_ci"]
    print(f"  n={e['n']}  平均偏移 {_fmt(e['mean_shift'], '+.3f')}  95% CI "
          f"{'—' if not ci else f'[{ci[0]:+.3f}, {ci[1]:+.3f}]'}  |偏移| CI 上界 {_fmt(e['abs_shift_ci_upper'])}"
          f"（參考 ≤{ref['abs_shift_ci_upper_max']}）")
    print(f"  門檻旗標翻轉 {e['flag_flips']}（haiku 待複核而 DeepSeek 否 {e['haiku_flag_only']}、反向 "
          f"{e['deepseek_flag_only']}）  翻轉率 {_fmt(e['flag_flip_rate'])}  Wilson 上界 "
          f"{_fmt(e['flag_flip_wilson_upper'])}（參考 ≤{ref['flag_flip_wilson_upper_max']}；點估計參考 0）")
    print(f"  主張數比（DeepSeek／haiku）中位數 {_fmt(e['claims_ratio_median'], '.2f')}  分層 {e['by_web']}")
    print("\n[G 模式] 固定 haiku 主張，只比 grounding verdict")
    print(f"  題 {g['n_cases']}、verdict {g['n_verdicts']}  原始一致率 {_fmt(g['raw_agreement'])}  κ {_fmt(g['kappa'])}"
          f"  κ CI 下界 {_fmt(g['kappa_ci_lower'])}（參考 ≥{ref['kappa_ci_lower_min']}）")
    print(f"  寬鬆率 P(D 支持|H 不支持) {_fmt(g['lenient_rate'])}  嚴格率 P(D 不支持|H 支持) {_fmt(g['strict_rate'])}"
          f"  分數平均偏移 {_fmt(g['score_mean_shift'], '+.3f')}")
    print("\n注意：脈絡是以 retrieve_context 重建的，不是當年生成時的那一份；差異同時來自 judge 與脈絡。")


def _default_out() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "data" / "judge_agreement" / f"judge_agreement-{ts}.json"


async def _fetch_rows(days: int, fmin: float) -> list[dict]:
    async with SessionFactory() as session:
        # 交易的第一句必須是 SET TRANSACTION；之後的 SET LOCAL 只活到交易結束。
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await relax_statement_timeout(session)
        result = await session.execute(text(_SAMPLE_SQL), {"days": days, "legacy": LEGACY_JUDGE_MODEL})
        rows = [dict(r) for r in result.mappings().all()]
        await session.rollback()
    return rows


def _main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser(description="judge 描述性校準：DeepSeek judge 對 qa_log 歷史 haiku 判定（只描述）")
    ap.add_argument("--model", default=settings.faithfulness_model,
                    help="受測 judge（預設 FAITHFULNESS_MODEL；必須是 DeepSeek 白名單模型）")
    ap.add_argument("--days", type=int, default=120, help="取樣窗期（天）")
    ap.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE,
                    help=f"只取這天（台北時間）起的抽查（預設 {DEFAULT_SINCE}；之前的抽查比的是帳本前 4000 字）")
    ap.add_argument("--max-cases", type=int, default=60)
    ap.add_argument("--max-cny", type=float, default=15.0,
                    help="花費上限（CNY，保守單價、下一題以最壞估算檢查；字元換 token 是估的，最多超出約一題的花費）")
    ap.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN, help="輸入單價 CNY／百萬 token")
    ap.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT, help="輸出單價 CNY／百萬 token")
    ap.add_argument("--timeout", type=float, default=settings.ask_faithfulness_timeout,
                    help="每次 judge 呼叫的總期限（預設 ASK_FAITHFULNESS_TIMEOUT，與生產同一把尺）")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--out", default=None, help="結果 JSON（預設 data/judge_agreement/<UTC 時間>.json）")
    ap.add_argument("--dry-run", action="store_true", help="只取樣與估價，不呼叫 LLM、不載嵌入模型")
    args = ap.parse_args()

    if not is_http_model(args.model):
        print(f"--model={args.model} 不是 DeepSeek 白名單模型；這支只校準 DeepSeek judge", file=sys.stderr)
        raise SystemExit(RC_CONFIG)
    if not args.dry_run:
        require_llm_key([args.model])

    fmin = settings.faithfulness_min
    prices = Prices(args.price_in, args.price_out)
    screened, excluded = screen_rows(asyncio.run(_fetch_rows(args.days, fmin)), args.since)
    rows = pick_cases(screened, args.max_cases, fmin)
    bands = {b: sum(1 for r in rows if band_of(r["score"], fmin) == b) for b in ("below", "edge", "above")}
    print(f"取樣前排除：{_fmt_excluded(excluded)}")
    if args.dry_run:
        est = sum(
            estimate_case_cost(len(r["answer"]), MAX_CONTEXT_CHARS, sum(map(len, haiku_claims(r["evaluation"])[0])),
                               len(haiku_claims(r["evaluation"])[0]), prices)
            for r in rows
        )
        print(f"dry-run：取樣 {len(rows)} 題（{bands}），估計約 ¥{est:.2f}"
              f"（脈絡以 MAX_CONTEXT_CHARS={MAX_CONTEXT_CHARS} 字計、不含重試），預算上限 ¥{args.max_cny:g}")
        return
    if not rows:
        print(f"沒有符合條件的歷史 haiku 判定（{args.since} 起、窗期內首輪、未降級、有可判主張）", file=sys.stderr)
        raise SystemExit(1)

    try:
        cases, run_meta = asyncio.run(run_cases(
            rows, model=args.model, timeout=args.timeout, fmin=fmin, prices=prices, max_cny=args.max_cny,
        ))
    except AccountError as exc:
        print(f"整批中止：{exc}（儲值／金鑰見 docs/production_resilience.md）", file=sys.stderr)
        raise SystemExit(RC_CONFIG) from None
    run_meta["excluded"] = excluded
    run_meta["fingerprints"] = sorted({f for c in cases for f in c.fingerprints})
    run_meta["model_resp"] = next((c.model_resp for c in cases if c.model_resp), None)
    summary = summarize(cases, fmin=fmin, n_boot=args.bootstrap, seed=args.seed)
    print_report(summary, run_meta, model=args.model, fmin=fmin)

    out = Path(args.out) if args.out else _default_out()
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"model": args.model, "days": args.days, "since": args.since.isoformat(),
                   "max_cases": args.max_cases, "max_cny": args.max_cny,
                   "prices_cny_per_mtok": asdict(prices), "timeout": args.timeout, "fmin": fmin,
                   "bootstrap": args.bootstrap, "seed": args.seed, "reference_judge": LEGACY_JUDGE_MODEL},
        "run": run_meta,
        "summary": summary,
        "cases": [{**asdict(c), "g_score": c.g_score} for c in cases],
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n完整結果 → {out}")


if __name__ == "__main__":
    _main()
