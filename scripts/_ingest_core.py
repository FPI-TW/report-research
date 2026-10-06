"""單篇入庫核心：抽字 → 標註 → 切塊 → 嵌入 → 原檔上傳 → 入庫（`ingest_one`）。

`scripts/sync_new_reports.py` 的逐篇迴圈與之後的上傳 worker 共用這一份，讓兩條入庫路徑的
失敗語意、R2 上傳順序（create-only、上傳前重算 SHA）、樣板剔除、`needs_review` 參數、行內標註
（`max_tokens`、`meta`）完全同源。

**分工**：這裡只「做事並回報發生了什麼」（`Outcome`）；計數、`data/sync_failures.log`、跳過名單
（`research.llm_task_failure`）、400 升級的保留檔、進度輸出都由呼叫端依 `Outcome` 自己記，
因為 sync 與 worker 各有自己的落點。

**不是入口**：不取 `scripts/_claude_lock.py` 的鎖、不呼叫 `load_llm_env()`。模組常數
`TAG_MODEL` 在 import 期解析，所以呼叫端必須照入口規約先 `load_llm_env()` 再 import 本模組
（`tests/test_llm_env_loading.py` 把 import 本模組的入口也算成 LLM 入口）。它不放在
`app/services/` 底下，因為要 import `scripts._claude_cli.run_claude`。

重型相依（抽字、DB、嵌入、物件儲存）延遲到 `ingest_one` 內 import：測試以
`mock.patch("app.services.<模組>.<名稱>")` 替換它們，純函式也可被輕量測試。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from app.services.extraction import cache as extraction_cache
from app.services.llm_models import TASK_TAG, resolve_model
from scripts._claude_cli import CliResult, error_kind, failure_kind, run_claude

ROOT = Path(__file__).resolve().parents[1]
TAGS_DIR = ROOT / "data" / "tags"

# 行內標註的模型：TAG_MODEL 旋鈕（與 tag_all_cli 共用），未設時查 LLM_PROVIDER 的預設表
# （app/services/llm_models.py；預設 deepseek 下是 deepseek-flash）。
TAG_MODEL = resolve_model(TASK_TAG)
# 走 DeepSeek 時的輸出上限（第二版計畫 §8；CLI 路徑不讀）；與 tag_all_cli 同值。
TAG_MAX_TOKENS = 1024

# `Outcome.kind` 的詞彙。除了最後兩個，都與 sync 的計數器鍵（`.sync_last_stats`）逐字相同，
# 分類規則（哪些算異常）見 sync_new_reports.ABNORMAL_COUNTERS 的註解。
#   - `missing`：檔案不存在（鏡像在 rsync 之後被移走）。sync 不計數。
#   - `would_ingest`：`dry_run=True` 時通過標註前所有閘的篇。sync 計進 `ingested`。
KINDS = (
    "ingested",
    "skip_admin",
    "skip_scanned",
    "skip_exists",
    "skip_untagged",
    "skip_blocked",
    "skip_truncated",
    "skip_non_research",
    "fail",
    "missing",
    "would_ingest",
)

# `Outcome.stage`（只有要留路徑紀錄的結果才有）：與 sync_failures.log 第二欄同一套詞彙。
# `tag_blocked`／`tag_truncated` 刻意另立階段：failures_to_delta 預設不撈（重打結果不會變）。
STAGES = ("extract", "tag", "tag_blocked", "tag_truncated", "ingest")

@dataclass(frozen=True)
class Outcome:
    """單篇入庫的結果。呼叫端依此計數、寫失敗紀錄、記跳過名單。

    - `kind`：見 `KINDS`。
    - `file_hash`：抽字成功才有（抽字拋例外時為 None）。
    - `report_id`：只有 `ingested` 才有（`upsert_report` 回傳的新 id；重新入庫會換 id）。
    - `market`、`chunks`：只有 `ingested` 才有。
    - `stage`、`reason`：要留路徑紀錄的結果才有（見 `STAGES`）。`reason` 是原始原因：抽字例外與
      入庫例外是 `repr(e)`，抽字自己接住的損毀檔是 `ExtractResult.error`，標註失敗是 `_tag_via_cli`
      回的錯誤字串（可能是 None）。
    - `failure_kind`：標註失敗時 `scripts._claude_cli.failure_kind` 的分類（跳過名單的 reason）；
      CLI 失敗、成功、非標註失敗都是 None。
    - `stopped_at`：本次寫進 `research.extraction_log` 的 `stopped_at`；沒寫為 None。
    - `cache_written`：只有 `ingested` 才有，抽取快取是否寫成（fail-open，False 不影響已入庫）。
    """

    kind: str
    path: Path
    file_hash: str | None = None
    report_id: str | None = None
    market: str | None = None
    chunks: int = 0
    stage: str | None = None
    reason: str | None = None
    failure_kind: str | None = None
    stopped_at: str | None = None
    cache_written: bool | None = None


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
    """標註後過濾：無 tag → skip_untagged（被內容審查擋下 → skip_blocked；被截斷 → skip_truncated）；
    非研究/無市場 → skip_non_research；否則 None。"""
    if tag is None:
        kind = error_kind(tag_error)
        if kind == "content_filter":
            return "skip_blocked"
        # 只認 finish_reason=length 的 truncated；期限型截斷（timeout_streamed）刻意落到 skip_untagged（可重放）
        if kind == "truncated":
            return "skip_truncated"
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
    # CliNotFoundError 刻意不接：環境層級失敗，讓它拋到入口 main 中止整批
    res = run_claude(
        prompt, model, timeout=timeout, max_tokens=TAG_MAX_TOKENS,
        meta={"task": TASK_TAG, "file_hash": file_hash, "report_id": None},
    )
    if not res.text:
        return None, res.error or "CLI 無回應"
    tag = parse_tags(res.text)
    return (tag, None) if tag is not None else (None, "回應無法解析為標籤")


def _persist_tag(file_hash: str, tag, tags_dir: Path = TAGS_DIR) -> None:
    """tag → <tags_dir>/<hash>.json（與 tag_all_cli 同格式，原子寫入）。"""
    tags_dir.mkdir(parents=True, exist_ok=True)
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
    out = tags_dir / f"{file_hash}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.rename(out)


def _write_cache(res, path: Path, meta, source: str | None, report_date) -> None:
    """把本輪抽取結果寫進 per-hash 快取（E1c），與 extract_all 同一種紀錄。

    report_date 用這裡決定的值（含 mtime 回退），不是 parse_filename 的原值——
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


