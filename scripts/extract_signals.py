"""觀點雷達訊號擷取批次（子集先行、冪等可續傳）→ research.report_signal

流程（對齊 scripts/generate_summaries.py 的 asyncio + Semaphore + `run_claude` 慣例）：
1. 依券商覆蓋度選「高覆蓋標的」子集（每個 (market, code) 的券商數/報告數達門檻，取 top-N）。
2. 撈涵蓋子集標的的研報；每份 requested_codes = report.stock_targets ∩ 該 market 子集。
3. checkpoint-resume：某報告的所有 requested 標的皆已有 valid/partial 列且版本相符 → 跳過。
4. 逐報告呼叫 LLM（DeepSeek）依固定 schema 擷取；Python 正規化（signal_extract）。
5. ON CONFLICT upsert；單筆失敗只寫 data/signal_failures.log，不中斷、不影響檢索/問答。
6. 有回應卻全數 rejected 的研報記入 research.llm_task_failure，同一 model 連續 3 輪後
   跳過，不再每輪重打（規則見 app/services/llm_failures.py；`--retry-blocked` 手動解除）。

**擷取單位＝一份研報**（一次 LLM 呼叫回該報告涵蓋的多標的，Python fan-out 成多列）。
資料源用 DB full_text（天然只涵蓋已入庫、is_research 的語料）。

用法：
  uv run python scripts/extract_signals.py --dry-run          # 只印子集與工作項數
  uv run python scripts/extract_signals.py --limit 5          # 小跑試驗
  uv run python scripts/extract_signals.py                    # 全子集
  uv run python scripts/extract_signals.py --reextract        # 版本升級後強制重跑

注意：--workers 預設壓到 2（見 generate_summaries.py 註），子集 + 低併發雙重控制成本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from sqlalchemy import text  # noqa: E402

from app.services import llm_failures  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.signal_extract import (  # noqa: E402
    EXTRACTION_VERSION,
    SIGNAL_MODEL_DEFAULT,
    ParsedReportSignals,
    ReportContext,
    SignalRow,
    build_rows,
    build_signal_prompt,
    parse_signal,
)
from scripts._claude_cli import (  # noqa: E402
    CliNotFoundError,
    CliResult,
    failure_kind,
    is_retryable,
    record_escalation,
    run_claude,
)
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "signal_failures.log"


# ── 純 SQL builder（供 test_extract_signals_sql.py 字串斷言、無 DB）──

def build_subset_sql() -> str:
    """高覆蓋標的子集：每個 (market, code) 券商數/報告數達門檻，取 top-N。

    unnest 放 FROM（對齊 overview.py 的 `unnest(r.instrument_types) it` 慣例）。
    count(DISTINCT r.source)＝券商數；count(*)＝該標的的報告數（每報告對該標的一列）。
    """
    return (
        "SELECT st AS instrument_code, r.market, "
        "count(DISTINCT r.source) AS broker_count, count(*) AS report_count "
        "FROM research.research_report r, unnest(r.stock_targets) st "
        "WHERE r.is_research IS NOT FALSE "
        "  AND r.report_date IS NOT NULL "
        "GROUP BY st, r.market "
        "HAVING count(DISTINCT r.source) >= :min_brokers "
        "   AND count(*) >= :min_reports "
        "ORDER BY broker_count DESC, report_count DESC, st "
        "LIMIT :top_n"
    )


def build_reports_sql() -> str:
    """撈某 market 內涵蓋子集標的的研報（陣列重疊 && 走 idx_rr_stock_targets GIN）。"""
    return (
        "SELECT r.id::text, r.source, r.report_date, r.market, "
        "       r.file_name, r.full_text, r.stock_targets, r.file_hash "
        "FROM research.research_report r "
        "WHERE r.market = :market "
        "  AND r.stock_targets && CAST(:codes AS text[]) "
        "  AND r.is_research IS NOT FALSE "
        "  AND r.full_text IS NOT NULL "
        "  AND r.report_date IS NOT NULL "
        "ORDER BY r.report_date DESC NULLS LAST, r.file_name"
    )


def build_existing_signals_sql() -> str:
    """撈既有訊號列供 checkpoint 判斷（以 report_id::text 比對避免 uuid 陣列轉型）。"""
    return (
        "SELECT report_id::text, instrument_code, extraction_status, extraction_version "
        "FROM research.report_signal "
        "WHERE report_id::text = ANY(:report_ids)"
    )


SIGNAL_UPSERT_SQL = text(
    """
    INSERT INTO research.report_signal
        (id, report_id, market, instrument_code, broker, report_date,
         rating_raw, rating_normalized, target_price, target_currency,
         target_horizon, target_price_evidence, eps_estimates, thesis_dimensions,
         extraction_version, extraction_status, raw_payload, error_detail)
    VALUES
        (:id, :report_id, :market, :instrument_code, :broker, :report_date,
         :rating_raw, :rating_normalized, :target_price, :target_currency,
         :target_horizon, :target_price_evidence,
         CAST(:eps_estimates AS jsonb), CAST(:thesis_dimensions AS jsonb),
         :extraction_version, :extraction_status,
         CAST(:raw_payload AS jsonb), :error_detail)
    ON CONFLICT (report_id, market, instrument_code) DO UPDATE SET
        broker = EXCLUDED.broker,
        report_date = EXCLUDED.report_date,
        rating_raw = EXCLUDED.rating_raw,
        rating_normalized = EXCLUDED.rating_normalized,
        target_price = EXCLUDED.target_price,
        target_currency = EXCLUDED.target_currency,
        target_horizon = EXCLUDED.target_horizon,
        target_price_evidence = EXCLUDED.target_price_evidence,
        eps_estimates = EXCLUDED.eps_estimates,
        thesis_dimensions = EXCLUDED.thesis_dimensions,
        extraction_version = EXCLUDED.extraction_version,
        extraction_status = EXCLUDED.extraction_status,
        raw_payload = EXCLUDED.raw_payload,
        error_detail = EXCLUDED.error_detail
    """
)


def row_to_params(row: SignalRow) -> dict:
    """SignalRow → upsert named params（jsonb 欄位序列化為字串供 CAST）。"""
    return {
        "id": str(uuid.uuid4()),
        "report_id": row.report_id,
        "market": row.market,
        "instrument_code": row.instrument_code,
        "broker": row.broker,
        "report_date": row.report_date,
        "rating_raw": row.rating_raw,
        "rating_normalized": row.rating_normalized,
        "target_price": row.target_price,
        "target_currency": row.target_currency,
        "target_horizon": row.target_horizon,
        "target_price_evidence": row.target_price_evidence,
        "eps_estimates": json.dumps(row.eps_estimates, ensure_ascii=False),
        "thesis_dimensions": json.dumps(row.thesis_dimensions, ensure_ascii=False),
        "extraction_version": row.extraction_version,
        "extraction_status": row.extraction_status,
        "raw_payload": (
            json.dumps(row.raw_payload, ensure_ascii=False)
            if row.raw_payload is not None
            else None
        ),
        "error_detail": row.error_detail,
    }


# ── LLM 呼叫（對齊 generate_summaries.py）──

# 輸出上限（第二版計畫 §8）。多標的研報一次回好幾組論點與
# 證據，非 thinking 模式不設上限時只有 8K，會被截斷。
MAX_TOKENS = 16384


def call_cli(
    prompt: str, model: str, timeout: int = 180, *,
    file_hash: Optional[str] = None, report_id: Optional[str] = None,
) -> CliResult:
    """呼叫 LLM（`run_claude`，DeepSeek；名稱是 CLI 時代的歷史值）。回 (text, None) 或 (None, 失敗原因)。

    實作在 `scripts/_claude_cli.py`（全批次共用）。這裡原本是
    `except Exception: return None`，於是所有失敗都被寫成同一句「CLI 無回應或逾時」
    ——`data/signal_failures.log` 累積 9,273 筆全是那一句，2026-08 連續四天 100%
    失敗時完全看不出該修 PATH、該調 timeout，還是該去看帳號額度。
    """
    return run_claude(
        prompt, model, timeout=timeout, max_tokens=MAX_TOKENS,
        meta={"task": llm_failures.TASK_SIGNAL, "file_hash": file_hash, "report_id": report_id},
    )


# ── 進度計數 ──
_done = 0
_ok = 0
_rejected = 0
_fail = 0


class WorkItem:
    """一份待擷取的研報 + 其 requested 標的清單。"""

    __slots__ = ("report_id", "market", "broker", "report_date", "file_name",
                 "full_text", "requested_codes", "file_hash")

    def __init__(self, report_id, market, broker, report_date, file_name,
                 full_text, requested_codes, file_hash=None):
        self.report_id = report_id
        self.market = market
        self.broker = broker
        self.report_date = report_date
        self.file_name = file_name
        self.full_text = full_text
        self.requested_codes = requested_codes
        self.file_hash = file_hash  # 跳過名單（llm_task_failure）的鍵


async def _fetch_subset(session, min_brokers, min_reports, top_n):
    rows = (
        await session.execute(
            text(build_subset_sql()),
            {"min_brokers": min_brokers, "min_reports": min_reports, "top_n": top_n},
        )
    ).all()
    return [(str(code), str(market), int(bc), int(rc)) for code, market, bc, rc in rows]


async def _fetch_reports(session, market, codes):
    rows = (
        await session.execute(
            text(build_reports_sql()), {"market": market, "codes": list(codes)}
        )
    ).all()
    return rows


async def _fetch_done_map(session, report_ids):
    """report_id → {instrument_code: (status, version)}，供 checkpoint 判斷。"""
    if not report_ids:
        return {}
    rows = (
        await session.execute(
            text(build_existing_signals_sql()), {"report_ids": list(report_ids)}
        )
    ).all()
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for rid, code, status, version in rows:
        out.setdefault(rid, {})[code] = (status, version)
    return out


def _is_done(requested_codes, existing: dict, reextract: bool) -> bool:
    """該報告是否可跳過：所有 requested 標的皆已 valid/partial 且版本相符。"""
    if reextract:
        return False
    for code in requested_codes:
        rec = existing.get(code)
        if rec is None:
            return False
        status, version = rec
        if status not in ("valid", "partial") or version != EXTRACTION_VERSION:
            return False
    return True


async def build_worklist(min_brokers, min_reports, top_n, reextract, skip_model=None):
    """回傳 (subset, worklist)：subset 供 dry-run 顯示，worklist 為需擷取的報告。

    skip_model 非 None＝套跳過名單：該 model 下 llm_failures.should_skip 為真的研報不排入。
    """
    async with SessionFactory() as session:
        subset = await _fetch_subset(session, min_brokers, min_reports, top_n)
        subset_by_market: dict[str, set[str]] = {}
        for code, market, _bc, _rc in subset:
            subset_by_market.setdefault(market, set()).add(code)

        worklist: list[WorkItem] = []
        for market, codes in subset_by_market.items():
            reports = await _fetch_reports(session, market, codes)
            ids = [r[0] for r in reports]
            done_map = await _fetch_done_map(session, ids)
            failures = (
                await llm_failures.fetch_failures(
                    session, llm_failures.TASK_SIGNAL, [r[7] for r in reports if r[7]]
                )
                if skip_model
                else {}
            )
            for rid, source, report_date, mkt, file_name, full_text, targets, fh in reports:
                requested = sorted(set(targets or []) & codes)
                if not requested:
                    continue
                if _is_done(requested, done_map.get(rid, {}), reextract):
                    continue
                if skip_model and llm_failures.should_skip(failures.get(fh), skip_model):
                    continue
                worklist.append(
                    WorkItem(rid, mkt, source, report_date, file_name, full_text, requested,
                             file_hash=fh)
                )
    return subset, worklist


async def _upsert_rows(rows: list[SignalRow]) -> None:
    async with SessionFactory() as session:
        for row in rows:
            await session.execute(SIGNAL_UPSERT_SQL, row_to_params(row))
        await session.commit()


async def extract_one(
    sem: asyncio.Semaphore, item: WorkItem, excerpt: int, model: str, total: int,
    retries: int = 2, recorder: Optional[llm_failures.FailureRecorder] = None,
) -> None:
    global _done, _ok, _rejected, _fail
    ctx = ReportContext(
        report_id=item.report_id,
        market=item.market,
        broker=item.broker,
        report_date=item.report_date,
        requested_codes=item.requested_codes,
    )
    date_str = item.report_date.isoformat() if item.report_date else None
    prompt = build_signal_prompt(
        item.file_name, date_str, item.broker, item.requested_codes,
        (item.full_text or "")[:excerpt],
    )
    parsed: Optional[ParsedReportSignals] = None
    used_model: Optional[str] = None  # 產出 parsed 那次回應的模型（raw_payload.model）
    # 保留最後一次的失敗原因：三次都沒回應時，log 要寫得出是逾時、非零退出碼還是別的
    last_error = "LLM 無回應"
    http_reason: Optional[str] = None  # HTTP 的審查／截斷／空回應／400（failure_kind）
    async with sem:
        for _ in range(retries + 1):
            # CliNotFoundError 刻意不接：那是環境壞了（每篇都會踩），
            # 讓它一路拋到 main 中止整批，而不是靜靜地把 N 篇都記成 rejected。
            res = await asyncio.to_thread(
                call_cli, prompt, model, file_hash=item.file_hash, report_id=item.report_id
            )
            if res.text:
                parsed = parse_signal(res.text, item.requested_codes)
                used_model = res.model_resp or model
                if parsed.ok:
                    break
            elif res.error:
                last_error = res.error
            if res.text is None and not is_retryable(res):
                # HTTP 失敗：傳輸層已重試過，或本來就是決定性的（見 scripts/_claude_cli.py）
                http_reason = failure_kind(res) or http_reason
                break
        # parsed 非 None ＝至少有一次「回了東西」；之後全數 rejected 就是內容型失敗，
        # 記入跳過名單。全程沒回應（逾時、非零退出）是環境型，不記。
        # HTTP 的內容型失敗是結束這一輪的那一次，取它（截斷、審查 1 次就跳過）。
        fail_reason = http_reason or (llm_failures.UNPARSEABLE if parsed is not None else None)
        if parsed is None:
            # 呼叫失敗（逾時、過載等）→ 落 rejected 列（供之後重跑），並記失敗
            parsed = ParsedReportSignals(ok=False, error=last_error)

    try:
        rows = build_rows(ctx, parsed, model=used_model)
        await _upsert_rows(rows)
        if all(r.extraction_status == "rejected" for r in rows):
            _rejected += 1
            with open(FAIL_LOG, "a", encoding="utf-8") as f:
                f.write(f"{item.report_id}\t{item.file_name}\t{parsed.error or 'rejected'}\n")
            if recorder and fail_reason:
                await recorder.record(item.file_hash, fail_reason)
        else:
            _ok += 1
            if recorder:
                await recorder.clear(item.file_hash)
    except Exception as exc:  # 單筆例外只記 log，不中斷長跑
        _fail += 1
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{item.report_id}\t{item.file_name}\tEXC:{exc}\n")

    _done += 1
    if _done % 10 == 0 or _done == total:
        print(f"  {_done}/{total}  ok={_ok} rejected={_rejected} fail={_fail}", flush=True)


async def main(args) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    recorder = await llm_failures.open_recorder(llm_failures.TASK_SIGNAL, args.model, SessionFactory)
    # --reextract 隱含 --retry-blocked：跳過鍵只看 model、不看 prompt／EXTRACTION_VERSION，
    # 改了 prompt 或版本而強制重跑時，舊 prompt 下累計的失敗不該繼續把研報擋在外面。
    skip_model = (
        args.model if recorder is not None and not (args.retry_blocked or args.reextract) else None
    )
    subset, worklist = await build_worklist(
        args.min_brokers, args.min_reports, args.top_n, args.reextract, skip_model
    )
    print(
        f"子集：{len(subset)} 個 (market, code)｜待擷取報告：{len(worklist)} 份"
        f"｜version={EXTRACTION_VERSION}｜model={args.model}",
        flush=True,
    )

    if args.dry_run:
        print("\n[dry-run] 子集（依券商覆蓋度）：", flush=True)
        for code, market, bc, rc in subset:
            print(f"  {market} {code}  券商 {bc}  報告 {rc}", flush=True)
        print(f"\n[dry-run] 待擷取報告 {len(worklist)} 份（未呼叫 LLM）", flush=True)
        return

    if args.limit:
        worklist = worklist[: args.limit]
    total = len(worklist)
    if not total:
        print("nothing to do（子集皆已擷取）", flush=True)
        return

    sem = asyncio.Semaphore(args.workers)
    try:
        await asyncio.gather(
            *(
                extract_one(sem, item, args.excerpt, args.model, total, recorder=recorder)
                for item in worklist
            )
        )
    except CliNotFoundError as exc:
        # 400 升級：觸發的研報先記入跳過名單，下一輪才跳得過去（審查 H2）
        await record_escalation(exc, recorder)
        # 環境層級失敗：剩下的每一篇都會踩到同一顆地雷。中止並以非零退出碼收場 ——
        # 「跑完 N 次註定失敗的呼叫、印 ok=0 rejected=N、然後 exit 0」是最糟的結局，
        # 因為排程 unit 仍然是綠的，沒有任何人會知道。
        print(f"\n中止：{exc}", flush=True)
        print(f"（已完成 {_done}/{total}；ok={_ok} rejected={_rejected} fail={_fail}）", flush=True)
        raise SystemExit(2) from exc
    print(f"\ndone. ok={_ok} rejected={_rejected} fail={_fail}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-brokers", type=int, default=3, help="子集門檻：券商數下限")
    ap.add_argument("--min-reports", type=int, default=5, help="子集門檻：報告數下限")
    ap.add_argument("--top-n", type=int, default=50, help="子集標的數上限（依覆蓋度）")
    ap.add_argument("--workers", type=int, default=2, help="同時的 LLM 呼叫數")
    ap.add_argument("--limit", type=int, default=None, help="最多擷取幾份報告（試跑用）")
    ap.add_argument("--excerpt", type=int, default=16000, help="餵給 LLM 的內文字數上限")
    ap.add_argument("--model", default=SIGNAL_MODEL_DEFAULT)
    ap.add_argument("--reextract", action="store_true",
                    help="忽略 checkpoint，強制重跑（隱含 --retry-blocked）")
    ap.add_argument("--retry-blocked", action="store_true",
                    help="不套跳過名單（research.llm_task_failure），連已判定跳過的研報也重打；"
                         "改 prompt 後要加")
    ap.add_argument("--dry-run", action="store_true", help="只印子集與工作項數，不呼叫 LLM")
    # --dry-run 也一起擋，理由同 extract_takeaways.py：鎖的涵蓋範圍不隨旗標而變。
    args = ap.parse_args()
    # 取鎖之前預檢模型與金鑰（缺金鑰是「跑了也白跑」，要在撞鎖 rc=75 之前說出來）。
    if not args.dry_run:  # --dry-run 不呼叫 LLM
        require_llm_key({llm_failures.TASK_SIGNAL: args.model})
    with claude_cli_lock_or_exit("extract_signals"):
        asyncio.run(main(args))
