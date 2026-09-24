"""增量同步匯入：吃 rsync delta 檔清單 → 逐檔 extract→tag→ingest。

僅處理「本次新傳入」的檔（或 --all-local 全本地對 DB 補漏），以 file_hash
對 DB 去重；單檔失敗不中斷，記 data/sync_failures.log。
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
import asyncio
import contextlib
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services.extraction import cache as extraction_cache  # noqa: E402
from app.services.llm_models import TASK_TAG, resolve_model  # noqa: E402
from scripts._claude_cli import (  # noqa: E402
    BadRequestEscalation,
    CliNotFoundError,
    CliResult,
    error_kind,
    failure_kind,
    record_escalation,
    run_claude,
)
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

SRC_LOCAL = ROOT / "研報自動匯入"
TAGS_DIR = ROOT / "data" / "tags"
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
ABNORMAL_COUNTERS = ("fail", "skip_untagged", "skip_blocked")

# 行內標註的模型：TAG_MODEL 旋鈕（與 tag_all_cli 共用），未設時查 LLM_PROVIDER 的預設表
# （app/services/llm_models.py；claude_cli 下是 claude-haiku-4-5）。
TAG_MODEL = resolve_model(TASK_TAG)
# 走 DeepSeek 時的輸出上限（第二版計畫 §8；CLI 路徑不讀）；與 tag_all_cli 同值。
TAG_MAX_TOKENS = 1024

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


def skip_before_tag(is_admin: bool, scanned: bool, exists: bool) -> str | None:
    """標註前的便宜過濾：行政檔／掃描空檔／已入庫 → skip 原因；否則 None。"""
    if is_admin:
        return "skip_admin"
    if scanned:
        return "skip_scanned"
    if exists:
        return "skip_exists"
    return None


def skip_after_tag(tag, tag_error: str | None = None) -> str | None:
    """標註後過濾：無 tag → skip_untagged（被內容審查擋下 → skip_blocked）；非研究/無市場 →
    skip_non_research；否則 None。"""
    if tag is None:
        if error_kind(tag_error) == "content_filter":
            return "skip_blocked"
        return "skip_untagged"
    if not tag.is_research or not tag.market:
        return "skip_non_research"
    return None


def _tag_via_cli(
    file_name: str,
    text: str,
    excerpt: int = 10000,
    model: str = TAG_MODEL,
    timeout: int = 150,
    *,
    file_hash: str | None = None,
):
    """標註單篇（`run_claude` 依白名單分派 CLI 或 DeepSeek）→ (tag, error)。tag 為 None 時 error
    說得出為什麼。

    **標註失敗是這條管線最貴的靜默失效**：它讓該檔被記成 `skip_untagged` 而不入庫，
    而排程殼只印一行「本次無新研報入庫」——與「NAS 真的沒有新檔」在畫面上完全一樣。
    2026-08-12 那輪 rsync 帶進 33 檔、全被吞掉，四天後才被發現。
    """
    from app.services.tagging import TAG_INSTRUCTION, parse_tags

    body = (text or "")[:excerpt]
    prompt = (
        f"{TAG_INSTRUCTION}\n\n檔名：{file_name}\n"
        f"報告內文（前 {excerpt} 字摘錄）：\n{body}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    )
    # CliNotFoundError 刻意不接：環境層級失敗，讓它拋到 main 中止整批
    res = run_claude(
        prompt, model, timeout=timeout, max_tokens=TAG_MAX_TOKENS,
        meta={"task": TASK_TAG, "file_hash": file_hash, "report_id": None},
    )
    if not res.text:
        return None, res.error or "CLI 無回應"
    tag = parse_tags(res.text)
    return (tag, None) if tag is not None else (None, "回應無法解析為標籤")


def _persist_tag(file_hash: str, tag) -> None:
    """tag → data/tags/<hash>.json（與 tag_all_cli 同格式，原子寫入）。"""
    import json

    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    obj = {
        "market": tag.market,
        "is_research": tag.is_research,
        "confidence": tag.confidence,
        "instrument_types": tag.instrument_types or [],
        "relates_stock": bool(tag.relates_stock),
        "relates_futures": bool(tag.relates_futures),
        "stock_targets": tag.stock_targets or [],
        "futures_targets": tag.futures_targets or [],
    }
    out = TAGS_DIR / f"{file_hash}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.rename(out)


def _write_cache(res, path: Path, meta, source: str | None, report_date) -> None:
    """把本輪抽取結果寫進 per-hash 快取（E1c），與 extract_all 同一種紀錄。

    report_date 用 sync 這裡決定的值（含 mtime 回退），不是 parse_filename 的原值——
    快取要記的是「入庫時採用的日期」。"""
    rec = extraction_cache.record_from_result(res, path, meta, source)
    rec["report_date"] = report_date.isoformat() if report_date else None
    extraction_cache.write_record(rec)


def write_cache_fail_open(res, path: Path, meta, source: str | None, report_date) -> bool:
    """入庫 commit **之後**寫抽取快取；失敗只印 WARNING、不拋，回是否寫成。

    快取不是正確性必要的：研報已在庫（full_text 是正典），快取只供全語料重建
    （tag_all_cli／ingest_all／build_boilerplate）省去重抽，以及摘錄剔除表格列時查
    block 索引（extract_takeaways 缺快取就退回完整正典文字）。缺一筆的代價是少量品質
    與重抽時間。反過來，讓它的例外落進入庫的 `except` 會把已 commit 的篇計成 fail、
    不進 hashes，重放時又 `skip_exists`——下游摘要／標題／摘錄永遠漏掉它（審查 L9，
    與 partial_hashes_on_abort 同一型）。批次的 logger.info 無聲，所以用 print。
    """
    try:
        _write_cache(res, path, meta, source, report_date)
        return True
    except Exception as e:  # noqa: BLE001 — 快取 fail-open，已入庫的篇不能因它掉出 hashes
        print(f"  WARNING 抽取快取寫入失敗（已入庫、已記入 hashes）：{path.name[:55]} {e!r}", flush=True)
        return False


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


def fallback_report_date_from_mtime(
    report_date: date | None,
    path: Path,
    *,
    created_at: date | None,
) -> date | None:
    """report_date 缺值時以 mtime 補；live sync 無可靠 copy-time 參照時直接信任 mtime。"""
    if report_date is not None:
        return report_date
    try:
        mtime = date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None

    from app.services.filename import mtime_report_date

    return mtime_report_date(mtime, created_at)


async def _run(args) -> None:
    import time

    from sqlalchemy import text as sql_text

    from app.config import get_settings
    from app.services import llm_failures
    from app.services.boilerplate import strip_boilerplate
    from app.services.chunk import chunk_text
    from app.services.db import SessionFactory, relax_statement_timeout
    from app.services.embed import embed_texts
    from app.services.extract import extract_text, file_sha256
    from app.services.filename import parse_filename, resolve_source
    from app.services.object_storage import get_object_storage, original_object_key
    from app.services.store import (
        ExtractionLogRow,
        ReportRow,
        needs_review,
        report_exists,
        upsert_extraction_log,
        upsert_report,
    )
    from app.services.tagging import load_tag
    from app.services.textnorm import clean_extracted

    settings = get_settings()
    review_min = get_settings().extraction_review_min
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
                if not path.exists():
                    continue
                try:
                    res = extract_text(path)
                except Exception as e:  # noqa: BLE001 — 長跑不因單檔中斷
                    stats["fail"] += 1
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\textract\t{e!r}\n")
                    continue

                def _log(stopped_at: str, _res=res, _path=path) -> ExtractionLogRow:
                    q = dict(_res.quality or {})
                    return ExtractionLogRow(
                        file_hash=_res.file_hash,
                        file_name=_path.name,
                        extractor=_res.extractor,
                        extraction_version=_res.extraction_version,
                        stopped_at=stopped_at,
                        page_count=_res.page_count,
                        pages_failed=list(_res.pages_failed) or None,
                        char_count=_res.char_count,
                        quality_score=q.get("quality_score"),
                        quality_flags=q,
                    )

                if res.error:
                    # extract_text 自己接住的損毀檔：現況只當 scanned 靜默跳過，這裡留一列。
                    stats["fail"] += 1
                    await upsert_extraction_log(session, _log("extract_error"))
                    await session.commit()
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\textract\t{res.error}\n")
                    continue

                meta = parse_filename(path.name)
                # live sync 沒有可信的「複製發生時間」參照；若 rsync 已保留 NAS 原始 mtime，
                # 這裡應直接信任 mtime，避免今天/近兩天的新報告再度被留成 NULL。
                report_date = fallback_report_date_from_mtime(
                    meta.report_date, path, created_at=None
                )
                # 來源券商：本土發行機構內文指紋 → 檔名 token → 外資內文指紋（見 resolve_source）。
                # 內文指紋置於檔名前，可校正檔名把標的公司誤當券商（語料約 67% 檔名亦不帶券商）。
                source = resolve_source(path.name, res.text)
                exists = await report_exists(session, res.file_hash)
                reason = skip_before_tag(meta.is_admin, res.scanned, exists)
                if reason:
                    stats[reason] += 1
                    # 每一道閘都寫 extraction_log（§4.2 目標 #1）；已入庫者不重寫。
                    if reason == "skip_admin" and not args.dry_run:
                        await upsert_extraction_log(session, _log("skip_admin"))
                        await session.commit()
                    elif reason == "skip_scanned" and not args.dry_run:
                        await upsert_extraction_log(session, _log("scanned"))
                        await session.commit()
                    continue

                if args.dry_run:
                    stats["ingested"] += 1
                    print(f"  [DRY] would ingest: {path.name[:60]}", flush=True)
                    continue

                tag = load_tag(TAGS_DIR, res.file_hash)
                tag_error = None
                if tag is None:
                    tagged_paths[res.file_hash] = path
                    try:
                        tag, tag_error = _tag_via_cli(path.name, res.text, file_hash=res.file_hash)
                    except BadRequestEscalation as exc:
                        # 審查 H2：觸發的研報先記跳過名單、寫保留檔，再中止整批（rc=2）
                        await record_escalation(exc, await _recorder())
                        write_bad_request_hashes(hashes_out_path(args), exc.file_hashes, tagged_paths)
                        raise
                if tag is not None:
                    _persist_tag(res.file_hash, tag)
                reason = skip_after_tag(tag, tag_error)
                if reason:
                    stats[reason] += 1
                    if reason == "skip_non_research":
                        await upsert_extraction_log(session, _log("not_research"))
                        await session.commit()
                    # skip_untagged 先前完全不留痕跡：計數 +1 之後就 continue，
                    # 於是「標註壞了」與「這批本來就沒有研報」在 log 上無從分辨。
                    if reason == "skip_untagged":
                        with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                            fl.write(f"{path}\ttag\t{tag_error or '標註失敗'}\n")
                    elif reason == "skip_blocked":
                        # 階段刻意叫 tag_blocked：failures_to_delta 預設不撈（重打結果不會變）。
                        # 原因欄帶 file_hash，`make llm-blocked` 列出的 hash 可以 grep 回路徑。
                        with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                            fl.write(f"{path}\ttag_blocked\t{tag_error}（file_hash={res.file_hash}）\n")
                    # DeepSeek 的內容型失敗（審查、截斷、空回應、400）記入跳過名單供 make llm-blocked
                    # 列出；行內標註不讀它（被擋的檔不會自己再出現在 delta 裡）。
                    fail_reason = failure_kind(CliResult(None, tag_error)) if tag is None else None
                    if fail_reason:
                        recorder = await _recorder()
                        if recorder is not None:
                            await recorder.record(res.file_hash, fail_reason)
                    continue

                try:
                    raw_text = (res.text or "").replace("\x00", "")
                    # 樣板段落只從要切塊的文字拿掉，full_text 不動（app/services/boilerplate.py）。
                    chunks = chunk_text(strip_boilerplate(clean_extracted(raw_text), source)[0])
                    if not chunks:
                        stats["skip_scanned"] += 1
                        await upsert_extraction_log(session, _log("scanned"))
                        await session.commit()
                        continue
                    embeddings = embed_texts(chunks, batch_size=args.batch_size)
                    # Upload first: a failed DB commit may leave a reconcilable orphan, whereas
                    # committing a key before bytes exist would expose a broken download.
                    source_object_key = None
                    if storage.enabled:
                        # The extract result was hashed earlier; re-hash at the last possible
                        # point so a concurrently replaced mirror file cannot overwrite R2 under
                        # the old DB key.
                        if file_sha256(path) != res.file_hash:
                            raise ValueError("source SHA-256 changed before R2 upload")
                        source_object_key = original_object_key(res.file_hash, path.name)
                        await asyncio.to_thread(
                            storage.upload_file, path, source_object_key, expected_sha256=res.file_hash
                        )
                    report = ReportRow(
                        file_hash=res.file_hash,
                        file_name=path.name,
                        file_path=str(path),
                        market=tag.market,
                        is_research=tag.is_research,
                        confidence=tag.confidence,
                        source_object_key=source_object_key,
                        stock_code=meta.stock_code,
                        company_name=meta.company_name,
                        source=source,
                        report_date=report_date,
                        report_type=meta.report_type,
                        language=res.language,
                        instrument_types=tag.instrument_types,
                        relates_stock=tag.relates_stock,
                        relates_futures=tag.relates_futures,
                        stock_targets=tag.stock_targets,
                        futures_targets=tag.futures_targets,
                        full_text=raw_text,
                        extractor=res.extractor,
                        extraction_version=res.extraction_version,
                        quality_score=(res.quality or {}).get("quality_score"),
                        quality_flags=dict(res.quality) if res.quality else None,
                        page_count=res.page_count,
                        pages_failed=list(res.pages_failed) or None,
                        needs_review=needs_review(
                            (res.quality or {}).get("quality_score"), res.pages_failed, review_min, res.quality or None,
                            min_coverage=settings.extraction_review_min_coverage,
                            max_garbled=settings.extraction_review_max_garbled,
                        ),
                    )
                    await upsert_report(session, report, chunks, embeddings)
                    await upsert_extraction_log(session, _log("ingested"))
                    await session.commit()
                except Exception as e:  # noqa: BLE001
                    stats["fail"] += 1
                    await session.rollback()
                    # **格式必須與另外兩處一致：`路徑<TAB>階段<TAB>原因`。**
                    # 初版這裡寫的是 `file_hash<TAB>檔名<TAB>原因`——欄位數相同但語意不同，
                    # 於是拿 sync_failures.log 補救時，第 0 欄拿到的是雜湊而不是路徑，
                    # 這一類漏收**無法**用 --delta 精準補回（2026-08-20 復原時發現）。
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{path}\tingest\t{e!r}\n")
                    continue

                # commit 成功就立刻記進 hashes，之後的任何步驟（寫快取）都不能讓它掉出去。
                ingested_hashes.append(res.file_hash)
                stats["ingested"] += 1
                stats["chunks"] += len(chunks)
                if not write_cache_fail_open(res, path, meta, source, report_date):
                    stats["cache_fail"] += 1  # 不算異常（見 ABNORMAL_COUNTERS 註解），但要看得到
                print(f"  [{tag.market}] {path.name[:55]} ({len(chunks)} chunks)", flush=True)

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
    # 這支也 spawn claude（行內標註，見 _tag_via_cli），而且它跑在排程路徑上、是三小時
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
