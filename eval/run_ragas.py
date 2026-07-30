"""M1 eval 編排：讀題集 → 逐題 retrieve→generate→三指標 → aggregate → 寫基準線。

生成端刻意用 build_user_prompt + stream_completion（非 answer_question）以隔離檢索與
生成、避開 qa_log 寫入/overview/off-topic。有界併發（claude CLI spawn 吃 IO）、逐題
fail-open（任一階段異常記 error、不計均值、不中斷批次）。

M5 起：每 case 記 latency_ms（檢索＋生成牆鐘，排除 judge）；--agentic 走 agentic
迴圈評測（plan 並行→run_agentic 合併，記 rounds/subqueries_run/skipped 歸因欄位，
補查全部排同一個 rerank semaphore，故一律強制 concurrency=1）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agentic_qa import run_agentic  # noqa: E402
from app.services.answer import (  # noqa: E402
    ASK_DENSE_SCAN,
    ASK_RERANK_TIMEOUT,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.llm import DEFAULT_MODEL, SEARCH_EVENT, stream_completion  # noqa: E402
from app.services.query_planner import plan_queries  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402
from app.services.scope_router import CORPUS_QA, POLICY_FOR_SCOPE, RouteDecision  # noqa: E402
from eval.judge import DEFAULT_JUDGE_MODEL, judge_json  # noqa: E402
from eval.ragas_metrics import (  # noqa: E402
    answer_relevancy_detailed,
    context_precision,
    faithfulness,
)

RETRIEVAL_PARAMS = {
    "k": RETRIEVAL_K,
    "dense_scan": ASK_DENSE_SCAN,
    "max_reports": MAX_REPORTS,
    "max_passages": MAX_PASSAGES_PER_REPORT,
    "max_chars": MAX_CONTEXT_CHARS,
}

_CTX_SPLIT_RE = re.compile(r"(?=^\[\d+\] )", re.MULTILINE)

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


async def _generate_answer(question: str, context: str) -> str:
    """以 build_user_prompt + stream_completion 生成答案（跳過 SEARCH_EVENT 控制標記）。"""
    prompt = build_user_prompt(question, context)
    parts: list[str] = []
    async for chunk in stream_completion(prompt, system=SYSTEM_PROMPT, model=DEFAULT_MODEL):
        if chunk == SEARCH_EVENT:
            continue
        parts.append(chunk)
    return "".join(parts)


async def eval_question(
    q: dict, *, judge, embed, retrieval_params: dict, agentic: bool = False
) -> dict:
    """單題：retrieve→generate→三指標。任一階段異常 → {..., "error": str}（fail-open）。

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
        answer = await _generate_answer(question, context)
        latency_ms = int((time.monotonic() - t0) * 1000)
        f = await faithfulness(answer, contexts, judge=judge)
        cp = await context_precision(question, answer, contexts, judge=judge)
        ar_detail = await answer_relevancy_detailed(
            question, answer, judge=judge, embed=embed
        )
        result = {
            **base,
            "faithfulness": f,
            "context_precision": cp,
            "answer_relevancy": ar_detail.score,
            # 反推問題與逐題餘弦一併落庫：先前只存分數，導致「AR 為什麼是 0.64」
            # 必須重跑整份評測才答得出來（見 ANSWER_RELEVANCY_MIN 旁的量測紀錄）。
            "answer_relevancy_questions": list(ar_detail.questions),
            "answer_relevancy_sims": [round(s, 4) for s in ar_detail.sims],
            "n_contexts": len(contexts),
            "latency_ms": latency_ms,
        }
        if outcome is not None:
            result["rounds"] = outcome.rounds
            result["subqueries_run"] = len(outcome.subqueries_run)
            result["skipped"] = outcome.skipped
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
    latency_ms 的 case——舊報表 case 無此欄）、計數、門檻旗標。純函式。"""
    f = _mean_of(per_q, "faithfulness")
    cp = _mean_of(per_q, "context_precision")
    ar = _mean_of(per_q, "answer_relevancy")
    n_errors = sum(1 for c in per_q if "error" in c)
    n_no_context = sum(
        1 for c in per_q if "error" not in c and c.get("faithfulness") is None
    )
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
        "thresholds_pass": thresholds_pass,
        "thresholds_failed": thresholds_failed,
    }


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
) -> dict:
    """讀題集 → 有界併發 eval_question → aggregate → 寫報表（{summary, cases}）。

    rerank_top_m>0 時檢索走 M2 cross-encoder 重排（rerank-on 基準線），=0 為 rerank-off。
    scope 指定時只跑該 scope 的題目（未標 scope 的題目視為 corpus_qa）。
    agentic=True 一律強制 concurrency=1：補查全部排同一個 rerank semaphore
    （workers=1、逾時含排隊），併發會使 deadline 非決定性跳過補查、CP 混入隨機性。
    """
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
            )

    cases = await asyncio.gather(*[_one(q) for q in questions])
    summary = aggregate(list(cases))
    report = {"summary": summary, "cases": list(cases)}
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
    print(f"n={s['n']}  errors={s['n_errors']}  no_context={s['n_no_context']}")
    failed = s.get("thresholds_failed") or []
    print(f"thresholds_pass   : {s['thresholds_pass']}"
          + (f"   未達標：{'、'.join(failed)}" if failed else ""))


def _main() -> None:
    parser = argparse.ArgumentParser(description="跑 M1 RAGAS reference-free 評測")
    parser.add_argument("--dataset", default="eval/ragas_questions.json")
    parser.add_argument("--out", default="eval/baselines/baseline-m0.json")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
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
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
