"""M1 eval 編排：讀題集 → 逐題 retrieve→generate→三指標 → aggregate → 寫基準線。

生成端刻意用 build_user_prompt + stream_completion（非 answer_question）以隔離檢索與
生成、避開 qa_log 寫入/overview/off-topic。有界併發（claude CLI spawn 吃 IO）、逐題
fail-open（任一階段異常記 error、不計均值、不中斷批次）。

M5 起：每 case 記 latency_ms（檢索＋生成牆鐘，排除 judge）；--agentic 走 agentic
迴圈評測（plan 並行→run_agentic 合併，記 rounds/subqueries_run/skipped 歸因欄位，
補查全部排同一個 rerank semaphore，故一律強制 concurrency=1）。

**量尺可追溯**（DeepSeek 遷移 PR-06）：
- 結果檔的 `config` 記錄生成端（`gen_model`）、每個會觸發 LLM 的任務實際用的 model
  （`models`）、judge 描述、git commit、題集 sha256、rerank／agentic 設定與起訖時間。
  config 只供 `eval_compare` 印差異，不參與判定。
- summary 帶三個 META 鍵：`judge_model`、`judge_prompt_sha`（`ragas_metrics.JUDGE_PROMPT_TEMPLATES`
  的雜湊）、`judge_schema_version`。兩份結果只要量尺不同、或只有一邊有記錄，`eval_compare`
  一律回 2。**所以在新基準線產出之前，拿新結果比 `eval/baselines/baseline-2026-09-02.json`
  （沒有這三個鍵）一律回 2**——那是預期，不是壞掉；要比就兩邊都用本版重跑。
- **judge 出錯只讓該指標記為 None**，不讓整題記為 error：錯誤訊息記在 case 的
  `judge_errors`，summary 的 `n_judge_errors` 計總數（只列出、不判方向：那是量尺故障，不是
  生成端劣化）。檢索或生成失敗才是整題 error。這樣一題 CP 的 judge 逾時不會連帶拿掉同一題的
  F 與 AR。但該指標的均值因此少一題，所以 summary 另記三個指標各自的入均值題數
  `n_effective_<指標>` 與題目集合的雜湊 `judged_ids_sha`（定義在 `scripts/eval_compare.py`，
  只有那一份）；兩邊不同時 eval_compare 回 2。處置是補跑到兩邊相同題目，或用
  `eval_compare --common-only` 只在兩邊都有值的題目上比，不是放寬比較器。
- `--repeat N` 的彙總規則：每題跑 N 次完整流程（檢索＋生成＋judge），**每題每指標跨 repeat
  取平均**（略過 None）；`n`＝題數（不乘 N），`n_errors`＝N 次全部失敗的題數，
  `n_no_context` 與 `n_judge_errors` 只在該指標 N 次都沒有值時計入。計數因此恆為整數，
  `eval_compare.sample_facts` 照舊可用。逐次結果留在 case 的 `runs`。
- `--dump-io DIR` 逐題（repeat 時逐次）寫出問題、脈絡、答案與每一次 judge 呼叫的回應，
  供之後凍結集與 judge 校準用。內容含研報全文，放 `data/eval_frozen/`（已 gitignore）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.config import get_settings  # noqa: E402
from app.services.agentic_qa import run_agentic  # noqa: E402
from app.services.answer import (  # noqa: E402
    ASK_ANSWER_MAX_TOKENS,
    ASK_DENSE_SCAN,
    ASK_RERANK_TIMEOUT,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.embed import MODEL_NAME as EMBED_MODEL  # noqa: E402
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.judge_schema import JUDGE_SCHEMA_VERSION, JudgeSchemaError  # noqa: E402
from app.services.llm import DEFAULT_MODEL, SEARCH_EVENT, LLMUnavailableError, stream_completion  # noqa: E402
from app.services.llm_models import is_http_model  # noqa: E402
from app.services.query_planner import plan_queries  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402
from app.services.scope_router import CORPUS_QA, POLICY_FOR_SCOPE, RouteDecision  # noqa: E402
from app.services.zh_hant import looks_simplified  # noqa: E402
from eval.judge import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_RETRIES,
    DEFAULT_JUDGE_TIMEOUT,
    JudgeError,
    judge_json,
)
from eval.ragas_metrics import (  # noqa: E402
    CTX_RELEVANCE_SYS,
    DECOMPOSE_SYS,
    GENQ_SYS,
    GROUND_SYS,
    answer_relevancy_detailed,
    context_precision,
    faithfulness,
    judge_prompt_sha,
)
from scripts.eval_compare import JUDGE_METRICS, judged_ids, judged_ids_sha  # noqa: E402

RETRIEVAL_PARAMS = {
    "k": RETRIEVAL_K,
    "dense_scan": ASK_DENSE_SCAN,
    "max_reports": MAX_REPORTS,
    "max_passages": MAX_PASSAGES_PER_REPORT,
    "max_chars": MAX_CONTEXT_CHARS,
}

_CTX_SPLIT_RE = re.compile(r"(?=^\[\d+\] )", re.MULTILINE)
# 與 app/services/answer.py 的 `_CITE_RE` 同一個樣式（那邊是模組私有名稱，不跨模組取用）。
_CITE_RE = re.compile(r"\[(\d+)\]")

# 生成端的逾時。沿用 stream_completion 的預設值（改它等於改被評的東西）。
# n_truncated 取自 stream_completion 的 `meta["truncated"]`：CLI 路徑逾時後對已吐出的文字
# fail-open、不拋例外，唯一的訊號是「成功的那次嘗試撞到了這個上限」（每次嘗試各自計時，
# 529 重試花掉的時間不算）。它只抓得到逾時截斷；輸出長度上限造成的截斷 CLI 看不到，
# PR-11 接 HTTP 後改用 finish_reason（length）。
GEN_TIMEOUT = 120.0

# judge 出錯的型別：只有這些讓「該指標」記 None（M8）。其他例外（程式錯誤、嵌入失敗）
# 仍讓整題記 error——那不是量尺的問題，吞掉會把 bug 藏成分數缺值。
# JudgeSchemaError＝JSON 合法但不合 schema v2（已重試 1 次，app/services/judge_schema.py）。
_JUDGE_FAILURES = (JudgeError, JudgeSchemaError, LLMUnavailableError)

# judge 系統提示 → 任務名，供 --dump-io 標記每一次 judge 呼叫屬於哪個指標。
_JUDGE_TASKS = {
    DECOMPOSE_SYS: "decompose",
    GROUND_SYS: "ground",
    CTX_RELEVANCE_SYS: "context_precision",
    GENQ_SYS: "answer_relevancy",
}

_METRICS = JUDGE_METRICS

# 題集已全標 corpus_qa（凍結題集），--agentic 固定注入此路由決策（設計 §7.2）。
_CORPUS_QA_DECISION = RouteDecision(
    scope=CORPUS_QA, tool_policy=POLICY_FOR_SCOPE[CORPUS_QA]
)

FAITHFULNESS_MIN = 0.9

# **刻意維持 0.8，不跟著 answer_relevancy 一起降。** 兩者的性質不同：
# AR 有結構性天花板（見下），CP 沒有——實測會達到 1.0（m2/m4 各有 2 題），
# 而反覆出現的 0.583 對應 rel=[0,1,1]，意思是「排第一的片段被判為不相關」。
# 那是真實的排序品質訊號，把門檻降到目前水準（0.68-0.78）等於把缺口粉飾掉。
# 換句話說：CP 沒過是**待辦事項**，不是校準錯誤。
#
# **2026-07-30 明確紀錄**，避免下一位讀者只盯著 AR：**從 M0 起沒通過的是 CP，
# 而且 AR 校準後（0.85 → 0.55，PR #137）CP 是三項裡唯一還卡著的**。逐份實測：
#     baseline-m0         F 0.945  CP 0.723  AR 0.608
#     baseline-m2         F 0.944  CP 0.769  AR 0.610  （n=8 名目，實際 7 題：一題 error）
#     m4-corpus-qa        F 0.931  CP 0.777  AR 0.636
#     baseline-2026-07-29 F 0.903  CP 0.679  AR 0.646  ← 首次乾淨量測（errors=0）
# 四份的 thresholds_pass 全 false；**以現行門檻重算，未達標項一律只有
# context_precision**（前三份的布林值是舊 AR 門檻 0.85 之下算的，別直接引用）。
# 07-29 那份 CP 從 0.777 掉到 0.679 有一部分是「先前虛高」——前三份都有 judge 逾時
# 掉題，掉的題不入均值。另注意 PR #140（gate_scores 量綱錯配）之後脈絡篇數回到 ~15，
# **預期會讓 CP 再降**（分母變大），那是修正的已知代價、不是新的回歸。
# 要判斷「這次改動有沒有讓 CP 變差」請用 `scripts/eval_compare.py` 比兩份結果，
# 不要拿絕對門檻當回歸訊號——它從來沒綠過，永遠紅等於沒有訊號。
CONTEXT_PRECISION_MIN = 0.8

# ⚠️ **舊門檻 0.85** 從 M0 到 M4 從未通過過，且與答案品質無關——2026-07-29 量測結論
# （下方數字全是 0.85 時代的診斷；現行的 0.55 自 baseline-2026-07-29 起實測通過，
# 詳見 CONTEXT_PRECISION_MIN 旁的四份對照）：
#
# answer_relevancy = 「由回答反推 3 個問題」與原問題的 BGE-M3 餘弦平均。
# GENQ_SYS 明文要求「問題須具體」，而題集的問題是廣義的（「台積電最新的營運展望如何」），
# 於是反推出的細節問題與原問題的餘弦**結構性地**落在 0.70 附近：
#
#   BGE-M3 尺度（實測，基準＝「台積電最新的營運展望如何」）
#     完全相同 1.0000 / 僅標點不同 0.9895 / 繁體改寫 0.9350 / 敘述句 0.9484
#     簡體同義 0.9174 / 帶 [n] 引用 0.9028
#     具體子問題（3 題平均）0.7197   ← 反推問題的實際樣態
#     不同公司同類問題 0.7276 / 完全無關 0.4909
#
#   真實資料（qa_log 三筆，judge 實跑）
#     舊提示 0.6953 / 0.7434 / 0.7821     對照組（無關問題）0.3571-0.4277
#
# 也就是說 0.85 只有在反推出「原問題的同義改寫」時才達得到，而那與 GENQ_SYS 的要求相反。
# 兩種提示詞修法皆經 A/B 量測**否決**：
#   (a) 改要求「反推使用者原始問題」→ +0.018 / +0.045 / **−0.167**（平均反而降）
#   (b) 加「與回答同語言」→ −0.005 / +0.016 / −0.002（修好語言漂移但分數不動）
# 主因是粒度而非語言，而粒度是 RAGAS 這個指標的設計本身。
#
# 指標本身沒壞，鑑別力充足——2026-07-29 乾淨基準（n=8、errors=0）：
#     切題（逐題 AR）      0.553 - 0.744，平均 0.646
#     切題（24 個逐題餘弦）0.498 - 0.777
#     無關對照組            0.357 - 0.428
# 兩個帶之間有清楚間隙（0.43 ↔ 0.55）。門檻取 **0.55**：高於所有實測的無關值、
# 低於所有實測的切題值，且對單次跑的抖動留了餘裕。
#
# 這是「明顯壞掉」的地板，**不是品質目標**——它答的是「檢索/生成有沒有崩掉」，
# 不是「答得好不好」。刻意不設在目前平均值附近（0.60+）：那會把此刻的品質當成
# 標準鎖死，且 n=8 的跑間抖動足以造成假警報。
ANSWER_RELEVANCY_MIN = 0.55


def split_contexts(context: str) -> list[str]:
    """把 build_context 的編號脈絡字串以 [n] 表頭切成每篇一塊。空 → []。"""
    if not context.strip():
        return []
    return [p.strip() for p in _CTX_SPLIT_RE.split(context) if p.strip()]


async def _generate_answer(
    question: str, context: str, *, model: str = DEFAULT_MODEL, timeout: float = GEN_TIMEOUT
) -> tuple[str, bool]:
    """以 build_user_prompt + stream_completion 生成答案（跳過 SEARCH_EVENT 控制標記）。

    回 (答案, 是否被逾時截斷)。截斷的判準見 GEN_TIMEOUT 旁的註解。
    """
    prompt = build_user_prompt(question, context)
    parts: list[str] = []
    meta: dict = {}
    async for chunk in stream_completion(
        prompt, system=SYSTEM_PROMPT, model=model, timeout=timeout, meta=meta,
        # 評測生成＝主答（同一個上限，改它等於改被評的東西）
        max_tokens=ASK_ANSWER_MAX_TOKENS, task="eval_answer",
    ):
        if chunk == SEARCH_EVENT:
            continue
        parts.append(chunk)
    return "".join(parts), bool(meta.get("truncated"))


def has_valid_citation(answer: str, n_contexts: int) -> bool:
    """答案是否至少引用一個存在的來源編號 [n]（1 ≤ n ≤ 脈絡篇數）。純函式。"""
    return any(1 <= int(m) <= n_contexts for m in _CITE_RE.findall(answer or ""))


def _recording_judge(judge, calls: list[dict]):
    """包一層 judge，把每一次呼叫的任務名與回應（或錯誤）記進 calls，供 --dump-io。"""

    async def _rec(system: str, user: str):
        task = _JUDGE_TASKS.get(system, "unknown")
        try:
            res = await judge(system, user)
        except Exception as e:
            calls.append({"task": task, "error": f"{type(e).__name__}: {e}"})
            raise
        calls.append({"task": task, "response": res})
        return res

    return _rec


async def eval_question(
    q: dict,
    *,
    judge,
    embed,
    retrieval_params: dict,
    agentic: bool = False,
    gen_model: str = DEFAULT_MODEL,
) -> dict:
    """單題：retrieve→generate→三指標。

    - 檢索或生成異常 → {..., "error": str}（整題 fail-open，不入任何均值）。
    - 某個指標的 judge 異常（JudgeError／JudgeSchemaError／LLMUnavailableError）→ 只有該指標為 None，
      錯誤記在 "judge_errors"（M8，見模組 docstring）。
    - 成功時另附私有鍵 "_io"（問題、脈絡、答案、judge 呼叫明細），由 run() 取走供
      --dump-io，不寫進結果檔。

    latency_ms 量測「檢索＋生成」牆鐘（agentic 時含規劃/評估/補查；排除 judge）。
    agentic=True 走 M5 流程：plan_queries（profile="qa"）與第一輪 retrieve_context
    並行 → run_agentic（忽略 stage 事件、取 outcome 的合併結果）→ 以合併 context
    生成，並記 rounds/subqueries_run/skipped 歸因欄位（補查是否被跳過可歸因）。
    """
    base = {"id": q.get("id"), "question": q.get("question")}
    try:
        question = q["question"]
        filters = q.get("filters") or {}
        t0 = time.monotonic()
        outcome = None
        if agentic:
            plan_task = asyncio.create_task(plan_queries(question, profile="qa"))
            try:
                sources, context = await retrieve_context(
                    question, filters=filters, **retrieval_params
                )
                plan = await plan_task  # plan_queries 永不 raise（失敗回 degraded 計畫）
            except BaseException:
                plan_task.cancel()
                raise
            async for kind, payload in run_agentic(
                question,
                plan=plan,
                decision=_CORPUS_QA_DECISION,
                first=(sources, context),
                filters=filters,
                # run_agentic 的補查 max_reports 由 qa_subquery_max_reports 決定，
                # 此處不傳 max_reports；rerank_timeout 沿用問答路徑常數。
                retrieval_params={
                    "k": retrieval_params.get("k", RETRIEVAL_K),
                    "dense_scan": retrieval_params.get("dense_scan", ASK_DENSE_SCAN),
                    "max_passages": retrieval_params.get(
                        "max_passages", MAX_PASSAGES_PER_REPORT
                    ),
                    "max_chars": retrieval_params.get("max_chars", MAX_CONTEXT_CHARS),
                    "rerank_top_m": retrieval_params.get("rerank_top_m", 0),
                    "rerank_timeout": ASK_RERANK_TIMEOUT,
                },
            ):
                if kind == "outcome":
                    outcome = payload
            if outcome is not None:
                sources, context = outcome.sources, outcome.context
        else:
            sources, context = await retrieve_context(
                question, filters=filters, **retrieval_params
            )
        contexts = split_contexts(context)
        answer, truncated = await _generate_answer(question, context, model=gen_model)
        latency_ms = int((time.monotonic() - t0) * 1000)

        calls: list[dict] = []
        rec_judge = _recording_judge(judge, calls)
        judge_errors: dict[str, str] = {}

        async def _metric(name: str, coro):
            try:
                return await coro
            except _JUDGE_FAILURES as e:
                judge_errors[name] = f"{type(e).__name__}: {e}"
                return None

        f = await _metric("faithfulness", faithfulness(answer, contexts, judge=rec_judge))
        cp = await _metric(
            "context_precision", context_precision(question, answer, contexts, judge=rec_judge)
        )
        ar_detail = await _metric(
            "answer_relevancy",
            answer_relevancy_detailed(question, answer, judge=rec_judge, embed=embed),
        )
        result = {
            **base,
            "faithfulness": f,
            "context_precision": cp,
            "answer_relevancy": ar_detail.score if ar_detail is not None else None,
            # 反推問題與逐題餘弦一併落庫：先前只存分數，導致「AR 為什麼是 0.64」
            # 必須重跑整份評測才答得出來（見 ANSWER_RELEVANCY_MIN 旁的量測紀錄）。
            "answer_relevancy_questions": list(ar_detail.questions) if ar_detail else [],
            "answer_relevancy_sims": [round(s, 4) for s in ar_detail.sims] if ar_detail else [],
            "n_contexts": len(contexts),
            "latency_ms": latency_ms,
            "cited": has_valid_citation(answer, len(contexts)),
            "simplified": looks_simplified(answer),
            "gen_truncated": truncated,
        }
        if judge_errors:
            result["judge_errors"] = judge_errors
        if outcome is not None:
            result["rounds"] = outcome.rounds
            result["subqueries_run"] = len(outcome.subqueries_run)
            result["skipped"] = outcome.skipped
        result["_io"] = {
            "question": question,
            "filters": filters,
            "context": context,
            "contexts": contexts,
            "answer": answer,
            "judge_calls": calls,
        }
        return result
    except Exception as e:  # noqa: BLE001 — 離線批次逐題 fail-open，不讓單題炸掉整批
        return {**base, "error": f"{type(e).__name__}: {e}"}


def _mean_of(per_q: list[dict], key: str) -> float | None:
    vals = [c[key] for c in per_q if "error" not in c and c.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _percentile(vals: list, pct: float):
    """最近秩（nearest-rank）百分位：取 sorted[ceil(pct/100*n)-1]。呼叫端保證非空。"""
    ordered = sorted(vals)
    idx = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[idx]


def aggregate(per_q: list[dict]) -> dict:
    """各指標均值（略過 error 與 None）、延遲分佈（mean/p50/p95，只算無 error 且有
    latency_ms 的 case——舊報表 case 無此欄）、計數、門檻旗標。純函式。

    n_no_context 只計「F 為 None 且不是 judge 出錯」的題：judge 出錯另計 n_judge_errors，
    混在一起會把量尺故障讀成檢索沒找到東西。
    citation_rate／simplified_residual_rate 是「答案帶有效引用」「答案整份判為簡體」的
    題數比例（分母＝有該欄位的非 error 題）；n_truncated 是生成被逾時截斷的題數。
    n_effective_<指標>／judged_ids_sha：三個 judge 指標各自實際入均值的題數與題目集合的雜湊
    （eval_compare 據此判定兩份是不是在同一組題目上算的均值）。
    """
    f = _mean_of(per_q, "faithfulness")
    cp = _mean_of(per_q, "context_precision")
    ar = _mean_of(per_q, "answer_relevancy")
    ok = [c for c in per_q if "error" not in c]
    n_errors = len(per_q) - len(ok)
    n_no_context = sum(
        1
        for c in ok
        if c.get("faithfulness") is None and "faithfulness" not in (c.get("judge_errors") or {})
    )
    n_judge_errors = sum(len(c.get("judge_errors") or {}) for c in ok)
    n_truncated = sum(1 for c in ok if c.get("gen_truncated"))
    # 逐項記錄哪一個沒過。先前只有一個布林，紅了還得自己去比對三個數字才知道
    # 卡在哪——而三份 baseline 全 False、卻沒人看出來全部都是 answer_relevancy
    # 一項造成的，正是因為這裡不說話（2026-07-29 診斷）。
    _checks = (
        ("faithfulness", f, FAITHFULNESS_MIN),
        ("context_precision", cp, CONTEXT_PRECISION_MIN),
        ("answer_relevancy", ar, ANSWER_RELEVANCY_MIN),
    )
    thresholds_failed = [
        name for name, val, floor in _checks if val is None or not val > floor
    ]
    thresholds_pass = not thresholds_failed
    latencies = [
        c["latency_ms"]
        for c in per_q
        if "error" not in c and c.get("latency_ms") is not None
    ]
    return {
        "faithfulness": f,
        "context_precision": cp,
        "answer_relevancy": ar,
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p50": _percentile(latencies, 50) if latencies else None,
        "latency_ms_p95": _percentile(latencies, 95) if latencies else None,
        "n": len(per_q),
        "n_errors": n_errors,
        "n_no_context": n_no_context,
        "n_judge_errors": n_judge_errors,
        **{f"n_effective_{m}": len(judged_ids(per_q, m)) for m in JUDGE_METRICS},
        "judged_ids_sha": judged_ids_sha(per_q),
        "citation_rate": _mean_of(per_q, "cited"),
        "simplified_residual_rate": _mean_of(per_q, "simplified"),
        "n_truncated": n_truncated,
        "thresholds_pass": thresholds_pass,
        "thresholds_failed": thresholds_failed,
    }


def merge_repeats(runs: list[dict]) -> dict:
    """同一題 N 次的結果 → 一筆 case（--repeat 的彙總規則，見模組 docstring）。純函式。

    - 全部 N 次都 error → 整題 error（取最後一次的訊息）。
    - 三指標、cited、simplified：跨成功的那幾次取平均，略過 None（布林當 0/1）。
      某指標沒有任何值、且至少一次是 judge 出錯 → 記進 judge_errors（取最後一則）。
    - latency_ms 取平均後四捨五入成整數；gen_truncated 任一次截斷即為真。
    - 逐次原始結果留在 runs（已去掉私有的 _io）。
    """
    base = {"id": runs[0].get("id"), "question": runs[0].get("question")}
    clean = [{k: v for k, v in r.items() if k != "_io"} for r in runs]
    ok = [r for r in clean if "error" not in r]
    if not ok:
        return {**base, "error": clean[-1]["error"], "n_runs": len(clean), "n_runs_ok": 0, "runs": clean}

    def _avg(key: str):
        vals = [float(r[key]) for r in ok if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    merged: dict = {**base}
    judge_errors: dict[str, str] = {}
    for key in _METRICS:
        merged[key] = _avg(key)
        if merged[key] is None:
            errs = [r["judge_errors"][key] for r in ok if key in (r.get("judge_errors") or {})]
            if errs:
                judge_errors[key] = errs[-1]
    merged["n_contexts"] = ok[0].get("n_contexts")
    latency = _avg("latency_ms")
    merged["latency_ms"] = round(latency) if latency is not None else None
    merged["cited"] = _avg("cited")
    merged["simplified"] = _avg("simplified")
    merged["gen_truncated"] = any(r.get("gen_truncated") for r in ok)
    if judge_errors:
        merged["judge_errors"] = judge_errors
    merged["n_runs"] = len(clean)
    merged["n_runs_ok"] = len(ok)
    merged["runs"] = clean
    return merged


def _git_commit() -> str | None:
    """目前的 commit（工作樹有未提交的追蹤檔改動時加 `-dirty`）。取不到回 None。"""
    root = Path(__file__).resolve().parents[1]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10
        )
        if head.returncode != 0:
            return None
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"], cwd=root, capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = head.stdout.strip()
    return f"{commit}-dirty" if dirty.returncode == 1 else commit


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def judge_provider(model: str) -> str:
    """judge 實際走哪個 backend：與 `stream_completion` 同一份白名單分派（`is_http_model`）。"""
    return "deepseek_http" if is_http_model(model) else "claude_cli"


def uncalibrated_judge_warning(model: str) -> str | None:
    """judge 指定為白名單模型時的警告文字；Claude judge 回 None。

    PR-26（judge 切 DeepSeek）之前 judge 校準尚未完成，這種結果屬於新的量尺系譜：與既有
    Claude judge 的結果比，`eval_compare` 因 `judge_model` 不同回 2。不阻擋——校準本身就要
    這樣跑。TODO(PR-26)：校準完成、judge 正式切換後刪掉這個警告。
    """
    if judge_provider(model) != "deepseek_http":
        return None
    return (
        f"WARNING：judge={model} 走 DeepSeek，judge 校準（PR-26）尚未完成。"
        "本次分數屬於新的量尺系譜，與 Claude judge 的基準線不可比（eval_compare 回 2）；"
        "不要拿它升格基準線或判定劣化。"
    )


def _warn_uncalibrated_judge(model: str) -> None:
    msg = uncalibrated_judge_warning(model)
    if msg:
        bar = "!" * 72
        print(f"{bar}\n{msg}\n{bar}", file=sys.stderr, flush=True)


def build_config(
    *,
    dataset_path,
    generator_model: str,
    judge_model: str,
    concurrency: int,
    repeat: int,
    rerank_top_m: int,
    scope: str | None,
    limit: int | None,
    agentic: bool,
    commit: str | None,
    started_at: str,
    finished_at: str | None = None,
) -> dict:
    """結果檔的 config 快照：只供 eval_compare 印差異，不參與判定（不放進 summary，
    否則每個新鍵都會觸發退出碼 3）。

    models 列出本次會觸發 LLM 的**每一個任務**實際用的 model——生成、四支 judge，以及
    agentic 時的規劃與評估步（兩者都走 `settings.qa_planner_model`）。檢索、rerank、嵌入
    都不呼叫 LLM。
    """
    models = {
        "generate": generator_model,
        "judge_decompose": judge_model,
        "judge_ground": judge_model,
        "judge_context_precision": judge_model,
        "judge_answer_relevancy": judge_model,
    }
    settings = get_settings()
    if agentic:
        models["agentic_plan"] = settings.qa_planner_model
        models["agentic_evaluate"] = settings.qa_planner_model
    return {
        "gen_model": generator_model,
        "gen_timeout": GEN_TIMEOUT,
        "models": models,
        "judge": {
            "provider": judge_provider(judge_model),
            "model": judge_model,
            "timeout": DEFAULT_JUDGE_TIMEOUT,
            "retries": DEFAULT_JUDGE_RETRIES,
            "prompt_sha": judge_prompt_sha(),
            "schema_version": JUDGE_SCHEMA_VERSION,
        },
        "embed_model": EMBED_MODEL,
        "rerank_top_m": rerank_top_m,
        "rerank_model": settings.rerank_model if rerank_top_m > 0 else None,
        "agentic": agentic,
        "concurrency": concurrency,
        "repeat": repeat,
        "queryset": str(dataset_path),
        "queryset_sha256": _sha256_file(dataset_path),
        "scope": scope,
        "limit": limit,
        "commit": commit,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _dump_name(case_id, run_idx: int, repeat: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(case_id)) or "case"
    return f"{safe}-r{run_idx + 1}.json" if repeat > 1 else f"{safe}.json"


def dump_io(
    dump_dir, run: dict, io: dict, *, run_idx: int, repeat: int, gen_model: str,
    judge_model: str, commit: str | None,
) -> Path:
    """把一次成功的 eval_question 的輸入輸出寫成一個 JSON 檔（--dump-io）。

    sha256 取自 (question, context, answer) 的正規化 JSON：凍結集之後被重評時，
    用它確認評的是同一份輸入。
    """
    out_dir = Path(dump_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        json.dumps(
            [io["question"], io["context"], io["answer"]], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    doc = {
        "id": run.get("id"),
        "run": run_idx + 1,
        **io,
        "scores": {k: run.get(k) for k in _METRICS},
        "judge_errors": run.get("judge_errors") or {},
        "gen_truncated": run.get("gen_truncated"),
        "gen_model": gen_model,
        "judge_model": judge_model,
        "judge_prompt_sha": judge_prompt_sha(),
        "judge_schema_version": JUDGE_SCHEMA_VERSION,
        "commit": commit,
        "ts": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
    }
    path = out_dir / _dump_name(run.get("id"), run_idx, repeat)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def run(
    dataset_path,
    *,
    out_path,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    concurrency: int = 3,
    rerank_top_m: int = 0,
    scope: str | None = None,
    agentic: bool = False,
    generator_model: str = DEFAULT_MODEL,
    repeat: int = 1,
    dump_dir=None,
) -> dict:
    """讀題集 → 有界併發 eval_question → aggregate → 寫報表（{summary, config, cases}）。

    rerank_top_m>0 時檢索走 M2 cross-encoder 重排（rerank-on 基準線），=0 為 rerank-off。
    scope 指定時只跑該 scope 的題目（未標 scope 的題目視為 corpus_qa）。
    agentic=True 一律強制 concurrency=1：補查全部排同一個 rerank semaphore
    （workers=1、逾時含排隊），併發會使 deadline 非決定性跳過補查、CP 混入隨機性。
    repeat>1 時每題跑 repeat 次，以 merge_repeats 彙總；dump_dir 給定時逐次寫出輸入輸出。
    """
    if repeat < 1:
        raise ValueError("repeat 必須 ≥ 1")
    _warn_uncalibrated_judge(judge_model)  # 開跑前先說：一輪評測要跑好幾個小時
    started_at = datetime.now(timezone.utc).isoformat()
    commit = _git_commit()
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = dataset.get("questions", [])
    if scope:
        questions = [q for q in questions if q.get("scope", "corpus_qa") == scope]
    if limit is not None:
        questions = questions[:limit]

    if agentic:
        concurrency = 1

    retrieval_params = {**RETRIEVAL_PARAMS, "rerank_top_m": rerank_top_m}

    async def _judge(system: str, user: str):
        return await judge_json(user, system=system, model=judge_model)

    sem = asyncio.Semaphore(concurrency)

    async def _one(q: dict) -> dict:
        async with sem:  # 限制同時 spawn 的 claude CLI 數，避開 IO 風暴
            return await eval_question(
                q,
                judge=_judge,
                embed=embed_query_cached,
                retrieval_params=retrieval_params,
                agentic=agentic,
                gen_model=generator_model,
            )

    flat = await asyncio.gather(*[_one(q) for q in questions for _ in range(repeat)])
    cases: list[dict] = []
    for qi in range(len(questions)):
        runs = list(flat[qi * repeat : (qi + 1) * repeat])
        for k, r in enumerate(runs):
            io = r.pop("_io", None)
            if dump_dir is not None and io is not None:
                dump_io(
                    dump_dir, r, io, run_idx=k, repeat=repeat, gen_model=generator_model,
                    judge_model=judge_model, commit=commit,
                )
        cases.append(runs[0] if repeat == 1 else merge_repeats(runs))

    summary = aggregate(cases)
    # 量尺三個 META 鍵：不同即不可比、只有一邊有也不可比（scripts/eval_compare.py）。
    summary["judge_model"] = judge_model
    summary["judge_prompt_sha"] = judge_prompt_sha()
    summary["judge_schema_version"] = JUDGE_SCHEMA_VERSION
    config = build_config(
        dataset_path=dataset_path,
        generator_model=generator_model,
        judge_model=judge_model,
        concurrency=concurrency,
        repeat=repeat,
        rerank_top_m=rerank_top_m,
        scope=scope,
        limit=limit,
        agentic=agentic,
        commit=commit,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    report = {"summary": summary, "config": config, "cases": cases}
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def _print_summary(report: dict) -> None:
    s = report["summary"]

    def fmt(v):
        return f"{v:.3f}" if isinstance(v, float) else "n/a"

    print("=== M1 RAGAS 基準線 ===")
    print(f"Faithfulness      : {fmt(s['faithfulness'])}  (門檻 > {FAITHFULNESS_MIN})")
    print(f"Context Precision : {fmt(s['context_precision'])}  (門檻 > {CONTEXT_PRECISION_MIN})")
    print(f"Answer Relevancy  : {fmt(s['answer_relevancy'])}  (門檻 > {ANSWER_RELEVANCY_MIN})")
    if s.get("latency_ms_mean") is not None:
        print(
            f"latency_ms        : mean={s['latency_ms_mean']:.0f}  "
            f"p50={s['latency_ms_p50']:.0f}  p95={s['latency_ms_p95']:.0f}"
        )
    print(
        f"n={s['n']}  errors={s['n_errors']}  no_context={s['n_no_context']}  "
        f"judge_errors={s.get('n_judge_errors', 0)}  truncated={s.get('n_truncated', 0)}"
    )
    print(f"citation_rate     : {fmt(s.get('citation_rate'))}   "
          f"simplified_residual_rate: {fmt(s.get('simplified_residual_rate'))}")
    print(f"judge             : {s.get('judge_model')}  schema v{s.get('judge_schema_version')}  "
          f"prompt {str(s.get('judge_prompt_sha'))[:12]}")
    failed = s.get("thresholds_failed") or []
    print(f"thresholds_pass   : {s['thresholds_pass']}"
          + (f"   未達標：{'、'.join(failed)}" if failed else ""))
    _warn_uncalibrated_judge(str(s.get("judge_model") or ""))  # 看結果的人不一定看過開頭


def _main() -> None:
    parser = argparse.ArgumentParser(description="跑 M1 RAGAS reference-free 評測")
    parser.add_argument("--dataset", default="eval/ragas_questions.json")
    # 預設刻意不在 eval/baselines/ 底下：舊預設 baseline-m0.json 會讓「忘了帶 --out」
    # 直接覆寫一份歷史基準線。要升格成基準線時由人複製並命名。
    parser.add_argument("--out", default="eval/candidate-ragas.json")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--generator-model", default=DEFAULT_MODEL,
                        help=f"生成端 model（預設 {DEFAULT_MODEL}，與問答主答相同）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每題重跑次數；>1 時每題每指標跨次取平均（彙總規則見模組 docstring）")
    parser.add_argument("--dump-io", default=None, metavar="DIR",
                        help="逐題寫出問題／脈絡／答案／judge 回應（建議 data/eval_frozen/<名稱>）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--rerank-top-m",
        type=int,
        default=0,
        help="檢索重排候選上限（>0 走 M2 cross-encoder 重排，0=off）",
    )
    parser.add_argument("--scope", default=None,
                        help="只跑指定 scope 的題目（如 corpus_qa）；未標 scope 的題目視為 corpus_qa")
    parser.add_argument("--agentic", action="store_true",
                        help="走 M5 agentic 迴圈評測（強制 concurrency=1）")
    parser.add_argument("--json", action="store_true", help="改輸出完整 JSON 到 stdout")
    args = parser.parse_args()
    # 本次會用到的模型：生成端、judge；agentic 另有查詢規劃與證據評估（QA_PLANNER_MODEL）。
    require_llm_key(
        [args.generator_model, args.judge_model] + ([get_settings().qa_planner_model] if args.agentic else []),
    )

    report = asyncio.run(
        run(
            args.dataset,
            out_path=args.out,
            judge_model=args.judge_model,
            limit=args.limit,
            concurrency=args.concurrency,
            rerank_top_m=args.rerank_top_m,
            scope=args.scope,
            agentic=args.agentic,
            generator_model=args.generator_model,
            repeat=args.repeat,
            dump_dir=args.dump_io,
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
