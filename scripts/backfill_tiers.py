"""首次導入的分層回填：第二層（最近 N 個月）一次跑完、第三層（更早與無日期）背景在上限內慢慢補。

分層規則、待補判斷與每日上限的帳在 `app/services/backfill_tiers.py`（模組 docstring）；首次導入的完整步驟見
`docs/WORKFLOW.md`「首次導入」。第一層（抽取、嵌入、標註）是既有的匯入，不經這支。

**這支本身不呼叫 LLM、也不取 LLM 批次鎖**：它挑出待補的研報、分批寫成 hashes 檔，再依序叫既有批次
（`generate_summaries`、`generate_titles`、`extract_takeaways` 帶 `--hashes-file`，`extract_signals` 帶
`--hashes-file` 與 `--limit`）。各批次照舊各自載入 LLM 環境、預檢金鑰、取鎖（`scripts/_claude_lock.py`）、
判斷已完成、套跳過名單——所以中斷後重跑不會重複付費，也不另寫一份派生的實作。刻意不在這裡持有 LLM 批次鎖：
子行程取的是同一把鎖，持有它會讓每個子行程撞鎖；而且分批之間放開鎖，sync 的匯入（行內標註也取這把鎖）
才擠得進來，不會被一次數小時的回填擋住。這支自己有一把編排鎖（`data/backfill/.lock`），擋的是兩個
編排同時跑（例如背景排程撞上手動的第二層）。金鑰照 LLM 入口的規矩在取鎖前預檢，缺金鑰時不用等到第一個
子行程才知道。

一輪的流程：挑候選（新的先、NULL 日期最後；本輪處理過的不再挑）→ 依份數與 token 上限排入一批（`--chunk`）→
依序跑四個批次 → 以 `data/llm_usage.jsonl` 裡這批研報的實測 token 入帳（讀不到用量檔時以估計值入帳）→
下一批，直到沒有候選或碰到上限。

- 第二層：沒有預設上限（範圍已經過估價與客戶簽字）；`--max-reports`、`--token-limit` 可再設上限。
- 第三層：每台北日 `[backfill].daily_token_limit`、每輪 `[backfill].max_reports_per_round`（profile）；帳在
  `data/backfill/state.json`。到上限就停，下一輪（排程每小時）再續；換日歸零。以 `os.nice` 降低優先權。
  `--scheduled`（scheduler 的 `backfill-tiers` 工作）在 profile 的 `background_enabled = false` 時是 no-op。

用法：
  uv run python scripts/backfill_tiers.py --status                 # 各層的待補數與今天的背景用量（唯讀、零 LLM）
  uv run python scripts/backfill_tiers.py --tier 2 --dry-run       # 列出會排入的份數與估計 token，不呼叫 LLM
  uv run python scripts/backfill_tiers.py --tier 2                 # 第二層
  uv run python scripts/backfill_tiers.py --tier 3                 # 第三層一輪（受每日上限；不看 background_enabled）
  uv run python scripts/backfill_tiers.py --tier 3 --scheduled     # 排程用

中止與續跑：Ctrl-C 或 kill 都可以；已完成的派生已落庫，重跑時自然不再挑（各批次也會再判斷一次）。
退出碼：0 跑完或碰到上限而停；2 帳號／環境型中止（子批次 rc=2，本批 hashes 保留並印補跑指令）；
75 鎖被佔用（另一個編排，或子批次撞上 LLM 批次鎖——那時本輪停下、下次再續）。
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import fcntl
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services import backfill_tiers as bt  # noqa: E402
from app.services.llm_models import TASK_SUMMARY, TASK_TAKEAWAY, TASK_TITLE, resolve_model  # noqa: E402
from app.services.llm_usage import usage_log_path  # noqa: E402
from app.services.signal_extract import SIGNAL_MODEL_DEFAULT  # noqa: E402

LOCK_BUSY_RC = 75  # scripts/_claude_lock.py 的 EXIT_LOCK_BUSY
ENV_ABORT_RC = 2
LOCK_NAME = ".lock"  # 在 backfill_tiers.work_dir() 底下
DEFAULT_CHUNK = 50
# 背景補齊的 nice 增量（子行程繼承）。
BACKGROUND_NICE = 10

# 任務 → 批次腳本（順序＝執行順序，同 sync 鏈第 5、6 段）。
STEP_SCRIPTS: dict[str, str] = {
    bt.TASK_SUMMARY: "generate_summaries.py",
    bt.TASK_TITLE: "generate_titles.py",
    bt.TASK_TAKEAWAY: "extract_takeaways.py",
    bt.TASK_SIGNAL: "extract_signals.py",
}


def task_models() -> dict[str, str]:
    """各派生要用的模型（與各批次的預設相同；跳過名單以 model 為鍵）。"""
    return {
        bt.TASK_SUMMARY: resolve_model(TASK_SUMMARY),
        bt.TASK_TITLE: resolve_model(TASK_TITLE),
        bt.TASK_TAKEAWAY: resolve_model(TASK_TAKEAWAY),
        bt.TASK_SIGNAL: SIGNAL_MODEL_DEFAULT,
    }


# ── 編排鎖 ───────────────────────────────────────────────────────────────


class OrchestratorBusy(RuntimeError):
    pass


@contextmanager
def orchestrator_lock(path: Path) -> Iterator[None]:
    """兩個編排不同時跑（flock，行程怎麼死都會釋放）。與 LLM 批次鎖是不同的兩把。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise OrchestratorBusy(f"另一個分層回填正在跑（{path}）") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ── 子批次 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Step:
    task: str
    argv: tuple[str, ...]