async def ingest_one(
    session,
    path: Path,
    *,
    storage=None,
    batch_size: int = 32,
    dry_run: bool = False,
    tags_dir: Path = TAGS_DIR,
    tagged_paths: dict[str, Path] | None = None,
    on_committed: Callable[[str], None] | None = None,
) -> Outcome:
    """單篇：抽字 → 標註前閘 → 標註 → 標註後閘 → 切塊 → 嵌入 → 原檔上傳 → 入庫 → 抽取快取。

    參數：
    - `storage`：`get_object_storage()` 的結果；None 時每次呼叫自己取。迴圈呼叫端應每輪取一次傳進來。
    - `dry_run`：只走到標註前的閘，通過者回 `would_ingest`；不標註、不嵌入、不入庫。**照舊**會為
      抽字自己接住的損毀檔寫 `extraction_log`（`extract_error`）——與抽出核心之前的 sync 相同。
    - `tags_dir`：標註快取（`load_tag` 讀、標註成功寫）。
    - `tagged_paths`：有給就把「本次送去標註的 file_hash → 路徑」填進去，供呼叫端在 400 升級
      （`BadRequestEscalation`）時寫保留檔。
    - `on_committed`：入庫 commit 成功後、寫抽取快取**之前**以 file_hash 呼叫。sync 用它把 hash 記進
      本輪清單：之後任何步驟（含被中止）都不能讓已入庫的篇掉出 hashes（審查 L9）。

    例外：抽字與入庫階段的單篇例外收成 `fail`；其餘照原樣往上拋、由入口中止整批——
    `CliNotFoundError`（含 `LlmEnvironmentError`，環境層級失敗）、`BadRequestEscalation`
    （400 升級；觸發篇記跳過名單與寫保留檔是呼叫端的事）、`report_exists`／`extraction_log`
    寫入這類 DB 例外。
    """
    from app.config import get_settings
    from app.services.boilerplate import strip_boilerplate
    from app.services.chunk import chunk_text
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

    if not path.exists():
        return Outcome("missing", path)
    try:
        res = extract_text(path)
    except Exception as e:  # noqa: BLE001 — 長跑不因單檔中斷
        return Outcome("fail", path, stage="extract", reason=repr(e))

    def _log(stopped_at: str) -> ExtractionLogRow:
        q = dict(res.quality or {})
        return ExtractionLogRow(
            file_hash=res.file_hash,
            file_name=path.name,
            extractor=res.extractor,
            extraction_version=res.extraction_version,
            stopped_at=stopped_at,
            page_count=res.page_count,
            pages_failed=list(res.pages_failed) or None,
            char_count=res.char_count,
            quality_score=q.get("quality_score"),
            quality_flags=q,
        )

    if res.error:
        # extract_text 自己接住的損毀檔：現況只當 scanned 靜默跳過，這裡留一列。
        await upsert_extraction_log(session, _log("extract_error"))
        await session.commit()
        return Outcome(
            "fail", path, file_hash=res.file_hash, stage="extract", reason=res.error, stopped_at="extract_error"
        )

    meta = parse_filename(path.name)
    # live sync 沒有可信的「複製發生時間」參照；若 rsync 已保留 NAS 原始 mtime，
    # 這裡應直接信任 mtime，避免今天/近兩天的新報告再度被留成 NULL。
    report_date = fallback_report_date_from_mtime(meta.report_date, path, created_at=None)
    # 來源券商：本土發行機構內文指紋 → 檔名 token → 外資內文指紋（見 resolve_source）。
    # 內文指紋置於檔名前，可校正檔名把標的公司誤當券商（語料約 67% 檔名亦不帶券商）。
    source = resolve_source(path.name, res.text)
    exists = await report_exists(session, res.file_hash)
    reason = skip_before_tag(meta.is_admin, res.scanned, exists)
    if reason:
        # 每一道閘都寫 extraction_log（§4.2 目標 #1）；已入庫者不重寫。
        stopped_at = None
        if reason == "skip_admin" and not dry_run:
            await upsert_extraction_log(session, _log("skip_admin"))
            await session.commit()
            stopped_at = "skip_admin"
        elif reason == "skip_scanned" and not dry_run:
            await upsert_extraction_log(session, _log("scanned"))
            await session.commit()
            stopped_at = "scanned"
        return Outcome(reason, path, file_hash=res.file_hash, stopped_at=stopped_at)

    if dry_run:
        return Outcome("would_ingest", path, file_hash=res.file_hash)

    tag = load_tag(tags_dir, res.file_hash)
    tag_error = None
    if tag is None:
        if tagged_paths is not None:
            tagged_paths[res.file_hash] = path
        tag, tag_error = _tag_via_cli(path.name, res.text, file_hash=res.file_hash)
    if tag is not None:
        _persist_tag(res.file_hash, tag, tags_dir)
    reason = skip_after_tag(tag, tag_error)
    if reason:
        stopped_at = None
        if reason == "skip_non_research":
            await upsert_extraction_log(session, _log("not_research"))
            await session.commit()
            stopped_at = "not_research"
        stage = {"skip_untagged": "tag", "skip_blocked": "tag_blocked", "skip_truncated": "tag_truncated"}.get(reason)
        # DeepSeek 的內容型失敗（審查、截斷、空回應、400）的跳過名單分類；CLI 失敗為 None。
        fail_reason = failure_kind(CliResult(None, tag_error)) if tag is None else None
        return Outcome(
            reason, path, file_hash=res.file_hash, stage=stage, reason=tag_error if stage else None,
            failure_kind=fail_reason, stopped_at=stopped_at,
        )

    settings = get_settings()
    review_min = get_settings().extraction_review_min
    if storage is None:
        storage = get_object_storage()
    try:
        raw_text = (res.text or "").replace("\x00", "")
        # 樣板段落只從要切塊的文字拿掉，full_text 不動（app/services/boilerplate.py）。
        chunks = chunk_text(strip_boilerplate(clean_extracted(raw_text), source)[0])
        if not chunks:
            await upsert_extraction_log(session, _log("scanned"))
            await session.commit()
            return Outcome("skip_scanned", path, file_hash=res.file_hash, stopped_at="scanned")
        embeddings = embed_texts(chunks, batch_size=batch_size)
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
        report_id = await upsert_report(session, report, chunks, embeddings)
        await upsert_extraction_log(session, _log("ingested"))
        await session.commit()
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        return Outcome("fail", path, file_hash=res.file_hash, stage="ingest", reason=repr(e))

    # commit 成功就立刻通知呼叫端（sync 記進 hashes），之後的任何步驟（寫快取）都不能讓它掉出去。
    if on_committed is not None:
        on_committed(res.file_hash)
    cache_written = write_cache_fail_open(res, path, meta, source, report_date)
    return Outcome(
        "ingested", path, file_hash=res.file_hash, report_id=report_id, market=tag.market,
        chunks=len(chunks), stopped_at="ingested", cache_written=cache_written,
    )
