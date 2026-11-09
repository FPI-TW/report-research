"""連接器輪詢入庫：profile `[connectors].enabled` 的每個連接器 → 落地新檔 → 掃描 → 逐篇 `ingest_one`。

連接器只負責把新檔落地、附上 metadata 側檔（`app/services/connectors/`）；之後與 sync、上傳 worker 共用
`scripts/_ingest_core.ingest_one`（以 file_hash 去重）。這支依它回的 `Outcome` 計數、寫失敗清單與跳過名單、
更新各連接器的處理紀錄、寫本輪 hashes（驅動下游摘要、標題、摘錄；`scripts/connector_sync.py` 串起整條鏈）。

重跑不重複入庫，兩道保險：
1. 處理紀錄（`<CONNECTOR_DATA_DIR>/state/<名>.json`）只記「重跑不會不一樣」的結果（`base.TERMINAL_KINDS`
   與側檔不合格），指紋含檔案大小、mtime 與側檔內容；`fail`、`skip_untagged` 不記，下一輪重試。
2. 就算紀錄遺失，`ingest_one` 以 file_hash 判斷已入庫（`skip_exists`），不會再入庫一次。

側檔不合格的檔案不入庫，寫進失敗清單（`<CONNECTOR_DATA_DIR>/failures.log`，`連接器<TAB>相對路徑<TAB>階段
<TAB>原因`）並計成異常；改好側檔後指紋改變，下一輪自動重送。

用法：
  uv run python scripts/ingest_connectors.py                  # 輪詢到期的連接器
  uv run python scripts/ingest_connectors.py --force          # 不管輪詢間隔
  uv run python scripts/ingest_connectors.py --only inbox --dry-run

退出碼：0 跑完（逐篇失敗看計數檔的 abnormal）；2 帳號／環境型中止（同 sync）；75 批次鎖被佔用。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services.llm_models import TASK_TAG  # noqa: E402
from scripts._claude_cli import (  # noqa: E402
    BadRequestEscalation,
    CliNotFoundError,
    record_escalation,
)
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402
from scripts._ingest_core import TAG_MODEL, TAGS_DIR, ingest_one  # noqa: E402
from scripts.sync_new_reports import partial_hashes_on_abort, write_ingested_hashes  # noqa: E402

COUNTERS = (
    "polled", "fetched", "fetch_fail", "connector_error", "candidates", "ingested", "chunks", "skip_admin",
    "skip_scanned", "skip_exists", "skip_untagged", "skip_blocked", "skip_truncated", "skip_non_research", "fail",
    "sidecar_invalid", "tag_skipped", "cache_fail",
)
# 「本該入庫卻沒進 DB」或「來源壞了」：分類理由同 scripts/sync_new_reports.py 的 ABNORMAL_COUNTERS，
# 另加側檔不合格（提供方要改）、落地失敗與連接器建不起來（來源斷了）。
ABNORMAL_COUNTERS = (
    "fail", "skip_untagged", "skip_blocked", "skip_truncated", "sidecar_invalid", "fetch_fail", "connector_error",
)
HASHES_NAME = "last_hashes"
STATS_NAME = "last_stats"
FAILURES_NAME = "failures.log"
# 狀態檔每處理這麼多篇存一次（中途被殺最多重抽這麼多篇的字，不會重複入庫）。
SAVE_EVERY = 20


def write_stats(path: Path, stats: dict) -> None:
    """本輪計數器 → `key=value`（原子寫入），另寫 `abnormal`（＝ABNORMAL_COUNTERS 之和）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={int(v)}" for k, v in stats.items()]
    lines.append(f"abnormal={sum(int(stats.get(k, 0)) for k in ABNORMAL_COUNTERS)}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_stats(path: Path) -> dict[str, int]:
    """`write_stats` 的反向；缺檔或格式壞回空 dict。"""
    try:
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key and value.strip().lstrip("-").isdigit():
                out[key] = int(value)
        return out
    except OSError:
        return {}


def append_failure(path: Path, connector: str, rel: str, stage: str, reason: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    reason = (reason or "").replace("\t", " ").replace("\n", " ")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{connector}\t{rel}\t{stage}\t{reason}\n")


def hashes_out_path(args) -> Path:
    from app.services.connectors import data_dir

    return Path(args.hashes_out) if args.hashes_out else data_dir() / HASHES_NAME


async def _ingest_connector(spec, session, *, args, storage, stats, hashes, tagged_paths, recorder, failures):
    """單一連接器：落地 → 掃描 → 逐篇入庫 → 更新處理紀錄。BadRequestEscalation 照原樣往上拋。"""
    import asyncio

    from app.services.connectors import ConnectorError, build, state_path
    from app.services.connectors.base import SIDECAR_INVALID, TERMINAL_KINDS, ConnectorState, scan

    state = ConnectorState.load(state_path(spec.name))
    try:
        connector = build(spec)
        report = await asyncio.to_thread(connector.fetch)
    except ConnectorError as exc:
        stats["connector_error"] += 1
        append_failure(failures, spec.name, "-", "connector", str(exc))
        print(f"[{spec.name}] 連接器無法使用：{exc}", flush=True)
        return
    except Exception as exc:  # noqa: BLE001 — 一個來源壞掉不擋其他來源
        stats["fetch_fail"] += 1
        append_failure(failures, spec.name, "-", "fetch", repr(exc))
        print(f"[{spec.name}] 落地失敗：{exc!r}", flush=True)
        return
    stats["polled"] += 1
    stats["fetched"] += report.landed
    stats["fetch_fail"] += report.failed
    for err in report.errors:
        append_failure(failures, spec.name, "-", "fetch", err)
    print(f"[{spec.name}] 新落地 {report.landed} 檔、落地失敗 {report.failed}", flush=True)

    pending = [c for c in scan(connector.root, settle_seconds=spec.settle_seconds) if not state.is_done(c)]
    if args.limit:
        pending = pending[: args.limit]
    stats["candidates"] += len(pending)
    print(f"[{spec.name}] 待處理 {len(pending)} 檔（dry_run={args.dry_run}）", flush=True)
    dirty = 0
    try:
        for cand in pending:
            if cand.sidecar_error:
                stats["sidecar_invalid"] += 1
                print(f"  [{spec.name}] 側檔不合格，不入庫：{cand.sidecar_error}", flush=True)
                if not args.dry_run:
                    append_failure(failures, spec.name, cand.rel, "sidecar", cand.sidecar_error)
                    state.record(cand, SIDECAR_INVALID, None)
                    dirty += 1
                continue
            out = await ingest_one(
                session, cand.path, storage=storage, batch_size=args.batch_size, dry_run=args.dry_run,
                tags_dir=TAGS_DIR, tagged_paths=tagged_paths, on_committed=hashes.append,
                metadata=cand.metadata, connector=spec.name,
            )
            if out.kind == "missing":
                continue
            stats["ingested" if out.kind == "would_ingest" else out.kind] += 1
            if out.tag_skipped:
                stats["tag_skipped"] += 1
            if out.stage is not None:
                append_failure(failures, spec.name, cand.rel, out.stage,
                               f"{out.reason}（file_hash={out.file_hash}）" if out.file_hash else out.reason)
            if out.failure_kind:
                rec = await recorder()
                if rec is not None:
                    await rec.record(out.file_hash, out.failure_kind)
            if out.kind == "ingested":
                stats["chunks"] += out.chunks
                if not out.cache_written:
                    stats["cache_fail"] += 1
                print(f"  [{spec.name}] [{out.market}] {cand.rel[:60]} ({out.chunks} chunks)", flush=True)
            elif out.kind == "would_ingest":
                print(f"  [{spec.name}] [DRY] would ingest: {cand.rel[:60]}", flush=True)
            if not args.dry_run and out.kind in TERMINAL_KINDS:
                state.record(cand, out.kind, out.file_hash)
                dirty += 1
                if dirty >= SAVE_EVERY:
                    state.save()
                    dirty = 0
    finally:
        if not args.dry_run:
            # 被中止也存：已入庫的篇記成處理過了（就算沒記到，下一輪也只是 skip_exists）。
            # 落地失敗的連接器在上面就 return 了、不更新 last_poll：下一次排程就重試，不等滿一個間隔。
            state.last_poll = time.time()
            state.save()


async def _run(args) -> dict:
    from sqlalchemy import text as sql_text

    from app.config import get_settings
    from app.profile import get_profile
    from app.services import llm_failures
    from app.services.connectors import data_dir, due_specs
    from app.services.db import SessionFactory, relax_statement_timeout
    from app.services.object_storage import get_object_storage

    get_settings()
    profile = get_profile()
    specs = profile.connectors.enabled_specs()
    if args.only:
        unknown = sorted(set(args.only) - {s.name for s in specs})
        if unknown:
            raise SystemExit(f"--only 指定的連接器沒有啟用：{'、'.join(unknown)}（profile [connectors].enabled）")
        specs = [s for s in specs if s.name in args.only]
    stats = {k: 0 for k in COUNTERS}
    hashes: list[str] = []
    failures = data_dir() / FAILURES_NAME
    due, broken = due_specs(specs, force=args.force)
    for spec, err in broken:
        stats["connector_error"] += 1
        append_failure(failures, spec.name, "-", "state", err)
        print(f"[{spec.name}] {err}（修好或移走狀態檔前這個連接器不跑）", flush=True)
    if not specs:
        print("profile 沒有啟用任何連接器（[connectors].enabled 為空）", flush=True)
    elif not due and not broken:
        print("沒有到輪詢時間的連接器", flush=True)
    storage = get_object_storage() if due else None
    tagged_paths: dict[str, Path] = {}
    recorder_box: list = []

    async def _recorder():
        if not recorder_box:
            recorder_box.append(await llm_failures.open_recorder(llm_failures.TASK_TAG, TAG_MODEL, SessionFactory))
        return recorder_box[0]

    t0 = time.time()
    with partial_hashes_on_abort(hashes_out_path(args), hashes, enabled=not args.dry_run):
        if due:
            async with SessionFactory() as session:
                for spec in due:
                    try:
                        await _ingest_connector(spec, session, args=args, storage=storage, stats=stats,
                                                hashes=hashes, tagged_paths=tagged_paths, recorder=_recorder,
                                                failures=failures)
                    except BadRequestEscalation as exc:
                        from scripts.sync_new_reports import write_bad_request_hashes

                        await record_escalation(exc, await _recorder())
                        write_bad_request_hashes(hashes_out_path(args), exc.file_hashes, tagged_paths)
                        raise
                if stats["ingested"] and not args.dry_run:
                    await relax_statement_timeout(session)
                    await session.execute(sql_text("ANALYZE research.report_chunk"))
                    await session.execute(sql_text("ANALYZE research.research_report"))
                    await session.commit()

    if not args.dry_run:
        write_ingested_hashes(hashes_out_path(args), hashes)
        write_stats(data_dir() / STATS_NAME, stats)
    print("\n=== connector summary ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    print(f"  elapsed: {time.time() - t0:.0f}s", flush=True)
    return stats


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="連接器輪詢入庫（profile [connectors]）")
    ap.add_argument("--only", action="append", help="只跑這個連接器（可重複）")
    ap.add_argument("--force", action="store_true", help="不管輪詢間隔，所有啟用的連接器都跑")
    ap.add_argument("--dry-run", action="store_true", help="落地照做，入庫只走到標註前；不呼叫 LLM、不寫 DB 與紀錄")
    ap.add_argument("--limit", type=int, default=None, help="每個連接器本輪最多處理幾檔")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--hashes-out", default=None,
                    help="本輪入庫 hashes 改寫到這個檔（預設 <CONNECTOR_DATA_DIR>/last_hashes）")
    return ap


def main() -> None:
    import asyncio

    args = build_parser().parse_args()
    # 取鎖之前：缺金鑰或模型名打錯是「跑了也白跑」，要在撞鎖（rc=75＝不跑）之前說出來。
    if not args.dry_run:
        require_llm_key({TASK_TAG: TAG_MODEL})
    with claude_cli_lock_or_exit("ingest_connectors"):
        try:
            asyncio.run(_run(args))
        except CliNotFoundError as exc:
            print(f"中止：{exc}", flush=True)
            raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