def chunk_steps(hashes_file: Path, chunk: Sequence[bt.Candidate], workers: int | None = None) -> list[Step]:
    """這一批要跑的批次：只排有研報待補的任務；訊號另帶 `--limit`（份數上限是安全機制，與 sync 相同）。"""
    py = sys.executable
    extra = ("--workers", str(workers)) if workers else ()
    steps: list[Step] = []
    for task in bt.DERIVED_TASKS:
        if not any(task in c.tasks for c in chunk):
            continue
        argv = [py, f"scripts/{STEP_SCRIPTS[task]}", "--hashes-file", str(hashes_file)]
        if task == bt.TASK_SIGNAL:
            argv += ["--limit", str(len(chunk))]
        steps.append(Step(task, (*argv, *extra)))
    return steps


def read_new_usage(path: Path, offset: int) -> tuple[list[dict], bool]:
    """用量檔自 `offset` 起新增的行。第二個值＝用量檔可用（是一般檔案）；不可用時呼叫端改以估計值入帳。"""
    try:
        if not path.is_file():
            return [], False
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return [], False
    return list(bt.iter_usage_rows(data.decode("utf-8", "replace").splitlines())), True


def usage_offset(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


# ── 一次執行 ─────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    rc: int = 0
    reports: int = 0  # 排入（或 dry-run 時會排入）的份數
    tokens: int = 0  # 入帳的 token（dry-run 時是估計）
    batches: int = 0
    stopped_by: str | None = None  # None 沒有候選了；reports／tokens 碰到上限；lock_busy；env_abort
    task_counts: dict[str, int] = field(default_factory=dict)
    oversized: list[str] = field(default_factory=list)


Fetch = Callable[[set[str], int], list[bt.Candidate]]
Run = Callable[[Sequence[str]], int]


def run_backfill(*, tier: int, fetch: Fetch, run: Run, chunk_size: int, max_reports: int | None,
                 token_limit: int | None, dry_run: bool, work_dir: Path, usage_log: Path,
                 state_file: Path | None = None, daily_limit: int | None = None, today: date | None = None,
                 workers: int | None = None, log: Callable[[str], None] = print,
                 stamp: Callable[[], str] = lambda: datetime.now().strftime("%Y%m%d_%H%M%S")) -> Outcome:
    """挑候選 → 分批 → 跑批次 → 入帳，直到沒有候選或碰到上限（說明見模組 docstring）。

    第三層傳 `state_file`＋`daily_limit`：token 上限是當日剩餘，跑完一批就把實測寫進帳。dry-run 不跑子批次、
    不寫帳，只模擬排入（同樣的上限）。
    """
    out = Outcome()
    state = None
    if state_file is not None:
        state = bt.load_state(state_file).for_day(today or bt.today_taipei())
    seen: set[str] = set()
    spent = 0  # 本次執行的 token（dry-run 時是估計）
    while True:
        room = None if max_reports is None else max_reports - out.reports
        if room is not None and room <= 0:
            out.stopped_by = "reports"
            break
        page = chunk_size if room is None else min(chunk_size, room)
        budget = None
        if token_limit is not None:
            budget = max(token_limit - spent, 0)
        if state is not None and daily_limit is not None:
            today_left = max(daily_limit - state.tokens - (spent if dry_run else 0), 0)
            budget = today_left if budget is None else min(budget, today_left)
        candidates = fetch(seen, page)
        if not candidates:
            break
        plan = bt.plan_round(candidates, token_budget=budget, max_reports=page,
                             full_budget=daily_limit if state is not None else None)
        seen.update(c.file_hash for c in plan.selected)
        if plan.oversized:
            seen.update(plan.oversized)
            out.oversized.extend(plan.oversized)
            log(f"略過 {len(plan.oversized)} 份：單篇估計就超過每日上限 {daily_limit}"
                "（調高 daily_token_limit 才補得到）")
        if not plan.selected:
            if plan.oversized and plan.stopped_by is None:
                continue
            out.stopped_by = "tokens"
            break
        out.batches += 1
        out.reports += len(plan.selected)
        for c in plan.selected:
            for t in c.tasks:
                out.task_counts[t] = out.task_counts.get(t, 0) + 1
        if dry_run:
            spent += plan.tokens
            out.tokens += plan.tokens
            if plan.stopped_by == "tokens":
                out.stopped_by = "tokens"
                break
            continue
        rc, used, measured = _run_chunk(plan.selected, tier=tier, run=run, work_dir=work_dir, usage_log=usage_log,
                                        workers=workers, log=log, stamp=stamp)
        spent += used
        out.tokens += used
        if state is not None and state_file is not None:
            state.tokens += used
            state.reports += len(plan.selected)
            state.measured = state.measured and measured
            bt.save_state(state_file, state)
        if rc == LOCK_BUSY_RC:
            out.rc, out.stopped_by = LOCK_BUSY_RC, "lock_busy"
            log("LLM 批次鎖被另一支批次佔用 → 本輪停下；已完成的不會重做，下次再續")
            break
        if rc == ENV_ABORT_RC:
            out.rc, out.stopped_by = ENV_ABORT_RC, "env_abort"
            break
        if plan.stopped_by == "tokens":
            out.stopped_by = "tokens"
            break
    return out


def _run_chunk(chunk: Sequence[bt.Candidate], *, tier: int, run: Run, work_dir: Path, usage_log: Path,
               workers: int | None, log: Callable[[str], None], stamp: Callable[[], str]) -> tuple[int, int, bool]:
    """跑一批。回傳 (rc, 入帳 token, 是否為實測)。rc：0、75（撞鎖，停）或 2（環境型，停）。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    hashes_file = work_dir / f"tier{tier}_{stamp()}_{chunk[0].file_hash[:12]}.txt"
    hashes_file.write_text("".join(f"{c.file_hash}\n" for c in chunk), encoding="utf-8")
    offset = usage_offset(usage_log)
    rc = 0
    for step in chunk_steps(hashes_file, chunk, workers):
        log(f"$ {' '.join(step.argv)}")
        step_rc = run(step.argv)
        if step_rc == 0:
            continue
        if step_rc == LOCK_BUSY_RC:
            rc = LOCK_BUSY_RC
            break
        if step_rc == ENV_ABORT_RC:
            rc = ENV_ABORT_RC
            retained = work_dir / f"retained_{hashes_file.name}"
            shutil.copyfile(hashes_file, retained)
            log(f"{STEP_SCRIPTS[step.task]} 帳號／環境型中止 rc=2 → 本輪停下。這批 hashes 已保留：{retained}")
            log("  排除中止原因後依序補跑（或直接重跑本指令，會重新挑出這批）：")
            for later in chunk_steps(retained, chunk, workers):
                log(f"     {' '.join(later.argv[1:])}")
            break
        log(f"{STEP_SCRIPTS[step.task]} 非零退出 rc={step_rc}（略過，照常往下；未補到的下次再挑）")
    rows, available = read_new_usage(usage_log, offset)
    if available:
        used, _calls = bt.measured_tokens(rows, {c.file_hash for c in chunk})
    else:
        used = sum(c.tokens for c in chunk)
    hashes_file.unlink(missing_ok=True)
    return rc, used, available


# ── DB ───────────────────────────────────────────────────────────────────


async def _skip_ready(session) -> bool:
    from app.services import llm_failures

    return await llm_failures.table_ready(session)


async def fetch_candidates(tier: int, cutoff: date, models: dict[str, str], exclude: set[str],
                           limit: int) -> list[bt.Candidate]:
    from sqlalchemy import text

    from app.services.db import SessionFactory

    async with SessionFactory() as session:
        skip = await _skip_ready(session)
        params: dict = {"cutoff": cutoff, "limit": limit, "exclude": sorted(exclude)}
        if skip:
            params.update(bt.skip_params(models))
        rows = (await session.execute(
            text(bt.build_candidates_sql(tier, skip_blocked=skip, limit=True, exclude=True)), params)).all()
    return [bt.candidate_from_row(r.file_hash, r.report_date, {t: bool(getattr(r, f"need_{t}"))
                                                               for t in bt.DERIVED_TASKS}, r.head or "")
            for r in rows]


async def fetch_status(cutoff: date, models: dict[str, str]) -> list:
    from sqlalchemy import text

    from app.services.db import SessionFactory, relax_statement_timeout

    async with SessionFactory() as session:
        await relax_statement_timeout(session)
        skip = await _skip_ready(session)
        params: dict = {"cutoff": cutoff}
        if skip:
            params.update(bt.skip_params(models))
        return list((await session.execute(text(bt.build_status_sql(skip_blocked=skip)), params)).all())


def print_status(rows, *, cutoff: date, months: int, state: bt.BudgetState, background: bool, daily_limit: int,
                 log: Callable[[str], None] = print) -> None:
    log(f"第二層：report_date ≥ {cutoff}（最近 {months} 個月）；第三層：更早與 report_date 為 NULL")
    head = f"{'層':<4}{'份數':>8}" + "".join(f"{t:>10}" for t in bt.DERIVED_TASKS)
    log(head + "   （各項為待補份數）")
    for r in rows:
        log(f"{r.tier:<4}{r.reports:>8}" + "".join(f"{getattr(r, f'pending_{t}'):>10}" for t in bt.DERIVED_TASKS))
    flag = "啟用" if background else "未啟用（profile [backfill].background_enabled = false）"
    note = "" if state.measured else "（含以估計值入帳的批次）"
    log(f"背景補齊：{flag}；今天（{state.day}）已用 {state.tokens} / {daily_limit} token、{state.reports} 份{note}")


# ── 入口 ─────────────────────────────────────────────────────────────────


def _positive(value: str) -> int:
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError("必須是正整數")
    return n


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="首次導入的分層回填（第二層／第三層）")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tier", type=int, choices=(bt.TIER_RECENT, bt.TIER_OLDER), help="要補的層")
    mode.add_argument("--status", action="store_true", help="各層的待補數與今天的背景用量（唯讀）")
    ap.add_argument("--dry-run", action="store_true", help="只列出會排入的份數與估計 token，不呼叫 LLM")
    ap.add_argument("--scheduled", action="store_true",
                    help="排程呼叫：第三層在 profile 的 background_enabled = false 時不跑")
    ap.add_argument("--chunk", type=_positive, default=DEFAULT_CHUNK, help="每批幾份（批與批之間放開 LLM 批次鎖）")
    ap.add_argument("--max-reports", type=_positive, default=None, help="本次最多排入幾份（第三層預設取 profile）")
    ap.add_argument("--token-limit", type=_positive, default=None, help="本次的 token 上限（估計＋實測）")
    ap.add_argument("--workers", type=_positive, default=None, help="傳給各批次的 --workers（預設用各批次的預設）")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.profile import get_profile

    bf = get_profile().backfill
    today = bt.today_taipei()
    cutoff = bt.recent_cutoff(today, bf.recent_months)
    models = task_models()
    directory = bt.work_dir()
    state_file = bt.state_path()

    if args.status:
        rows = asyncio.run(fetch_status(cutoff, models))
        print_status(rows, cutoff=cutoff, months=bf.recent_months, state=bt.load_state(state_file).for_day(today),
                     background=bf.background_enabled, daily_limit=bf.daily_token_limit)
        return 0
    tier = args.tier
    if tier == bt.TIER_OLDER and args.scheduled and not bf.background_enabled:
        print("背景補齊未啟用（profile [backfill].background_enabled = false）→ 不跑")
        return 0
    if not args.dry_run:  # --dry-run 不呼叫 LLM
        require_llm_key(models)

    background = tier == bt.TIER_OLDER
    max_reports = args.max_reports or (bf.max_reports_per_round if background else None)

    # 一個事件迴圈用到底：連線池綁在建立它的迴圈上，每次 asyncio.run 都開新迴圈會讓池裡的連線失效。
    loop = asyncio.new_event_loop()

    def fetch(exclude: set[str], limit: int) -> list[bt.Candidate]:
        return loop.run_until_complete(fetch_candidates(tier, cutoff, models, exclude, limit))

    def run(cmd: Sequence[str]) -> int:
        return subprocess.run(list(cmd), cwd=ROOT).returncode

    def go() -> Outcome:
        return run_backfill(tier=tier, fetch=fetch, run=run, chunk_size=args.chunk, max_reports=max_reports,
                            token_limit=args.token_limit, dry_run=args.dry_run, work_dir=directory,
                            usage_log=usage_log_path(), state_file=state_file if background else None,
                            daily_limit=bf.daily_token_limit if background else None, today=today,
                            workers=args.workers)
    scope = f"第{tier}層（report_date {'≥' if tier == bt.TIER_RECENT else '<'} {cutoff}" + (
        "，含 NULL）" if background else "）")
    print(f"{scope}；每批 {args.chunk} 份；份數上限 {max_reports or '無'}；"
          f"token 上限 {args.token_limit or '無'}" + (f"；每日上限 {bf.daily_token_limit}" if background else ""))
    try:
        if args.dry_run:
            out = go()
        else:
            try:
                with orchestrator_lock(directory / LOCK_NAME):
                    if background:
                        try:
                            os.nice(BACKGROUND_NICE)
                        except OSError:
                            pass
                    out = go()
            except OrchestratorBusy as exc:
                print(f"{exc}，本次不啟動", file=sys.stderr)
                return LOCK_BUSY_RC
    finally:
        try:
            from app.services.db import engine

            loop.run_until_complete(engine.dispose())
        except Exception:  # noqa: BLE001 — 收尾失敗不影響結果
            pass
        loop.close()
    why = {None: "沒有待補的研報了", "reports": "碰到份數上限", "tokens": "碰到 token 上限",
           "lock_busy": "LLM 批次鎖被佔用", "env_abort": "子批次環境型中止"}[out.stopped_by]
    counts = "、".join(f"{t} {out.task_counts[t]}" for t in bt.DERIVED_TASKS if t in out.task_counts) or "無"
    verb = "[dry-run] 會排入" if args.dry_run else "排入"
    unit = "估計" if args.dry_run else "入帳"
    print(f"{verb} {out.reports} 份（{out.batches} 批；待補：{counts}），{unit} {out.tokens} token；結束原因：{why}")
    return out.rc


if __name__ == "__main__":
    sys.exit(main())
