#!/usr/bin/env python3
"""檢索回歸檢查（零 LLM）：凍結題集的 top-k 對一次性基準快照。

用法（在 repo 根）：
    uv run python scripts/retrieval_regression.py capture            # 一次性擷取基準（已有基準要加 --force）
    uv run python scripts/retrieval_regression.py check              # 比對；timer 每日跑這個
    uv run python scripts/retrieval_regression.py check --json       # stdout 只印完整比對的 JSON

設計（量哪一層、如何不把新研報／隱藏／下架誤判成劣化、指標與門檻）在
`app/services/retrieval_regression.py` 的模組 docstring。

退出碼（`deploy/systemd/report-mark-retrieval-regression.service` 依此分流）：
    0  沒有劣化
    1  劣化（告警）：平均研報召回低於門檻，或崩掉的題數超過上限
    2  這次略過（不告警，unit 的 SuccessExitStatus=2）：DB 連不上、可用記憶體不足、sync 正在跑。
       都是會自己好的狀況；DB 掛掉本身由 web 探針經 P5 帶去重地告警，這裡再叫只是重複通知。
       略過那次不會蓋掉上一次的比對結果；連續略過超過 48 小時，管理頁會把結果標成過期。
    3  無法比對、要人處理（告警）：還沒有基準、基準壞了或與題集不可比、基準研報大半已下架或隱藏、
       嵌入模型載不起來、DB 回了非連線類的錯誤、其他未預期的例外。

**不取 `scripts/_claude_lock.py` 的鎖**：它不呼叫 LLM，那把鎖防的是重複計費、摘錄互撞與 DB 連線數；
取了反而會讓同時段的 LLM 批次（含 sync 輪內的摘要／標題／摘錄）以 rc=75 跳過——為了一支低優先的檢查
讓入庫鏈少跑一段，方向反了。它與 sync（匯入時載 BGE-M3）、夜間回填真正搶的是記憶體，處置是：
排程避開（timer 07:40）、sync 的 PID 檔被持有就略過（只讀 PID 檔、不碰任何鎖）、可用記憶體低於
`RETRIEVAL_REGRESSION_MIN_AVAILABLE_GIB` 就略過、unit 再以 `MemoryMax` 兜底。

這支只 import 檢索與嵌入層，不 import 任何 LLM 呼叫層（`tests/test_retrieval_regression.py` 以 AST 守），
所以不需要 `scripts._llm_env`。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.services import data_health  # noqa: E402
from app.services import retrieval_regression as rr  # noqa: E402
from eval.question_contract import load_dataset  # noqa: E402

EXIT_OK = 0
EXIT_DEGRADED = 1
EXIT_SKIPPED = 2
EXIT_ERROR = 3

# scripts/sync_new_reports.sh 的 PID 檔（`LOCK="data/.sync_new_reports.lock"`；是 PID 檔不是 flock）。
SYNC_PID_FILE = REPO_ROOT / "data" / ".sync_new_reports.lock"


class Skip(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class Fail(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


# ── 環境探測（可替換，測試餵假的）────────────────────────────────────────


def available_gib(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    """MemAvailable（GiB）；讀不到（非 Linux）回 None＝不擋。"""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


def sync_running(pid_file: Path = SYNC_PID_FILE) -> bool:
    """sync 的 PID 檔存在且那個行程還活著。只讀，不取任何鎖。"""
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_connection_failure(exc: BaseException) -> bool:
    """「連不上 DB」才回 True（比照 scripts/schema_baseline.py 的同名判斷：只追 `.orig` 與 `__cause__`）。

    帳密錯、庫名錯、SQL 錯都不算——那是設定或程式錯誤，每天重試也不會好，必須告警（rc=3）。
    """
    errors: tuple[type, ...] = (OSError,)
    try:
        from asyncpg import exceptions as ape

        errors += (ape.CannotConnectNowError, ape.ConnectionDoesNotExistError, ape.TooManyConnectionsError)
    except ImportError:  # pragma: no cover - asyncpg 是必要相依
        pass
    try:
        from sqlalchemy.exc import TimeoutError as PoolTimeout

        errors += (PoolTimeout,)
    except ImportError:  # pragma: no cover
        pass
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, errors):
            return True
        cur = getattr(cur, "orig", None) or cur.__cause__
    return False


def _db_error(exc: BaseException, what: str) -> Exception:
    if is_connection_failure(exc):
        return Skip(rr.REASON_DB_UNAVAILABLE, f"DB 連不上（{what}，{type(exc).__name__}），本次略過")
    return Fail(rr.REASON_QUERY_FAILED, f"DB 查詢失敗（{what}，{type(exc).__name__}: {exc}）")


def _default_deps() -> dict:
    from app.services.db import SessionFactory
    from app.services.embed import embed_query_cached
    from app.services.retrieval import hybrid_search

    return {"session_factory": SessionFactory, "embed_fn": embed_query_cached, "search_fn": hybrid_search}


# ── 共用：守門與跑題目 ─────────────────────────────────────────────────────


def _guards(settings, *, available_gib_fn: Callable[[], float | None], sync_running_fn: Callable[[], bool]) -> None:
    if sync_running_fn():
        raise Skip(rr.REASON_SYNC_RUNNING, "sync 正在跑（PID 檔被持有），本次略過，避免同時載入 BGE-M3")
    avail = available_gib_fn()
    need = settings.retrieval_regression_min_available_gib
    if avail is not None and avail < need:
        raise Skip(rr.REASON_LOW_MEMORY, f"可用記憶體 {avail:.1f} GiB < {need:g} GiB，本次略過（不跟 web 搶）")


async def _corpus_state(session_factory):
    try:
        async with session_factory() as session:
            return await rr.fetch_corpus_state(session)
    except Exception as exc:  # noqa: BLE001 — 分類後轉成 Skip／Fail
        raise _db_error(exc, "讀語料狀態") from exc


async def _search_all(questions, *, k: int, dense_scan: int, session_factory, embed_fn, search_fn) -> dict:
    """每題 embed → hybrid_search，回 {題號: 完整候選清單（scored_items）}。"""
    out = {}
    for q in questions:
        try:
            vec = await asyncio.to_thread(embed_fn, q["question"])
        except Exception as exc:  # noqa: BLE001
            raise Fail(rr.REASON_EMBED_FAILED, f"嵌入失敗（{q['id']}，{type(exc).__name__}: {exc}）") from exc
        try:
            async with session_factory() as session:
                scored = await search_fn(session, q["question"], vec, k=k, dense_scan=dense_scan,
                                         **dict(q.get("filters") or {}))
        except Exception as exc:  # noqa: BLE001
            raise _db_error(exc, f"檢索 {q['id']}") from exc
        out[q["id"]] = rr.scored_items(scored)
    return out


async def _meta(session_factory, hashes) -> dict:
    try:
        async with session_factory() as session:
            return await rr.fetch_report_meta(session, hashes)
    except Exception as exc:  # noqa: BLE001
        raise _db_error(exc, "讀研報狀態") from exc


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


# ── capture ────────────────────────────────────────────────────────────────


async def run_capture(
    path: Path, *, force: bool, as_of: datetime | None, settings=None, dataset_path: Path = rr.DATASET,
    session_factory=None, embed_fn=None, search_fn=None,
    available_gib_fn: Callable[[], float | None] = available_gib, sync_running_fn: Callable[[], bool] = sync_running,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    """一次性擷取基準。`as_of`（驗證用）＝假裝基準是在那個時點擷取的：之後入庫的研報不進基準。"""
    settings = settings or get_settings()
    if path.exists() and not force:
        print(f"已有基準 {path}；確定要換基準才加 --force（換了之後比的是新的尺）", file=sys.stderr)
        return EXIT_ERROR
    if session_factory is None:
        deps = _default_deps()
        session_factory, embed_fn, search_fn = deps["session_factory"], deps["embed_fn"], deps["search_fn"]
    try:
        questions, digest = load_dataset(dataset_path)
        _guards(settings, available_gib_fn=available_gib_fn, sync_running_fn=sync_running_fn)
        cutoff, n_reports = await _corpus_state(session_factory)
        if as_of is not None:
            cutoff = as_of
        k = settings.retrieval_regression_k
        dense_scan = settings.ask_dense_scan
        current = await _search_all(questions, k=k, dense_scan=dense_scan, session_factory=session_factory,
                                    embed_fn=embed_fn, search_fn=search_fn)
        if as_of is not None:
            meta = await _meta(session_factory, {i["file_hash"] for items in current.values() for i in items})
            current = {qid: [i for i in items if (m := meta.get(i["file_hash"])) is not None
                             and m.created_at is not None and m.created_at <= as_of]
                       for qid, items in current.items()}
    except Skip as s:
        print(s.message, file=sys.stderr)
        return EXIT_SKIPPED
    except Fail as f:
        print(f.message, file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"題集讀不了：{exc}", file=sys.stderr)
        return EXIT_ERROR
    doc = rr.build_baseline(
        questions=questions, items_by_id=current, dataset_sha256=digest, k=k, dense_scan=dense_scan,
        captured_at=now_fn(), corpus_cutoff=cutoff, corpus_reports=n_reports, simulated_as_of=as_of is not None,
    )
    rr.write_json_atomic(path, doc)
    print(f"基準已寫入 {path}：{len(questions)} 題、k={k}、dense_scan={dense_scan}、截點 {doc['corpus_cutoff']}")
    for q in doc["questions"]:
        n_items = len(q["items"])
        n_rep = len({i["file_hash"] for i in q["items"]})
        warn = "（沒有任何結果：這題比對時會被當成不可比）" if n_items == 0 else ""
        print(f"  {q['id']}  {n_items:>2} 片段／{n_rep:>2} 篇  {q['question'][:40]}{warn}")
    return EXIT_OK


# ── check ──────────────────────────────────────────────────────────────────


def _previous_comparison() -> dict | None:
    result, _ = data_health.read_result(rr.RESULT_NAME)
    comparison = (result or {}).get("comparison")
    return comparison if isinstance(comparison, dict) else None


def _write(exit_code: int, outcome: str, reason: str | None, message: str, comparison: dict | None,
           now: datetime) -> None:
    """結果檔（fail-open：寫不進去只警告，不改退出碼）。沒比對的那次沿用上一次的比對。"""
    data_health.write_result(rr.RESULT_NAME, {
        "result_version": rr.RESULT_VERSION,
        "finished_at": now.isoformat(),
        "exit_code": exit_code,
        "outcome": outcome,
        "reason": reason,
        "message": message[:500],
        "comparison": comparison if comparison is not None else _previous_comparison(),
    }, now=now)


def _render(comparison: dict) -> str:
    s = comparison["summary"]
    t = comparison["thresholds"]
    header = (f"{'題號':<6}{'研報召回':>8}{'不排除新研報':>12}{'片段召回':>8}{'RBO':>6}"
              f"{'新研報':>6}{'隱藏':>5}{'下架':>5}")
    lines = [header, "-" * 64]
    for q in comparison["questions"]:
        flag = "  ← 劣化" if q["degraded"] else ("  （不可比）" if not q["comparable"] else "")
        lines.append(f"{q['id']:<8}{_fmt(q['report_recall']):>8}{_fmt(q['raw_report_recall']):>14}"
                     f"{_fmt(q['chunk_recall']):>10}{_fmt(q['rbo']):>8}{q['excluded_new_reports']:>6}"
                     f"{q['hidden_reports']:>6}{q['removed_reports']:>6}{flag}")
    lines.append("-" * 64)
    lines.append(
        f"平均研報召回 {_fmt(s['mean_report_recall'])}（門檻 {t['min_mean_recall']:g}）  "
        f"不排除新研報 {_fmt(s['mean_raw_report_recall'])}  片段召回 {_fmt(s['mean_chunk_recall'])}  "
        f"RBO {_fmt(s['mean_rbo'])}"
    )
    lines.append(
        f"可比較 {s['comparable']}/{s['questions']} 題；劣化題數 {s['degraded_questions']}"
        f"（上限 {t['max_degraded_questions']}，單題門檻 {t['min_question_recall']:g}）；"
        f"排除新研報 {s['excluded_new_reports']}、隱藏 {s['hidden_reports']}、下架 {s['removed_reports']}"
    )
    return "\n".join(lines)


async def run_check(
    path: Path, *, settings=None, dataset_path: Path = rr.DATASET, json_out: bool = False,
    session_factory=None, embed_fn=None, search_fn=None,
    available_gib_fn: Callable[[], float | None] = available_gib, sync_running_fn: Callable[[], bool] = sync_running,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    settings = settings or get_settings()
    started = time.monotonic()
    try:
        try:
            questions, digest = load_dataset(dataset_path)
        except (OSError, ValueError) as exc:
            raise Fail(rr.REASON_DATASET_INVALID, f"題集讀不了：{exc}") from exc
        try:
            baseline = rr.load_baseline(path)
        except rr.BaselineMissing as exc:
            raise Fail(rr.REASON_NO_BASELINE,
                       f"還沒有基準（{path}）：先跑 uv run python scripts/retrieval_regression.py capture") from exc
        except rr.BaselineError as exc:
            raise Fail(rr.REASON_BASELINE_INVALID, f"基準檔不能用：{exc}") from exc
        problems = rr.compatibility_problems(baseline, dataset_sha256=digest, questions=questions)
        if problems:
            raise Fail(rr.REASON_INCOMPARABLE, "；".join(problems))
        _guards(settings, available_gib_fn=available_gib_fn, sync_running_fn=sync_running_fn)
        if session_factory is None:
            deps = _default_deps()
            session_factory, embed_fn, search_fn = deps["session_factory"], deps["embed_fn"], deps["search_fn"]
        await _corpus_state(session_factory)  # 先確認 DB 連得上，連不上就不必載模型
        k = baseline["params"]["k"]
        dense_scan = settings.ask_dense_scan
        by_id = {q["id"]: q for q in questions}
        asked = [by_id[bq["id"]] for bq in baseline["questions"]]
        current = await _search_all(asked, k=k, dense_scan=dense_scan, session_factory=session_factory,
                                    embed_fn=embed_fn, search_fn=search_fn)
        hashes = {i["file_hash"] for bq in baseline["questions"] for i in bq["items"]}
        hashes |= {i["file_hash"] for items in current.values() for i in items}
        meta = await _meta(session_factory, hashes)
    except Skip as s:
        print(s.message, file=sys.stderr)
        _write(EXIT_SKIPPED, rr.OUTCOME_SKIPPED, s.reason, s.message, None, now_fn())
        return EXIT_SKIPPED
    except Fail as f:
        print(f.message, file=sys.stderr)
        _write(EXIT_ERROR, rr.OUTCOME_ERROR, f.reason, f.message, None, now_fn())
        return EXIT_ERROR

    cutoff = data_health.parse_time(baseline.get("corpus_cutoff"))
    thresholds = rr.Thresholds.from_settings(settings)
    per_question = []
    for bq in baseline["questions"]:
        row = rr.compare_question(bq["items"], current[bq["id"]], meta, cutoff=cutoff, k=k)
        per_question.append({"id": bq["id"], "question": bq["question"], **row})
    summary = rr.summarize(per_question, thresholds)
    comparison = {
        "finished_at": now_fn().isoformat(),
        "duration_s": round(time.monotonic() - started, 1),
        "baseline": rr.baseline_summary(baseline),
        "dense_scan": dense_scan,
        "params_changed": dense_scan != baseline["params"]["dense_scan"],
        "thresholds": thresholds.as_dict(),
        "summary": summary,
        "questions": per_question,
    }
    if json_out:
        print(json.dumps(comparison, ensure_ascii=False))
    else:
        print(_render(comparison))
    if comparison["params_changed"]:
        print(f"注意：ASK_DENSE_SCAN={dense_scan} 與基準的 {baseline['params']['dense_scan']} 不同"
              "（比的是現在的問答設定；確認是刻意調整後應重新擷取基準）", file=sys.stderr)
    if summary["verdict"] == rr.VERDICT_INCOMPARABLE:
        msg = (f"可比較的題目只有 {summary['comparable']}/{summary['questions']}：基準研報大半已下架或隱藏，"
               "基準太舊，請重新擷取")
        print(msg, file=sys.stderr)
        _write(EXIT_ERROR, rr.OUTCOME_ERROR, rr.REASON_INCOMPARABLE, msg, comparison, now_fn())
        return EXIT_ERROR
    if summary["verdict"] == rr.VERDICT_DEGRADED:
        msg = (f"檢索劣化：平均研報召回 {_fmt(summary['mean_report_recall'])}、"
               f"劣化題 {', '.join(summary['degraded_ids']) or '無'}")
        print(msg, file=sys.stderr)
        _write(EXIT_DEGRADED, rr.OUTCOME_DEGRADED, None, msg, comparison, now_fn())
        return EXIT_DEGRADED
    msg = f"沒有劣化：平均研報召回 {_fmt(summary['mean_report_recall'])}"
    print(msg, file=sys.stderr if json_out else sys.stdout)
    _write(EXIT_OK, rr.OUTCOME_OK, None, msg, comparison, now_fn())
    return EXIT_OK


# ── 進入點 ─────────────────────────────────────────────────────────────────


def _parse_as_of(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="檢索回歸檢查（零 LLM）")
    sub = p.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="一次性擷取基準快照")
    cap.add_argument("--baseline", type=Path, default=None, help="基準檔路徑（預設 RETRIEVAL_REGRESSION_BASELINE）")
    cap.add_argument("--force", action="store_true", help="覆寫既有基準")
    cap.add_argument("--as-of", type=_parse_as_of, default=None,
                     help="驗證用：假裝在這個時點擷取（之後入庫的研報不進基準），ISO 時間")
    chk = sub.add_parser("check", help="對基準比對（timer 跑這個）")
    chk.add_argument("--baseline", type=Path, default=None, help="基準檔路徑（預設 RETRIEVAL_REGRESSION_BASELINE）")
    chk.add_argument("--json", action="store_true", help="stdout 只印完整比對的 JSON（表格與結論改走 stderr）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = args.baseline or rr.baseline_path()
    try:
        if args.cmd == "capture":
            return asyncio.run(run_capture(path, force=args.force, as_of=args.as_of))
        return asyncio.run(run_check(path, json_out=args.json))
    except Exception as exc:  # noqa: BLE001 — 未預期的例外不可落到 rc=1（那是「劣化」）
        traceback.print_exc()
        if args.cmd == "check":
            _write(EXIT_ERROR, rr.OUTCOME_ERROR, rr.REASON_UNEXPECTED, f"未預期的錯誤：{type(exc).__name__}",
                   None, datetime.now(timezone.utc))
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
