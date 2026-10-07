"""增量同步匯入：吃 rsync delta 檔清單 → 逐檔 extract→tag→ingest。

僅處理「本次新傳入」的檔（或 --all-local 全本地對 DB 補漏），以 file_hash
對 DB 去重；單檔失敗不中斷，記 data/sync_failures.log。
單篇流程在 `scripts/_ingest_core.py` 的 `ingest_one`（與上傳 worker 共用）；這支依它回的
`Outcome` 計數、寫失敗紀錄與跳過名單、記 hashes，最後 ANALYZE。
重型相依（embed/store/db…）延遲到 main() 內 import，讓純函式可被輕量測試。

用法：
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt
  uv run python scripts/sync_new_reports.py --all-local
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt --dry-run
  uv run python scripts/sync_new_reports.py --delta data/sync_delta_X.txt \
      --hashes-out data/sync_hashes_retained_X.txt      # 手動重放：hashes 另寫一份
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable

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

# 單篇「抽字 → 標註 → 入庫」在 scripts/_ingest_core.py（與上傳 worker 共用）；這支只管
# 目標清單、計數、失敗紀錄、跳過名單、hashes 與 ANALYZE。
from scripts._ingest_core import TAG_MODEL, TAGS_DIR, ingest_one  # noqa: E402

SRC_LOCAL = ROOT / "研報自動匯入"
FAIL_LOG = ROOT / "data" / "sync_failures.log"
INGESTED_HASHES_FILE = ROOT / "data" / ".sync_last_hashes"
STATS_FILE = ROOT / "data" / ".sync_last_stats"
EXTS = {".pdf", ".docx", ".doc"}

# **哪些計數器代表「這篇本來該入庫、卻沒進 DB」。**
#
# 分界不是看名字，是看「重跑會不會不一樣」：
#   - `skip_untagged`：標註的前置條件失敗（claude CLI 壞掉、逾時、回應無法解析）。
#     檔案本身沒問題，環境修好後重跑就會入庫 ⇒ **異常**。
#   - `skip_blocked`：行內標註被模型供應商的內容審查擋下（DeepSeek 的 content_filter）。
#     重跑不會不一樣（同一份輸入再送一次結果不變），但它確實是「本該入庫卻沒進 DB」，要人處理
#     （9/24 決策：只列清單、交人工，不做新的入庫路徑）⇒ **異常**：擋心跳、走既有告警鏈。
#     另記進 research.llm_task_failure（task=tag, reason=content_filter），`make llm-blocked` 列出；
#     路徑寫進 FAIL_LOG 的 `tag_blocked` 階段（failures_to_delta 預設不撈它）。處置見
#     docs/production_resilience.md「DeepSeek 批次的失敗處置」。
#   - `skip_truncated`：行內標註被截斷（DeepSeek 的 truncated＝`finish_reason=length`，`max_tokens` 用完）。
#     同 skip_blocked：重跑不會不一樣（同一份輸入、同一個上限），重送只是再付一次錢 ⇒ **異常**，
#     記進 llm_task_failure（reason=truncated），FAIL_LOG 階段 `tag_truncated`（failures_to_delta 預設
#     不撈）。處置是調 TAG_MAX_TOKENS 或手寫 tags 後用 `--stage tag_truncated` 單篇重放。
#     **期限型截斷（`timeout_streamed`：已吐字後碰到總期限）不算**，留在 skip_untagged／階段 `tag`：它可能只是
#     DeepSeek 暫時變慢，下一輪常常就好，歸 tag_truncated 會被 failures_to_delta 預設排除、永遠不重放。
#     空回應與一般 400 **刻意留在 skip_untagged**（補救指令會重送）：空回應多半是供應商端的偶發狀況，
#     下一輪常常就好了；400 在送出時就被拒、不產生輸出，重送幾乎不花錢，而且可能是修好請求之後
#     本來就該重打的那一批。
#   - `fail`：抽字或寫入 DB 拋例外。同上 ⇒ **異常**。
#   - `skip_admin`／`skip_non_research`：標註成功且明確判定不該入庫 ⇒ 預期。
#   - `skip_exists`：已在庫，冪等 ⇒ 預期。
#   - `skip_scanned`：掃描件抽不出文字，是檔案本身的性質，重跑一萬次也一樣。
#     把它算成異常會讓心跳因為語料裡固定存在的掃描件而**永遠**不更新，
#     而永遠紅的告警兩週內就會被當背景噪音（本 repo 已有兩次前例）⇒ 預期。
#     代價是它不留路徑紀錄，屬已知限制，見 docs/production_resilience.md。
#   - `cache_fail`：入庫 commit 之後寫抽取快取失敗（write_cache_fail_open）。**這篇已經在 DB、
#     也已記進 hashes**，下游照常；它不是「該入庫卻沒進 DB」，補救指令（failures_to_delta →
#     重放）對它無效（重放會 skip_exists）。算成異常會擋心跳、印出錯的補救指令 ⇒ **不算異常**，
#     只寫進 .sync_last_stats，由殼層在 >0 時印 WARNING（持續出現多半是磁碟滿或權限）。
ABNORMAL_COUNTERS = ("fail", "skip_untagged", "skip_blocked", "skip_truncated")

# 400 升級中止時（BadRequestEscalation），觸發的研報寫到 `<hashes_out>.bad_request`
# （`file_hash<TAB>路徑`），殼改名保留並印出——重放本輪 delta 前要先把它們從 delta 拿掉，
# 否則會再撞一次同樣的升級。
BAD_REQUEST_SUFFIX = ".bad_request"


def parse_rsync_delta(
    lines: Iterable[str], dst_root: Path, exts: set[str] = EXTS
) -> list[Path]:
    """rsync --out-format='%n' 輸出 → 本地絕對路徑清單。

    略過空行與目錄列（'/' 結尾）；只留副檔名在 exts 內者；去重保序。
    """
    out: list[Path] = []
    seen: set[str] = set()
    for raw in lines:
        name = raw.strip()
        if not name or name.endswith("/"):
            continue
        if Path(name).suffix.lower() not in exts:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(dst_root / name)
    return out


def write_ingested_hashes(path: Path, hashes: list[str]) -> None:
    """把本輪成功入庫的 file_hash 清單原子寫入標記檔（每行一個，每輪覆寫）。

    供殼層 gate 摘要步驟，並讓摘要只針對本輪新研報、不掃歷史 NULL 積壓。
    空清單寫成 0-byte 檔，殼層 `[ -s file ]` 會視為「無新研報」而跳過。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(hashes))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_stats(path: Path, stats: dict) -> None:
    """把本輪計數器原子寫入 `key=value` 標記檔，供殼層判斷是否為完整成功。

    **殼層不可以去 grep 那段給人看的 `=== sync summary ===`。** 那是人類文案，
    改一個字就會讓守門靜默失效，而症狀是「心跳照常更新」——與沒有守門完全一樣。

    額外寫出 `abnormal`（＝ABNORMAL_COUNTERS 之和），讓殼層不必知道分類規則；
    分類是這支腳本的知識，殼層只需要一個數字。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={int(v)}" for k, v in stats.items()]
    lines.append(f"abnormal={sum(int(stats.get(k, 0)) for k in ABNORMAL_COUNTERS)}")
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
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


def bad_request_path(hashes_out: Path) -> Path:
    """400 升級中止時觸發研報的清單：`<hashes_out>.bad_request`。"""
    return hashes_out.with_name(hashes_out.name + BAD_REQUEST_SUFFIX)


def write_bad_request_hashes(hashes_out: Path, hashes, paths: dict) -> None:
    """觸發 400 升級的研報寫成 `file_hash<TAB>路徑`（原子寫入）並印出；寫檔失敗只印清單。"""
    lines = [f"{h}\t{paths.get(h, '-')}" for h in hashes]
    dst = bad_request_path(hashes_out)
    try:
        write_ingested_hashes(dst, lines)
        print(f"觸發 400 升級的研報寫到 {dst}（重放 delta 前先把這些路徑拿掉）：", flush=True)
    except OSError as exc:
        print(f"觸發 400 升級的研報寫 {dst} 失敗：{exc!r}；清單如下：", flush=True)
    print("\n".join(lines), flush=True)


def hashes_out_path(args) -> Path:
    """本輪入庫 hashes 要寫到哪裡：預設 `data/.sync_last_hashes`（排程殼讀它驅動下游）。

    `--hashes-out` 給手動重放多份保留的 delta 用：固定檔名每重放一份就被覆寫一次，
    只有最後一份的 hashes 留得下來，前幾份入庫的研報就不會跑摘要、標題、摘錄——
    摘錄沒有全表補的機制，會靜默缺漏。指定時**只寫這一份**，不動預設檔，免得蓋掉
    排程殼當輪要用的內容。
    """
    return Path(args.hashes_out) if getattr(args, "hashes_out", None) else INGESTED_HASHES_FILE


def partial_hashes_path(hashes_out: Path) -> Path:
    """中途中止時「已 commit 的 hashes」寫到哪裡：`<hashes_out>.partial`。"""
    return hashes_out.with_name(hashes_out.name + ".partial")


@contextlib.contextmanager
def partial_hashes_on_abort(hashes_out: Path, hashes: list[str], *, enabled: bool = True):
    """區塊內任何例外（含 SystemExit／KeyboardInterrupt）中止時，把 `hashes` 寫到
    `<hashes_out>.partial` 後原樣重拋；正常結束不寫。

    **為什麼要有它**：逐篇入庫是各自 commit 的，但 hashes 清單原本只在最後寫出。整批
    中止（`CliNotFoundError`→rc=2、`report_exists` 之類的 DB 例外→rc=1）時，已 commit
    的那幾篇不會出現在任何 hashes 裡；重放同一份 delta 時它們又變成 `skip_exists`，
    於是摘要、標題、摘錄永遠漏掉它們（摘錄沒有全表補的機制）。殼在匯入 rc≠0／75 時
    把這份 partial 改名保留並印出補跑指令。

    `hashes` 是呼叫端持續 append 的同一個 list（只放已 commit 的篇），所以寫出的是
    中止當下的內容。寫檔失敗只印出來，不蓋掉原本的例外。
    """
    try:
        yield
    except BaseException:
        if enabled and hashes:
            dst = partial_hashes_path(hashes_out)
            try:
                write_ingested_hashes(dst, list(hashes))
                print(f"中止前已入庫 {len(hashes)} 篇，hashes 寫到 {dst}（下游要補跑）", flush=True)
            except OSError as exc:
                print(f"中止前已入庫 {len(hashes)} 篇，但寫 {dst} 失敗：{exc!r}；清單如下：", flush=True)
                print("\n".join(hashes), flush=True)
        raise


def _iter_targets(args) -> list[Path]:
    """依參數取得待處理檔清單：--all-local 掃整個本地夾；否則解析 delta 檔。"""
    if args.all_local:
        return sorted(p for p in SRC_LOCAL.rglob("*") if p.suffix.lower() in EXTS)
    if not args.delta:
        return []
    lines = Path(args.delta).read_text(encoding="utf-8").splitlines()
    return parse_rsync_delta(lines, SRC_LOCAL)


async def _run(args) -> None:
    import time

    from sqlalchemy import text as sql_text

    from app.config import get_settings
    from app.services import llm_failures
    from app.services.db import SessionFactory, relax_statement_timeout
    from app.services.object_storage import get_object_storage

    # 設定與物件儲存在處理任何一篇之前就取：設定錯、R2 缺憑證要整批 fail-closed，不是逐篇失敗。
    get_settings()
    storage = get_object_storage()
    targets = _iter_targets(args)
    if args.limit:
        targets = targets[: args.limit]
    print(f"待處理檔：{len(targets)}（dry_run={args.dry_run}）", flush=True)

    stats = {
        k: 0
        for k in (
            "ingested",
            "chunks",
            "skip_admin",
            "skip_scanned",
            "skip_exists",
            "skip_untagged",
            "skip_blocked",
            "skip_truncated",
            "skip_non_research",
            "fail",
            "cache_fail",
        )
    }
    ingested_hashes: list[str] = []
    t0 = time.time()
    # 本輪送去標註的 file_hash → 路徑（400 升級時寫保留檔用）
    tagged_paths: dict[str, Path] = {}
    # 跳過名單的寫入端：只有 DeepSeek 的內容型失敗才用得到，第一次需要時才開（fail-open）
    recorder_box: list = []

    async def _recorder():
        if not recorder_box:
            recorder_box.append(
                await llm_failures.open_recorder(llm_failures.TASK_TAG, TAG_MODEL, SessionFactory)
            )
        return recorder_box[0]

    # 逐篇各自 commit，hashes 卻在最後才寫：中途整批中止時，已入庫的篇要另寫 .partial，
    # 否則重放時它們變 skip_exists、永遠進不了任何 hashes（見 partial_hashes_on_abort）。
    with partial_hashes_on_abort(hashes_out_path(args), ingested_hashes, enabled=not args.dry_run):
        async with SessionFactory() as session:
            for path in targets:
                try:
                    # commit 成功就立刻記進 hashes（on_committed），之後的任何步驟（寫快取）都不能讓它掉出去。
                    out = await ingest_one(
                        session, path, storage=storage, batch_size=args.batch_size, dry_run=args.dry_run,
                        tags_dir=TAGS_DIR, tagged_paths=tagged_paths, on_committed=ingested_hashes.append,
                    )
                except BadRequestEscalation as exc:
                    # 審查 H2：觸發的研報先記跳過名單、寫保留檔，再中止整批（rc=2）
                    await record_escalation(exc, await _recorder())
                    write_bad_request_hashes(hashes_out_path(args), exc.file_hashes, tagged_paths)
                    raise
                if out.kind == "missing":
                    continue
                stats["ingested" if out.kind == "would_ingest" else out.kind] += 1
                # **格式三處一致：`路徑<TAB>階段<TAB>原因`。**
                # 曾有一處寫成 `file_hash<TAB>檔名<TAB>原因`——欄位數相同但語意不同，
                # 於是拿 sync_failures.log 補救時，第 0 欄拿到的是雜湊而不是路徑，
                # 這一類漏收**無法**用 --delta 精準補回（2026-08-20 復原時發現）。
                if out.stage == "tag":
                    # skip_untagged 先前完全不留痕跡：計數 +1 之後就 continue，
                    # 於是「標註壞了」與「這批本來就沒有研報」在 log 上無從分辨。
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\ttag\t{out.reason or '標註失敗'}\n")
                elif out.stage in ("tag_blocked", "tag_truncated"):
                    # 階段刻意叫 tag_blocked／tag_truncated：failures_to_delta 預設不撈（重打結果不會變）。
                    # 原因欄帶 file_hash，`make llm-blocked` 列出的 hash 可以 grep 回路徑。
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\t{out.stage}\t{out.reason}（file_hash={out.file_hash}）\n")
                elif out.stage is not None:  # extract／ingest
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\t{out.stage}\t{out.reason}\n")
                # DeepSeek 的內容型失敗（審查、截斷、空回應、400）記入跳過名單供 make llm-blocked
                # 列出；行內標註不讀它（被擋的檔不會自己再出現在 delta 裡）。
                if out.failure_kind:
                    recorder = await _recorder()
                    if recorder is not None:
                        await recorder.record(out.file_hash, out.failure_kind)
                if out.kind == "would_ingest":
                    print(f"  [DRY] would ingest: {path.name[:60]}", flush=True)
                elif out.kind == "ingested":
                    stats["chunks"] += out.chunks
                    if not out.cache_written:
                        stats["cache_fail"] += 1  # 不算異常（見 ABNORMAL_COUNTERS 註解），但要看得到
                    print(f"  [{out.market}] {path.name[:55]} ({out.chunks} chunks)", flush=True)

            if stats["ingested"] and not args.dry_run:
                # ANALYZE 可能久於引擎層的 statement_timeout，且是本輪匯入的最後一步——
                # 被砍掉時資料都已 commit，症狀只有 planner 統計靜默過期，排程沒人在看。
                await relax_statement_timeout(session)
                await session.execute(sql_text("ANALYZE research.report_chunk"))
                # research_report 也一起刷（毫秒級）。autoanalyze 是開著的，缺這句不會讓統計
                # 長期失真；會失真的是「剛大批 ingest 完就立刻查詢」那個短窗——這條排程每 3 小時
                # 匯入一次，正好落在那個窗裡。
                await session.execute(sql_text("ANALYZE research.research_report"))
                await session.commit()

    if not args.dry_run:
        write_ingested_hashes(hashes_out_path(args), ingested_hashes)
        write_stats(STATS_FILE, stats)

    print("\n=== sync summary ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    print(f"  elapsed: {time.time() - t0:.0f}s", flush=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", help="rsync 傳輸清單檔（本次新傳）")
    ap.add_argument(
        "--all-local",
        action="store_true",
        help="改掃整個本地 研報自動匯入/ 對 DB 補漏",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只印將匯入清單，不呼叫 claude、不寫 DB",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument(
        "--hashes-out",
        default=None,
        help="本輪入庫 hashes 改寫到這個檔（預設 data/.sync_last_hashes）；手動重放多份 delta 時每份各寫一份",
    )
    return ap


def main() -> None:
    import asyncio

    ap = build_parser()
    args = ap.parse_args()
    if not args.delta and not args.all_local:
        ap.error("需指定 --delta <file> 或 --all-local")
    # 取鎖之前：缺金鑰或模型名打錯是「跑了也白跑」，要在撞鎖（rc=75＝不跑）之前說出來。
    if not args.dry_run:  # --dry-run 不標註、不呼叫 LLM
        require_llm_key({TASK_TAG: TAG_MODEL})
    # 這支也 spawn claude（行內標註，見 _ingest_core._tag_via_cli），而且它跑在排程路徑上、是三小時
    # 一輪的第一個競爭者——手動批次正在跑時它照樣會被 timer 叫起來。
    with claude_cli_lock_or_exit("sync_new_reports"):
        try:
            asyncio.run(_run(args))
        except CliNotFoundError as exc:
            # 環境層級失敗：每一篇的標註都會踩到同一顆地雷，整批會被記成
            # skip_untagged 而「成功」結束（rc=0），排程殼只會印「本次無新研報入庫」。
            # 以非零碼收場，讓 unit 變紅、record_unit_failure 留下痕跡。
            print(f"中止：{exc}", flush=True)
            raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
