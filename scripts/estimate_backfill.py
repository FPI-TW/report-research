"""首次導入的事前估價：分層算出 LLM 的輸入／輸出 token、金額與時間。零 LLM、唯讀。

給「導入 → 估價 → 客戶簽字 → 開跑」用（`docs/WORKFLOW.md`「首次導入」）。分層規則與 token 估計法在
`app/services/backfill_tiers.py` 的模組 docstring：

- 第一層：標註（`tag`）。已有標註快取（`data/tags/<hash>.json`）或側檔同時給了 `market`＋`stock_targets` 的不計。
  抽取與嵌入不呼叫 LLM，不在這張表裡（時間另計）。
- 第二層（`report_date` 在最近 `recent_months` 個月內）、第三層（其餘，含 `report_date` 為 NULL）：摘要、標題、
  摘錄、訊號。來源是 DB 時只計還沒做的（同 `backfill_tiers.py` 的待補判斷，含跳過名單）。訊號的份數是**上限**：
  實際只擷取高覆蓋標的子集（`extract_signals.py`）。

兩種語料來源：

- `--source cache`：抽取快取（`data/extracted/<hash>.json`，`scripts/extract_all.py` 的產物），入庫之前估。略過
  管理類、掃描檔、抽取失敗的檔（它們不會入庫）。沒有標註快取的檔當成研報（類型要標註後才知道，寧可多算）；
  側檔只認同名 JSON（`_metadata.csv` 清單不讀）。
- `--source db`：已入庫的 `research_report.full_text`（第一層已完成，只估第二、三層還沒做的）。

**費率不寫死**：以 `--rates <檔案>`（TOML，格式見 `parse_rates`）或 `--input-rate`／`--output-rate`（每
`--per-tokens` 個 token 的價格，預設每百萬）提供；沒給費率就只輸出 token 與時間。快取命中折扣不計（保守）。

時間＝呼叫數 × 每次秒數 ÷ 併發數（派生 `--workers`，預設 2＝各批次的預設；標註 `--tag-workers`，預設 1＝連接器
匯入逐篇標註）。每次秒數與輸出 token 沒有實測時用預設值（`TASK_SPECS`，表上標「預設」）；`--usage-log`
（預設 `data/llm_usage.jsonl`，不存在就略過）校正：輸入 token 以同一批研報的實測 ÷ 估計得倍率，輸出 token
與每次秒數改用實測平均（每項至少 20 筆）。第三層另以 profile 的每日上限換算「背景補齊約需幾天」。

用法：
  uv run python scripts/estimate_backfill.py --source cache --rates rates.toml
  uv run python scripts/estimate_backfill.py --source db --input-rate 1.25 --output-rate 10 --currency USD
  uv run python scripts/estimate_backfill.py --source db --json > estimate.json
  make estimate ESTIMATE_ARGS="--source cache --rates rates.toml"

退出碼：0 成功；1 參數或費率檔錯誤；3 DB 連不上（`--source db`）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import backfill_tiers as bt  # noqa: E402

TIER_TAG = 1
TIERS = (TIER_TAG, bt.TIER_RECENT, bt.TIER_OLDER)
DB_BATCH = 500


class EstimateError(ValueError):
    pass


# ── 費率 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rates:
    """每 `per_tokens` 個 token 的價格；`tasks` 是個別任務的覆寫（例如訊號用另一顆模型時）。"""

    currency: str = ""
    per_tokens: int = 1_000_000
    input: float = 0.0
    output: float = 0.0
    tasks: Mapping[str, tuple[float, float]] = field(default_factory=dict)

    def cost(self, task: str, input_tokens: float, output_tokens: float) -> float:
        rin, rout = self.tasks.get(task, (self.input, self.output))
        return (input_tokens * rin + output_tokens * rout) / self.per_tokens


def _rate(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not math.isfinite(value):
        raise EstimateError(f"費率 {where} 必須是非負數，目前是 {value!r}")
    return float(value)


def parse_rates(data: Mapping[str, Any], origin: str = "費率檔") -> Rates:
    """費率檔（TOML）：

        currency = "USD"        # 顯示用的幣別（必填）
        per_tokens = 1000000    # 價格是每多少 token（選填，預設一百萬）
        input = 1.25            # 輸入（必填）
        output = 10.0           # 輸出（必填）
        [tasks.signal]          # 選填：個別任務的覆寫（tag summary title takeaway signal）
        input = 2.5
        output = 15.0

    未知的鍵一律拒絕（拼錯的費率靜默退回預設，報價就錯了）。
    """
    unknown = sorted(set(data) - {"currency", "per_tokens", "input", "output", "tasks"})
    if unknown:
        raise EstimateError(f"{origin}：未知的鍵 {unknown[0]!r}（可用：currency per_tokens input output tasks）")
    for key in ("currency", "input", "output"):
        if key not in data:
            raise EstimateError(f"{origin}：缺少 {key}")
    currency = data["currency"]
    if not isinstance(currency, str) or not currency.strip() or len(currency) > 10:
        raise EstimateError(f"{origin}：currency 必須是 1 到 10 字的字串")
    per = data.get("per_tokens", 1_000_000)
    if isinstance(per, bool) or not isinstance(per, int) or per <= 0:
        raise EstimateError(f"{origin}：per_tokens 必須是正整數")
    tasks_table = data.get("tasks", {})
    if not isinstance(tasks_table, dict):
        raise EstimateError(f"{origin}：tasks 必須是表格")
    tasks: dict[str, tuple[float, float]] = {}
    base_in, base_out = _rate(data["input"], "input"), _rate(data["output"], "output")
    for task, spec in tasks_table.items():
        if task not in bt.ALL_TASKS:
            raise EstimateError(f"{origin}：tasks.{task} 不是任務名（可用：{' '.join(bt.ALL_TASKS)}）")
        if not isinstance(spec, dict) or set(spec) - {"input", "output"}:
            raise EstimateError(f"{origin}：tasks.{task} 只能有 input、output")
        tasks[task] = (_rate(spec.get("input", base_in), f"tasks.{task}.input"),
                       _rate(spec.get("output", base_out), f"tasks.{task}.output"))
    return Rates(currency=currency.strip(), per_tokens=per, input=base_in, output=base_out, tasks=tasks)


def load_rates(path: Path) -> Rates:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise EstimateError(f"讀不到費率檔 {path}：{exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise EstimateError(f"費率檔 {path} 的 TOML 語法錯誤：{exc}") from exc
    return parse_rates(data, str(path))


# ── 估計 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Item:
    """一份研報：要估的任務與送出的內文前綴。"""

    file_hash: str
    report_date: date | None
    tasks: frozenset[str]
    head: str


@dataclass
class Cell:
    calls: int = 0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    seconds: float = 0.0
    cost: float | None = None

    def add(self, other: "Cell") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.seconds += other.seconds
        if other.cost is not None:
            self.cost = (self.cost or 0.0) + other.cost


@dataclass
class Estimate:
    today: date
    cutoff: date
    reports: int
    tiers: dict[int, dict[str, Cell]]
    calibration: bt.Calibration
    rates: Rates | None
    daily_limit: int

    def tier_total(self, tier: int) -> Cell:
        total = Cell(cost=0.0 if self.rates else None)
        for cell in self.tiers[tier].values():
            total.add(cell)
        return total

    def background_days(self) -> float:
        total = self.tier_total(bt.TIER_OLDER)
        return (total.input_tokens + total.output_tokens) / self.daily_limit if self.daily_limit else 0.0


def tier_tasks(tier: int) -> tuple[str, ...]:
    return (bt.TASK_TAG,) if tier == TIER_TAG else bt.DERIVED_TASKS


def estimate(items: Iterable[Item], *, today: date, months: int, daily_limit: int, rates: Rates | None = None,
             usage_rows: Iterable[Mapping] = (), workers: int = 2, tag_workers: int = 1) -> Estimate:
    """固定輸入 → 固定輸出（`tests/test_estimate_backfill.py` 釘住）。"""
    cutoff = bt.recent_cutoff(today, months)
    raw: dict[tuple[int, str], list[float]] = {}  # (層, 任務) → [呼叫數, 估計輸入]
    per_hash: dict[tuple[str, str], int] = {}
    reports = 0
    for item in items:
        reports += 1
        estimates = bt.input_tokens_by_task(item.head, [t for t in bt.ALL_TASKS if t in item.tasks])
        derived_tier = bt.tier_of(item.report_date, cutoff)
        for task, tokens in estimates.items():
            tier = TIER_TAG if task == bt.TASK_TAG else derived_tier
            acc = raw.setdefault((tier, task), [0, 0.0])
            acc[0] += 1
            acc[1] += tokens
            per_hash[(task, item.file_hash)] = tokens
    cal = bt.calibration_from_usage(usage_rows, per_hash)
    tiers: dict[int, dict[str, Cell]] = {}
    for tier in TIERS:
        cells: dict[str, Cell] = {}
        for task in tier_tasks(tier):
            calls, est_in = raw.get((tier, task), [0, 0.0])
            calls = int(calls)
            cell = Cell(calls=calls, input_tokens=est_in * cal.factor(task),
                        output_tokens=calls * cal.out_tokens(task),
                        seconds=calls * cal.call_seconds(task) / (tag_workers if task == bt.TASK_TAG else workers))
            if rates is not None:
                cell.cost = rates.cost(task, cell.input_tokens, cell.output_tokens)
            cells[task] = cell
        tiers[tier] = cells
    return Estimate(today=today, cutoff=cutoff, reports=reports, tiers=tiers, calibration=cal, rates=rates,
                    daily_limit=daily_limit)


# ── 輸出 ─────────────────────────────────────────────────────────────────

def _cell_json(cell: Cell) -> dict:
    return {"calls": cell.calls, "input_tokens": round(cell.input_tokens), "output_tokens": round(cell.output_tokens),
            "hours": round(cell.seconds / 3600, 2), "cost": None if cell.cost is None else round(cell.cost, 2)}


def to_json(est: Estimate, *, source: str) -> dict:
    cal = est.calibration
    return {
        "today": est.today.isoformat(),
        "recent_cutoff": est.cutoff.isoformat(),
        "source": source,
        "reports": est.reports,
        "currency": est.rates.currency if est.rates else None,
        "tiers": {str(t): {"tasks": {k: _cell_json(c) for k, c in est.tiers[t].items()},
                           "total": _cell_json(est.tier_total(t))} for t in TIERS},
        "background": {"daily_token_limit": est.daily_limit, "days": round(est.background_days(), 1)},
        "calibration": {t: {"input_factor": round(cal.factor(t), 3) if t in cal.input_factor else None,
                            "output_tokens": round(cal.out_tokens(t)), "seconds": round(cal.call_seconds(t), 1),
                            "calibrated": {"input": t in cal.input_factor, "output": t in cal.output_tokens,
                                           "seconds": t in cal.seconds},
                            "matched_reports": cal.samples.get(t, 0)} for t in bt.ALL_TASKS},
        "method": {"cjk_tokens_per_char": bt.CJK_TOKENS_PER_CHAR, "other_chars_per_token": bt.OTHER_CHARS_PER_TOKEN,
                   "excerpt": {t: s.excerpt for t, s in bt.TASK_SPECS.items()}},
    }


def _fmt_int(n: float) -> str:
    return f"{round(n):,}"


def render(est: Estimate, *, source: str) -> str:
    cal = est.calibration
    cur = est.rates.currency if est.rates else ""
    lines = [
        f"估價：來源 {source}，{est.reports:,} 份；今天 {est.today}（台北），第二層＝report_date ≥ {est.cutoff}，"
        "第三層＝更早與 report_date 為 NULL",
        "",
        f"{'層':<4}{'任務':<10}{'呼叫數':>10}{'輸入 token':>16}{'輸出 token':>14}{'時間(時)':>10}"
        + (f"{'金額(' + cur + ')':>14}" if est.rates else ""),
    ]
    for tier in TIERS:
        rows = list(est.tiers[tier].items()) + [("小計", est.tier_total(tier))]
        for task, cell in rows:
            lines.append(
                f"{tier:<4}{task:<10}{cell.calls:>10,}{_fmt_int(cell.input_tokens):>16}{_fmt_int(cell.output_tokens):>14}"
                f"{cell.seconds / 3600:>10.1f}" + (f"{cell.cost or 0:>14,.2f}" if est.rates else ""))
        lines.append("")
    if est.rates is None:
        lines.append("未提供費率（--rates 或 --input-rate／--output-rate）：只列 token 與時間。")
    lines.append(f"第三層以每日上限 {est.daily_limit:,} token 背景補齊，約需 {est.background_days():.1f} 天。")
    lines.append("訊號的呼叫數是上限：實際只擷取高覆蓋標的子集。")
    notes = []
    for t in bt.ALL_TASKS:
        matched = f"輸入×{cal.factor(t):.2f}（{cal.samples.get(t, 0)} 份比對）"
        parts = [matched if t in cal.input_factor else "輸入未校正",
                 "輸出實測" if t in cal.output_tokens else "輸出預設",
                 "秒數實測" if t in cal.seconds else "秒數預設"]
        notes.append(f"{t}：{'、'.join(parts)}")
    lines.append("校正：" + "；".join(notes))
    lines.append(f"估計法：CJK 字 ×{bt.CJK_TOKENS_PER_CHAR}＋其他非空白字元 ÷{bt.OTHER_CHARS_PER_TOKEN}，未校正時"
                 "請當成 ±30% 的量級；先小跑一批再帶用量檔重估（說明見 app/services/backfill_tiers.py）。")
    return "\n".join(lines)


# ── 語料來源 ─────────────────────────────────────────────────────────────


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _sidecar(file_path: str | None):
    """同名 JSON 側檔（驗證過的 Metadata）；沒有或不合格回 None（不合格的檔不會入庫，估價照最壞情況算）。"""
    if not file_path:
        return None
    from app.services.connectors.sidecar import SidecarError, parse_json_bytes, sidecar_path

    path = sidecar_path(Path(file_path))
    try:
        if not path.is_file():
            return None
        return parse_json_bytes(path.read_bytes(), str(path))
    except (OSError, SidecarError):
        return None


def items_from_cache(cache_dir: Path, tags_dir: Path) -> Iterable[Item]:
    from app.profile import get_profile
    from app.services.extraction import cache
    from app.services.tagging import DOC_TYPE_RESEARCH, load_tag

    doc_types = get_profile().doc_types
    for rec in cache.iter_records(cache_dir):
        if rec.get("error") or rec.get("is_admin") or rec.get("scanned"):
            continue
        file_hash = rec.get("file_hash")
        if not file_hash:
            continue
        meta = _sidecar(rec.get("file_path"))
        tag = load_tag(tags_dir, file_hash)
        tasks: set[str] = set()
        if tag is None and not (meta is not None and meta.skips_tagging()):
            tasks.add(bt.TASK_TAG)
        doc_type = tag.doc_type if tag is not None else DOC_TYPE_RESEARCH
        if meta is not None and meta.has("market"):
            doc_type = DOC_TYPE_RESEARCH
        report_date = meta.report_date if meta is not None and meta.has("report_date") else _parse_date(
            rec.get("report_date"))
        for task in bt.DERIVED_TASKS:
            if not doc_types.enabled(doc_type, bt.TASK_FEATURE[task]):
                continue
            if task == bt.TASK_TITLE and meta is not None and meta.has("title"):
                continue
            if task == bt.TASK_SIGNAL:
                targets = meta.stock_targets if meta is not None and meta.has("stock_targets") else (
                    tag.stock_targets if tag is not None else None)
                if report_date is None or (targets is not None and len(targets) == 0):
                    continue
            tasks.add(task)
        if tasks:
            yield Item(file_hash, report_date, frozenset(tasks), (rec.get("text") or "")[:bt.MAX_EXCERPT])


async def _db_items(models: dict[str, str]) -> list[Item]:
    from sqlalchemy import text

    from app.services import llm_failures
    from app.services.db import SessionFactory, relax_statement_timeout

    out: list[Item] = []
    async with SessionFactory() as session:
        await relax_statement_timeout(session)
        skip = await llm_failures.table_ready(session)
        sql = text(bt.build_estimate_sql(skip_blocked=skip))
        params: dict = bt.skip_params(models) if skip else {}
        after = ""
        while True:
            rows = (await session.execute(sql, {**params, "after": after, "batch": DB_BATCH})).all()
            if not rows:
                break
            for r in rows:
                tasks = frozenset(t for t in bt.DERIVED_TASKS if getattr(r, f"need_{t}"))
                if tasks:
                    out.append(Item(r.file_hash, r.report_date, tasks, r.head or ""))
            after = rows[-1].file_hash
    return out


def _models() -> dict[str, str]:
    from app.services.llm_models import TASK_SUMMARY, TASK_TAKEAWAY, TASK_TITLE, resolve_model
    from app.services.signal_extract import SIGNAL_MODEL_DEFAULT

    return {bt.TASK_SUMMARY: resolve_model(TASK_SUMMARY), bt.TASK_TITLE: resolve_model(TASK_TITLE),
            bt.TASK_TAKEAWAY: resolve_model(TASK_TAKEAWAY), bt.TASK_SIGNAL: SIGNAL_MODEL_DEFAULT}


def read_usage(path: Path) -> list[dict]:
    try:
        if not path.is_file():
            return []
        with path.open(encoding="utf-8", errors="replace") as fh:
            return list(bt.iter_usage_rows(fh))
    except OSError:
        return []


# ── 入口 ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="首次導入的事前估價（零 LLM）")
    ap.add_argument("--source", choices=("cache", "db"), required=True, help="語料來源：抽取快取或 DB")
    ap.add_argument("--cache-dir", type=Path, default=None, help="抽取快取目錄（預設 data/extracted）")
    ap.add_argument("--tags-dir", type=Path, default=ROOT / "data" / "tags", help="標註快取目錄")
    ap.add_argument("--rates", type=Path, default=None, help="費率檔（TOML，格式見 parse_rates）")
    ap.add_argument("--input-rate", type=float, default=None, help="輸入每 --per-tokens 個 token 的價格")
    ap.add_argument("--output-rate", type=float, default=None, help="輸出每 --per-tokens 個 token 的價格")
    ap.add_argument("--per-tokens", type=int, default=None, help="價格的 token 單位（預設 1000000）")
    ap.add_argument("--currency", default=None, help="幣別（顯示用）")
    ap.add_argument("--usage-log", type=Path, default=None, help="用來校正的用量檔（預設 data/llm_usage.jsonl）")
    ap.add_argument("--no-calibrate", action="store_true", help="不讀用量檔，只用預設值")
    ap.add_argument("--workers", type=int, default=2, help="派生批次的併發數（時間換算用）")
    ap.add_argument("--tag-workers", type=int, default=1, help="標註的併發數（時間換算用）")
    ap.add_argument("--recent-months", type=int, default=None, help="第二層窗期（預設取 profile）")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help="以哪一天為今天（YYYY-MM-DD，預設今天）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    return ap


def resolve_rates(args: argparse.Namespace) -> Rates | None:
    base: dict[str, Any] = {}
    if args.rates is not None:
        rates = load_rates(args.rates)
        base = {"currency": rates.currency, "per_tokens": rates.per_tokens, "input": rates.input,
                "output": rates.output, "tasks": {t: {"input": i, "output": o} for t, (i, o) in rates.tasks.items()}}
    for key, value in (("input", args.input_rate), ("output", args.output_rate), ("per_tokens", args.per_tokens),
                       ("currency", args.currency)):
        if value is not None:
            base[key] = value
    if not base:
        return None
    if args.rates is None and ("input" in base or "output" in base):
        base.setdefault("currency", "")
        if not base["currency"]:
            raise EstimateError("以參數給費率時要一起給 --currency")
    return parse_rates(base, "--rates／費率參數")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.workers <= 0 or args.tag_workers <= 0:
            raise EstimateError("--workers、--tag-workers 必須是正整數")
        if args.recent_months is not None and args.recent_months <= 0:
            raise EstimateError("--recent-months 必須是正整數")
        rates = resolve_rates(args)
    except EstimateError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    from app.profile import get_profile

    bf = get_profile().backfill
    if args.source == "cache":
        from app.services.extraction import cache

        items: Iterable[Item] = items_from_cache(args.cache_dir or cache.CACHE_DIR, args.tags_dir)
    else:
        try:
            items = asyncio.run(_db_items(_models()))
        except Exception as exc:  # noqa: BLE001 — 連不上就說清楚，不印 traceback
            print(f"DB 讀取失敗：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
    if args.no_calibrate:
        usage: list[dict] = []
    else:
        from app.services.llm_usage import usage_log_path

        usage = read_usage(args.usage_log or usage_log_path())
    est = estimate(items, today=args.today or bt.today_taipei(), months=args.recent_months or bf.recent_months,
                   daily_limit=bf.daily_token_limit, rates=rates, usage_rows=usage, workers=args.workers,
                   tag_workers=args.tag_workers)
    if args.json:
        print(json.dumps(to_json(est, source=args.source), ensure_ascii=False, indent=2))
    else:
        print(render(est, source=args.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
