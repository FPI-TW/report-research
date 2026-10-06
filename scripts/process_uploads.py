"""研報上傳 worker（Admin v1.5 上傳管線 PR-5）：`scan` → `ingest` → `cleanup` 三個子命令。

排程殼是 `scripts/process_uploads.sh`（systemd `report-mark-upload.service`，timer 每 5 分鐘、`Persistent=false`；
ops 另有「立即執行」）。殼取整輪鎖 `data/.upload_worker.lock`、依序跑三個子命令，`ingest` 有新草稿時另以
`--hashes-file` 跑摘要、標題、摘錄（各自取 claude 鎖，不巢狀）。狀態機、草稿原子性、清除守門的說明在
`app/services/upload_worker.py` 檔頭；這支只做入庫那一步（要呼叫 `scripts/_ingest_core.ingest_one`，會打 LLM）
與子命令的收場。

子命令（每個都先取整輪鎖——殼傳下來的或自己取的——並回收上一輪被殺的 scanning／processing 殘留）：

- `scan`：零 LLM、不取 claude 鎖。`quarantined` → 重算 SHA → ClamAV（`app/services/clamd.scan`）→
  clean／infected／blocked，或退回 quarantined（暫時性錯誤，永不放行）。
- `ingest [--hashes-out 檔]`：有 `clean` 才動。依序：backfill 正在跑 → 只掃描、本輪不入庫（rc 0）；斷路器有效
  → 全部 `clean` 標 `failure_kind=llm_breaker`（延後，rc 0）；`require_llm_key` → 取 claude 鎖（撞鎖 rc 75，
  乾淨檔維持 clean）→ 逐筆認領 → 語料已有同 hash 轉 duplicate → 子行程入庫前檢查（主動內容、加密、頁數、
  試抽字；`app/services/pdf_preflight.py`）→ 搬正到 `data/uploads/clean/<hash>/<原始檔名>` → `ingest_one`
  （`pre_upsert` 寫草稿）→ 依 `Outcome` 轉 failed。被擋的標註照 sync 記 `research.llm_task_failure`。
  `--hashes-out` 一定會寫（空清單＝0-byte），中途中止也寫已 commit 的那幾篇。
- `cleanup`：寬限期已過的退回件（守門成立時連語料、visibility、extraction_log、快取、標籤、本機乾淨檔、
  R2 原檔一起刪；刪語料時持 claude 鎖與 sync 互斥，拿不到就留到下一輪）、過保留期的感染證據、隔離區孤兒檔。

退出碼（分界是「會不會自己好」：會自己好的不告警，不會的告警）：
  0  做完了（含「另一輪在跑」「backfill 在跑」「斷路器延後」這類刻意不做）
  1  這支自己壞了（例外、SQL 錯誤、隔離區權限）→ OnFailure 告警
  2  這輪不跑、下一輪自然會好：DB 連不上（由 web 探針經 P5 告警）→ unit 列為成功
  3  LLM 設定或帳號錯誤、不會自己好：缺金鑰、環境檔讀不到（不存在／權限）、`LLM_PROVIDER` 拼錯、未知模型、
     重複的鍵（`require_llm_key` 拒跑），以及途中的 401／402／400 升級／CLI 跑不起來 → OnFailure 告警。
     只有真的有上傳在等（有 `clean`）時才會走到這一步，所以沒有上傳時不會每 5 分鐘告警一次。
  75 claude 鎖被其他批次佔用（下一輪再試）→ unit 列為成功

用法：
  uv run python scripts/process_uploads.py scan
  uv run python scripts/process_uploads.py ingest --hashes-out data/.upload_last_hashes
  uv run python scripts/process_uploads.py cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._llm_env import breaker_active, load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與 TAG_MODEL 都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services import quarantine, uploads  # noqa: E402
from app.services import upload_worker as uw  # noqa: E402
from app.services.llm_models import TASK_TAG  # noqa: E402
from scripts._claude_cli import BadRequestEscalation, CliNotFoundError, record_escalation  # noqa: E402
from scripts._claude_lock import ClaudeCliBusyError, claude_cli_lock, claude_cli_lock_or_exit  # noqa: E402
from scripts._ingest_core import TAG_MODEL, TAGS_DIR, ingest_one  # noqa: E402

RC_OK = 0
RC_FAIL = 1
RC_ENV = 2  # 這輪不跑、會自己好（DB 連不上）：unit 的 SuccessExitStatus 放行
RC_LLM_CONFIG = 3  # LLM 設定或帳號錯誤、不會自己好：不在 SuccessExitStatus，走 OnFailure 告警
INGEST_BATCH = 20  # 每輪最多入庫幾筆（每 5 分鐘一輪；一筆含標註與嵌入約數十秒）

# `Outcome` → (state, failure_kind)。`ingested` 由 pre_upsert 在同一個交易裡轉 draft，不在這裡。
_FAILURES = {
    "skip_admin": uploads.FAILURE_ADMIN_FILE,
    "skip_scanned": uploads.FAILURE_SCANNED,
    "skip_untagged": uploads.FAILURE_TAG_FAILED,
    "skip_blocked": uploads.FAILURE_TAG_BLOCKED,
    "skip_truncated": uploads.FAILURE_TAG_TRUNCATED,
    "skip_non_research": uploads.FAILURE_NOT_RESEARCH,
    "missing": uploads.FAILURE_EXTRACT_ERROR,
}
_FAIL_STAGE = {"extract": uploads.FAILURE_EXTRACT_ERROR, "ingest": uploads.FAILURE_INGEST_ERROR}
_DETAIL = {
    "skip_admin": "檔名判定為行政文件",
    "skip_scanned": "抽不出文字（掃描影像 PDF）",
    "skip_non_research": "標註判定非研究報告或沒有市場",
    "missing": "乾淨檔不見了",
}


def _say(msg: str) -> None:
    print(f"[process_uploads] {msg}", flush=True)


def _db_unavailable(exc: BaseException) -> bool:
    from sqlalchemy.exc import InterfaceError, OperationalError

    return isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror, OperationalError, InterfaceError))


@dataclass
class Ctx:
    """一輪 worker 的相依（測試換成假物件）。"""

    session_factory: object
    qroot: Path
    clean_dir: Path
    lock_path: Path
    tags_dir: Path = TAGS_DIR
    cache_dir: Optional[Path] = None
    storage: object = None  # None → get_object_storage()
    scanner: Optional[Callable] = None  # None → clamd.scan
    notify: Callable = uw.notify_infected
    preflight: Optional[Callable] = None  # None → pdf_preflight.run_preflight（設定的記憶體上限）
    backfill_probe: Callable[[], Optional[str]] = uw.backfill_running
    claude_lock_path: Optional[Path] = None  # None → scripts/_claude_lock.py 的預設鎖檔
    recorder_factory: Optional[Callable] = None  # None → llm_failures.open_recorder
    engine: object = None  # 有給就在每個子命令收尾時 dispose（連線池綁在 asyncio.run 的事件迴圈上）


def default_ctx() -> Ctx:
    from app.config import get_settings
    from app.services.db import SessionFactory, engine

    s = get_settings()
    return Ctx(session_factory=SessionFactory, qroot=quarantine.quarantine_dir(s), clean_dir=uw.clean_root(s),
               lock_path=uw.lock_file(s), engine=engine)


def _storage(ctx: Ctx):
    if ctx.storage is not None:
        return ctx.storage
    from app.services.object_storage import get_object_storage

    return get_object_storage()


def _preflight(ctx: Ctx, path: Path):
    if ctx.preflight is not None:
        return ctx.preflight(path)
    from app.config import get_settings
    from app.services.pdf_preflight import run_preflight

    return run_preflight(path, memory_mb=get_settings().upload_preflight_memory_mb)


def _run(ctx: Ctx, coro):
    """一個子命令只跑一次 `asyncio.run`（連線池綁在事件迴圈上）。DB 連不上回 RC_ENV；其他例外原樣拋出（rc 1）。"""

    async def wrapped():
        try:
            return await coro
        finally:
            if ctx.engine is not None:
                await ctx.engine.dispose()

    try:
        return asyncio.run(wrapped())
    except Exception as exc:  # noqa: BLE001
        if _db_unavailable(exc):
            _say(f"DB 不可用（{type(exc).__name__}: {exc}），本輪不跑")
            return RC_ENV
        raise


async def _recover(ctx: Ctx) -> uw.Recovery:
    async with ctx.session_factory() as session:
        rec = await uw.recover_stale(session)
    if rec.scanning or rec.processing or rec.gave_up:
        _say(f"回收上一輪的殘留：scanning→quarantined {rec.scanning}、processing→clean {rec.processing}、"
             f"中止達上限轉 failed {rec.gave_up}")
    return rec


# ── scan ────────────────────────────────────────────────────────────────


async def scan_round(ctx: Ctx, *, limit: int) -> int:
    from app.services import clamd

    await _recover(ctx)
    st = await uw.scan_pending(
        ctx.session_factory, qroot=ctx.qroot, scanner=ctx.scanner or clamd.scan, notify=ctx.notify, limit=limit,
    )
    _say(f"掃描：認領 {st.claimed}、乾淨 {st.clean}、感染 {st.infected}、攔截 {st.blocked}、退回重試 {st.retry}")
    return RC_OK


def cmd_scan(args, ctx: Optional[Ctx] = None) -> int:
    ctx = ctx or default_ctx()
    with uw.round_lock(ctx.lock_path) as held:
        if not held:
            _say("另一輪 worker 正在跑，本次不啟動")
            return RC_OK
        return _run(ctx, scan_round(ctx, limit=args.limit))


# ── ingest ──────────────────────────────────────────────────────────────


@dataclass
class IngestStats:
    claimed: int = 0
    drafts: int = 0
    failed: int = 0
    duplicate: int = 0
    blocked: int = 0
    deferred: int = 0
    by_kind: dict = field(default_factory=dict)


async def _fail(session, st: IngestStats, upload_id: str, kind: str, detail: Optional[str]) -> None:
    if await uw.finish(session, upload_id, uploads.STATE_FAILED, failure_kind=kind, detail=detail):
        st.failed += 1
        st.by_kind[kind] = st.by_kind.get(kind, 0) + 1


async def _process_one(ctx: Ctx, session, item: uw.CleanItem, storage, hashes: list[str], tagged: dict,
                       recorder, st: IngestStats) -> None:
    """一筆 processing 的上傳走到終點（draft／failed／duplicate／blocked）。LLM 環境型例外原樣往上拋。"""
    h, uid = item.file_hash, item.upload_id
    if await uw.corpus_has(session, h):
        # 認領時語料已有同 hash（例如 NAS 先送到）：轉 duplicate，**不碰 visibility**。
        if await uw.finish(session, uid, uploads.STATE_DUPLICATE, detail="語料已有同一份檔案"):
            st.duplicate += 1
        uw.remove_quiet(quarantine.bin_path(ctx.qroot, uid))
        hash_dir = ctx.clean_dir / h
        if hash_dir.exists() and not await uw.corpus_uses_dir(session, hash_dir):
            uw.remove_quiet(hash_dir)
        await session.rollback()
        return
    await session.rollback()
    dst = uw.clean_file(ctx.clean_dir, h, item.original_name)
    src = quarantine.bin_path(ctx.qroot, uid)
    if dst.exists():
        # 重試（tag_failed／ingest_error）：上一次已通過檢查並搬正。仍重算一次 SHA。
        if await asyncio.to_thread(uw.sha256_file, dst) != h:
            uw.remove_quiet(dst)
            if await uw.finish(session, uid, uploads.STATE_BLOCKED, failure_kind=uploads.FAILURE_HASH_MISMATCH,
                               detail="乾淨檔的 SHA-256 與 DB 不符"):
                st.blocked += 1
            return
        uw.remove_quiet(src)
    elif src.exists():
        pre = await asyncio.to_thread(_preflight, ctx, src)
        if not pre.ok:
            await _fail(session, st, uid, pre.kind or uploads.FAILURE_EXTRACT_ERROR, pre.detail)
            return
        try:
            await asyncio.to_thread(uw.move_to_clean, src, dst, h, item.client_mtime)
        except uw.HashMismatchError as exc:
            if await uw.finish(session, uid, uploads.STATE_BLOCKED, failure_kind=uploads.FAILURE_HASH_MISMATCH,
                               detail=str(exc)):
                st.blocked += 1
            return
    else:
        await _fail(session, st, uid, uploads.FAILURE_EXTRACT_ERROR, "隔離區與乾淨檔目錄都找不到這份檔案")
        return

    out = await ingest_one(
        session, dst, storage=storage, tags_dir=ctx.tags_dir, tagged_paths=tagged,
        pre_upsert=uw.draft_hook(uid), on_committed=hashes.append,
    )
    if out.failure_kind:
        # DeepSeek 的內容型失敗（審查、截斷、空回應、400）照 sync 記入跳過名單（make llm-blocked 列得出來）。
        rec = await recorder.get()
        if rec is not None:
            await rec.record(out.file_hash, out.failure_kind)
    if out.kind == "ingested":
        st.drafts += 1
        _say(f"草稿：{uid}（{out.market}，{out.chunks} chunks）")
        return
    await session.rollback()
    if out.kind == "skip_exists":
        if await uw.finish(session, uid, uploads.STATE_DUPLICATE, detail="語料已有同一份檔案"):
            st.duplicate += 1
        return
    kind = _FAILURES.get(out.kind) or _FAIL_STAGE.get(out.stage or "", uploads.FAILURE_INGEST_ERROR)
    detail = out.reason or _DETAIL.get(out.kind)
    await _fail(session, st, uid, kind, detail)


class _Recorder:
    """跳過名單的寫入端：第一次需要時才開（fail-open，表不存在回 None）。"""

    def __init__(self, ctx: Ctx):
        self.ctx, self._box = ctx, []

    async def get(self):
        if not self._box:
            if self.ctx.recorder_factory is not None:
                rec = await self.ctx.recorder_factory()
            else:
                from app.services import llm_failures

                rec = await llm_failures.open_recorder(llm_failures.TASK_TAG, TAG_MODEL, self.ctx.session_factory)
            self._box.append(rec)
        return self._box[0]


async def ingest_round(ctx: Ctx, *, limit: int, hashes: list[str]) -> IngestStats:
    st = IngestStats()
    recorder = _Recorder(ctx)
    storage = _storage(ctx)
    tagged: dict = {}
    async with ctx.session_factory() as session:
        for upload_id in await uw.list_clean(session, limit):
            item = await uw.claim_for_processing(session, upload_id)
            if item is None:
                continue  # 被退回了
            st.claimed += 1
            try:
                await _process_one(ctx, session, item, storage, hashes, tagged, recorder, st)
            except BadRequestEscalation as exc:
                # 400 升級（≥2 篇不同研報收到相同的 400）：請求或設定壞了。觸發篇記跳過名單、這一筆轉 failed，
                # 整批中止（rc 3，告警）；其餘 clean 留著，修好之後下一輪照常。
                await session.rollback()
                await record_escalation(exc, await recorder.get())
                await _fail(session, st, upload_id, uploads.FAILURE_TAG_FAILED, f"400 升級中止：{exc}")
                raise
            except CliNotFoundError as exc:
                # LLM 環境型失敗（斷路器、401／402、設定）：這一筆退回 clean。斷路器＝延後（rc 0）；其他 rc 3（告警）。
                await session.rollback()
                tripped = breaker_active()
                if tripped:
                    await uw.back_to_clean(session, upload_id, failure_kind=uploads.FAILURE_LLM_BREAKER,
                                           detail=f"LLM 斷路器跳脫，延後處理：{tripped}")
                    # defer_all_clean 也會數到剛退回 clean 的這一筆
                    st.deferred += await uw.defer_all_clean(session, detail=f"LLM 斷路器跳脫，延後處理：{tripped}")
                    _say(f"LLM 斷路器跳脫：{st.deferred} 筆延後到斷路器過期後再處理")
                    return st
                await uw.back_to_clean(session, upload_id, detail=f"LLM 環境錯誤，未處理：{exc}")
                raise
            except Exception as exc:  # noqa: BLE001 — 單筆的意外不中斷整輪；DB 掛了則往上拋（rc 2）
                if _db_unavailable(exc):
                    raise
                await session.rollback()
                uw.journal_error(f"上傳 {upload_id} 處理時發生未預期的錯誤：{exc!r}")
                await _fail(session, st, upload_id, uploads.FAILURE_INGEST_ERROR, repr(exc))
    return st


async def _defer_breaker(ctx: Ctx, tripped: str) -> int:
    async with ctx.session_factory() as session:
        n = await uw.defer_all_clean(session, detail=f"LLM 斷路器有效，延後處理：{tripped}")
    _say(f"LLM 斷路器有效：{n} 筆標為延後（llm_breaker），斷路器過期後再處理")
    return RC_OK


async def ingest_main(ctx: Ctx, *, limit: int, hashes: list[str]) -> int:
    await _recover(ctx)
    async with ctx.session_factory() as session:
        pending = await uw.count_clean(session)
    if not pending:
        _say("沒有等著入庫的上傳")
        return RC_OK
    why = ctx.backfill_probe()
    if why:
        # 記憶體保護（設計 1.1）：backfill 也載 BGE-M3、不取 claude 鎖，重疊時主機會有三份模型。
        _say(f"抽取回填正在跑（{why}）：本輪只掃描、不入庫，{pending} 筆維持 clean")
        return RC_OK
    tripped = breaker_active()
    if tripped:
        return await _defer_breaker(ctx, tripped)
    # 取鎖之前：缺金鑰或模型名打錯是「跑了也白跑」，要在撞鎖（rc=75＝不跑）之前說出來。
    try:
        require_llm_key({TASK_TAG: TAG_MODEL})
    except SystemExit as exc:
        if exc.code != 2:  # scripts/_llm_env.RC_CONFIG
            raise
        tripped = breaker_active()  # 斷路器剛好在上面的檢查之後跳脫：照樣是延後，不是設定錯誤
        if tripped:
            return await _defer_breaker(ctx, tripped)
        uw.journal_error(f"LLM 設定錯誤（原因見上方 [llm-env] 那一行），{pending} 筆上傳維持 clean；"
                         "修好之前每輪都會告警")
        return RC_LLM_CONFIG
    with claude_cli_lock_or_exit("process_uploads", ctx.claude_lock_path):
        try:
            st = await ingest_round(ctx, limit=limit, hashes=hashes)
        except CliNotFoundError as exc:
            # 環境層級（401／402、設定、400 升級、CLI 跑不起來）：每一篇都會踩到、不會自己好。這一筆已退回 clean。
            uw.journal_error(f"LLM 帳號或設定錯誤，入庫中止：{exc}")
            return RC_LLM_CONFIG
    _say(f"入庫：認領 {st.claimed}、草稿 {st.drafts}、失敗 {st.failed} {st.by_kind or ''}、"
         f"重複 {st.duplicate}、攔截 {st.blocked}、延後 {st.deferred}")
    return RC_OK


def cmd_ingest(args, ctx: Optional[Ctx] = None) -> int:
    ctx = ctx or default_ctx()
    hashes: list[str] = []
    out_path = Path(args.hashes_out) if args.hashes_out else None
    with uw.round_lock(ctx.lock_path) as held:
        if not held:
            # 不寫 hashes：那個檔屬於正在跑的那一輪，寫空檔會讓它的下游以為沒有新草稿。
            _say("另一輪 worker 正在跑，本次不啟動")
            return RC_OK
        try:
            return _run(ctx, ingest_main(ctx, limit=args.limit, hashes=hashes))
        finally:
            # 持鎖時一定寫（空清單＝0-byte）：中途中止（rc 2／3／75、例外）也留下已 commit 的那幾篇，殼據此跑下游。
            if out_path is not None:
                uw.write_hashes(out_path, hashes)


# ── cleanup ─────────────────────────────────────────────────────────────


async def cleanup_round(ctx: Ctx) -> int:
    from app.services.extraction import cache as extraction_cache

    await _recover(ctx)
    try:
        # 刪語料（研報、visibility、extraction_log）時與 sync 的入庫互斥。零 LLM，鎖只拿來排除並行寫入。
        with claude_cli_lock("process_uploads.cleanup", ctx.claude_lock_path):
            st = await uw.purge_rejected(
                ctx.session_factory, qroot=ctx.qroot, clean_dir=ctx.clean_dir, tags_dir=ctx.tags_dir,
                cache_dir=ctx.cache_dir or extraction_cache.CACHE_DIR, storage=_storage(ctx), corpus_lock_held=True,
            )
    except ClaudeCliBusyError:
        _say("claude 鎖被其他批次佔用：要連語料一起清的退回件留到下一輪，其餘照常")
        st = await uw.purge_rejected(
            ctx.session_factory, qroot=ctx.qroot, clean_dir=ctx.clean_dir, tags_dir=ctx.tags_dir,
            cache_dir=ctx.cache_dir or extraction_cache.CACHE_DIR, storage=_storage(ctx), corpus_lock_held=False,
        )
    st.evidence = await uw.purge_evidence(ctx.session_factory, qroot=ctx.qroot)
    st.orphans = await uw.purge_orphans(ctx.session_factory, qroot=ctx.qroot)
    _say(f"清除：退回件 {st.purged}（含語料 {st.corpus_purged}、延到下一輪 {st.deferred}）、"
         f"感染／攔截證據 {st.evidence}、孤兒檔 {st.orphans}")
    return RC_OK


def cmd_cleanup(args, ctx: Optional[Ctx] = None) -> int:
    ctx = ctx or default_ctx()
    with uw.round_lock(ctx.lock_path) as held:
        if not held:
            _say("另一輪 worker 正在跑，本次不啟動")
            return RC_OK
        return _run(ctx, cleanup_round(ctx))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研報上傳 worker（scan → ingest → cleanup）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="掃描隔離區（ClamAV；零 LLM）")
    p.add_argument("--limit", type=int, default=uw.SCAN_BATCH)
    p = sub.add_parser("ingest", help="入庫掃描通過的上傳（抽字、標註、嵌入；草稿）")
    p.add_argument("--limit", type=int, default=INGEST_BATCH)
    p.add_argument("--hashes-out", default=None, help="本輪入庫的 file_hash 寫到這個檔（驅動摘要、標題、摘錄）")
    sub.add_parser("cleanup", help="清除過寬限期的退回件、過保留期的感染證據、隔離區孤兒檔")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return {"scan": cmd_scan, "ingest": cmd_ingest, "cleanup": cmd_cleanup}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
