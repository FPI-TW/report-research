# NAS 定時偵測新研報並增量匯入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每隔幾小時自動把 NAS 共享 `\\192.168.1.100\投資研究處\02.研究資源\研報自動匯入` 上的新研報增量匯入可檢索資料庫，對線上服務干擾最小。

**Architecture:** systemd timer（每 3 小時）→ oneshot service → orchestration shell：drvfs 唯讀掛載 NAS（沿用 Windows 快取憑證、免明文密碼）→ rsync 同步新檔到本地並擷取 delta 清單 → Python 增量處理器只對 delta 檔逐檔 `extract→claude 標註→chunk→BGE-M3 嵌入→upsert`，以 `file_hash` 對 DB 去重，單檔失敗不中斷。重用既有 `app/services/*`，不更動既有批次管線與線上 web 服務。

**Tech Stack:** Python 3.13 / asyncio / SQLAlchemy async + pgvector；`uv` 執行；`rsync` + WSL `drvfs`；systemd timer/service；`claude` CLI（Haiku）標註；BGE-M3 嵌入；pytest。

## Global Constraints

- Python 3.13；DB 存取一律 async（`AsyncSession`、`await`、明確 `commit()`），原始 SQL 用 `text()`。
- 去重鍵＝檔案內容 SHA-256（`app/services/extract.py:file_sha256`），DB 以 `file_hash` upsert（`app/services/store.py`）。
- 標註模型固定 `claude-haiku-4-5`；呼叫 `claude` CLI 前一律 `prompt.replace("\x00","")`、`cwd="/tmp"`、timeout 150s（沿用 `scripts/tag_all_cli.py` 慣例）。
- `full_text` 走原始文字並 `replace("\x00","")`（避免含 NUL 的 PDF upsert 時 UTF8 編碼錯誤永久失敗）。
- DB 預設 DSN `postgresql+asyncpg://postgres:postgres@localhost:5436/research`（`REPORT_MARK_DB_URL` 可覆寫）；不要硬編其他連線字串。
- drvfs 掛載選項固定 `ro,uid=1000,gid=1000`；掛載點 `/mnt/nas-research`；本地同步目的地 `研報自動匯入/`（專案根）。
- 不更動既有檔（`extract_all.py`/`tag_all_cli.py`/`ingest_all.py`/`run_ingest.py`/`web/`/線上服務）；只新增檔 + 在 `Makefile` 末尾新增一個目標。
- commit 用 Conventional Commits（中文標題）；`uv run black`/`ruff` 執行檔在本機可能缺，缺則略過格式化步驟、改以人工對齊風格；測試一律 `uv run pytest`。
- argparse `help` 字串內**不可含 `%`**（argparse 會對 help 做 %-格式化，`%n` 會拋例外）。

---

### Task 1: 純函式核心 + 單元測試（delta 解析 / 過濾判定）

把整個增量流程裡「不需 DB / 不需模型 / 不需 claude」的決策邏輯抽成三個純函式，先 TDD。這是唯一能完全離線單元測試的部分，獨立可審。

**Files:**
- Create: `scripts/sync_new_reports.py`（本任務只放檔頭 docstring、常數、三個純函式；重型 import 留待 Task 2 放進 `main()`）
- Test: `tests/test_sync_new_reports.py`

**Interfaces:**
- Produces:
  - `parse_rsync_delta(lines: Iterable[str], dst_root: Path, exts: set[str] = EXTS) -> list[Path]`
  - `skip_before_tag(is_admin: bool, scanned: bool, exists: bool) -> str | None`
  - `skip_after_tag(tag) -> str | None`（`tag` 為 `None` 或具 `.market`/`.is_research` 屬性的物件，duck-typed，不 import）
  - 模組常數 `EXTS = {".pdf", ".docx", ".doc"}`、`SRC_LOCAL`、`TAGS_DIR`、`ALL_JSONL`、`FAIL_LOG`

- [ ] **Step 1: 先寫失敗測試**

建立 `tests/test_sync_new_reports.py`：

```python
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


def test_parse_rsync_delta_keeps_only_files_with_known_ext():
    lines = [
        "新增/",                      # 目錄列 → 略過
        "新增/0701 報告.pdf",
        "note.txt",                   # 非目標副檔名 → 略過
        "a/b/Taiwan daily.docx",
        "",                            # 空行
    ]
    out = snr.parse_rsync_delta(lines, Path("/local"))
    assert out == [
        Path("/local/新增/0701 報告.pdf"),
        Path("/local/a/b/Taiwan daily.docx"),
    ]


def test_parse_rsync_delta_dedupes_preserving_order():
    out = snr.parse_rsync_delta(["x.pdf", "x.pdf", "y.PDF"], Path("/d"))
    assert out == [Path("/d/x.pdf"), Path("/d/y.PDF")]


def test_skip_before_tag_priority_order():
    assert snr.skip_before_tag(True, False, False) == "skip_admin"
    assert snr.skip_before_tag(False, True, False) == "skip_scanned"
    assert snr.skip_before_tag(False, False, True) == "skip_exists"
    assert snr.skip_before_tag(False, False, False) is None


@dataclass
class _Tag:
    market: str | None
    is_research: bool


def test_skip_after_tag():
    assert snr.skip_after_tag(None) == "skip_untagged"
    assert snr.skip_after_tag(_Tag(None, True)) == "skip_non_research"
    assert snr.skip_after_tag(_Tag("TW", False)) == "skip_non_research"
    assert snr.skip_after_tag(_Tag("TW", True)) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_sync_new_reports.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'sync_new_reports'`（檔還沒建）。

- [ ] **Step 3: 寫最小實作**

建立 `scripts/sync_new_reports.py`，**只含**以下內容（重型邏輯下一個任務再加）：

```python
"""增量同步匯入：吃 rsync delta 檔清單 → 逐檔 extract→tag→ingest。

僅處理「本次新傳入」的檔（或 --all-local 全本地對 DB 補漏），以 file_hash
對 DB 去重；單檔失敗不中斷，記 data/sync_failures.log。
重型相依（embed/store/db…）延遲到 main() 內 import，讓純函式可被輕量測試。

用法：
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt
  uv run python scripts/sync_new_reports.py --all-local
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC_LOCAL = ROOT / "研報自動匯入"
TAGS_DIR = ROOT / "data" / "tags"
ALL_JSONL = ROOT / "data" / "extracted" / "all.jsonl"
FAIL_LOG = ROOT / "data" / "sync_failures.log"
EXTS = {".pdf", ".docx", ".doc"}


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
        if name in seen:
            continue
        seen.add(name)
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


def skip_after_tag(tag) -> str | None:
    """標註後過濾：無 tag → skip_untagged；非研究/無市場 → skip_non_research；否則 None。"""
    if tag is None:
        return "skip_untagged"
    if not tag.is_research or not tag.market:
        return "skip_non_research"
    return None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_sync_new_reports.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 5: 提交**

```bash
git add scripts/sync_new_reports.py tests/test_sync_new_reports.py
git commit -m "$(cat <<'EOF'
feat(sync): 增量同步純函式核心 + 單元測試

新增 scripts/sync_new_reports.py 的 delta 解析與兩段過濾判定純函式
（parse_rsync_delta / skip_before_tag / skip_after_tag），重型相依延後
到 main() 內 import 以利離線單元測試。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 增量處理器主體（extract→tag→ingest 串接）

把純函式接上既有 services，完成單檔處理流程、`--delta`/`--all-local`/`--dry-run`/`--limit` 與統計。重型 import 全部放進 `main()`/`_run()`，維持 Task 1 測試輕量。

**Files:**
- Modify: `scripts/sync_new_reports.py`（在 Task 1 內容**之後**追加；不改既有純函式）

**Interfaces:**
- Consumes（Task 1）：`parse_rsync_delta`、`skip_before_tag`、`skip_after_tag`、`SRC_LOCAL`、`TAGS_DIR`、`ALL_JSONL`、`FAIL_LOG`、`EXTS`
- Consumes（既有 services，皆已存在）：
  - `app.services.extract.extract_text(path: Path) -> ExtractResult`（欄位 `file_hash,text,char_count,scanned,language`）
  - `app.services.filename.parse_filename(name: str)`（屬性 `is_admin,stock_code,company_name,source,report_date,report_type`）
  - `app.services.tagging`：`TAG_INSTRUCTION`、`parse_tags(raw)->MarketTag|None`、`load_tag(TAGS_DIR, hash)->MarketTag|None`
  - `app.services.chunk.chunk_text(str)->list[str]`、`app.services.embed.embed_texts(chunks, batch_size)->list[list[float]]`
  - `app.services.textnorm.clean_extracted(str)->str`
  - `app.services.store`：`ReportRow`、`report_exists(session, hash)->bool`、`upsert_report(session, report, chunks, embeddings)->str`
  - `app.services.db.SessionFactory`
- Produces：`main()`（`__main__` 進入點）、`_run(args)`、輔助 `_tag_via_cli`/`_persist_tag`/`_append_all_jsonl`/`_iter_targets`

- [ ] **Step 1: 追加輔助函式（claude 標註 / 落檔 / 取目標清單）**

在 `scripts/sync_new_reports.py` 末尾追加：

```python
def _tag_via_cli(
    file_name: str,
    text: str,
    excerpt: int = 10000,
    model: str = "claude-haiku-4-5",
    timeout: int = 150,
):
    """用 claude CLI(Haiku)標註單篇；沿用 tag_all_cli 慣例（剝 NUL、cwd=/tmp）。"""
    import subprocess

    from app.services.tagging import TAG_INSTRUCTION, parse_tags

    body = (text or "")[:excerpt]
    prompt = (
        f"{TAG_INSTRUCTION}\n\n檔名：{file_name}\n"
        f"報告內文（前 {excerpt} 字摘錄）：\n{body}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    ).replace("\x00", "")
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
        )
    except Exception:
        return None
    return parse_tags(r.stdout) if r.returncode == 0 else None


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


def _append_all_jsonl(rec: dict) -> None:
    """把成功匯入的紀錄 append 進 all.jsonl，維持與批次工具一致。"""
    import json

    ALL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(ALL_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _iter_targets(args) -> list[Path]:
    """依參數取得待處理檔清單：--all-local 掃整個本地夾；否則解析 delta 檔。"""
    if args.all_local:
        return sorted(p for p in SRC_LOCAL.rglob("*") if p.suffix.lower() in EXTS)
    if not args.delta:
        return []
    lines = Path(args.delta).read_text(encoding="utf-8").splitlines()
    return parse_rsync_delta(lines, SRC_LOCAL)
```

- [ ] **Step 2: 追加主流程 `_run()` 與 `main()`**

繼續在末尾追加：

```python
async def _run(args) -> None:
    import time
    from datetime import date

    from sqlalchemy import text as sql_text

    from app.services.chunk import chunk_text
    from app.services.db import SessionFactory
    from app.services.embed import embed_texts
    from app.services.extract import extract_text
    from app.services.filename import parse_filename
    from app.services.store import ReportRow, report_exists, upsert_report
    from app.services.tagging import load_tag
    from app.services.textnorm import clean_extracted

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
            "skip_non_research",
            "fail",
        )
    }
    t0 = time.time()

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

            meta = parse_filename(path.name)
            exists = await report_exists(session, res.file_hash)
            reason = skip_before_tag(meta.is_admin, res.scanned, exists)
            if reason:
                stats[reason] += 1
                continue

            if args.dry_run:
                stats["ingested"] += 1
                print(f"  [DRY] would ingest: {path.name[:60]}", flush=True)
                continue

            tag = load_tag(TAGS_DIR, res.file_hash) or _tag_via_cli(path.name, res.text)
            if tag is not None:
                _persist_tag(res.file_hash, tag)
            reason = skip_after_tag(tag)
            if reason:
                stats[reason] += 1
                continue

            try:
                raw_text = (res.text or "").replace("\x00", "")
                chunks = chunk_text(clean_extracted(raw_text))
                if not chunks:
                    stats["skip_scanned"] += 1
                    continue
                embeddings = embed_texts(chunks, batch_size=args.batch_size)
                report = ReportRow(
                    file_hash=res.file_hash,
                    file_name=path.name,
                    file_path=str(path),
                    market=tag.market,
                    is_research=tag.is_research,
                    confidence=tag.confidence,
                    stock_code=meta.stock_code,
                    company_name=meta.company_name,
                    source=meta.source,
                    report_date=meta.report_date,
                    report_type=meta.report_type,
                    language=res.language,
                    instrument_types=tag.instrument_types,
                    relates_stock=tag.relates_stock,
                    relates_futures=tag.relates_futures,
                    stock_targets=tag.stock_targets,
                    futures_targets=tag.futures_targets,
                    full_text=raw_text,
                )
                await upsert_report(session, report, chunks, embeddings)
                _append_all_jsonl(
                    {
                        "file_hash": res.file_hash,
                        "file_name": path.name,
                        "file_path": str(path),
                        "text": res.text,
                        "char_count": res.char_count,
                        "scanned": res.scanned,
                        "language": res.language,
                        "is_admin": meta.is_admin,
                        "stock_code": meta.stock_code,
                        "company_name": meta.company_name,
                        "source": meta.source,
                        "report_date": (
                            meta.report_date.isoformat() if meta.report_date else None
                        ),
                        "report_type": meta.report_type,
                    }
                )
            except Exception as e:  # noqa: BLE001
                stats["fail"] += 1
                await session.rollback()
                with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                    fl.write(f"{res.file_hash}\t{path.name}\t{e!r}\n")
                continue

            stats["ingested"] += 1
            stats["chunks"] += len(chunks)
            print(f"  [{tag.market}] {path.name[:55]} ({len(chunks)} chunks)", flush=True)

        if stats["ingested"] and not args.dry_run:
            await session.execute(sql_text("ANALYZE research.report_chunk"))
            await session.commit()

    print("\n=== sync summary ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    print(f"  elapsed: {time.time() - t0:.0f}s", flush=True)


def main() -> None:
    import asyncio

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
    args = ap.parse_args()
    if not args.delta and not args.all_local:
        ap.error("需指定 --delta <file> 或 --all-local")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 確認既有純函式測試仍通過（未破壞 Task 1）**

Run: `uv run pytest tests/test_sync_new_reports.py -v`
Expected: PASS（5 passed）——追加重型程式碼後，模組頂層 import 仍只 stdlib，測試照常輕量通過。

- [ ] **Step 4: 乾跑煙霧測試（需本機 DB 在跑）**

先確認 DB 容器在跑（`make db`），放一個測試檔到本地夾，建一個假 delta 檔指向它，乾跑驗證選檔：

```bash
ls 研報自動匯入/*.pdf | head -1 | sed "s#^研報自動匯入/##" > data/_smoke_delta.txt
uv run python scripts/sync_new_reports.py --delta data/_smoke_delta.txt --dry-run
rm -f data/_smoke_delta.txt
```

Expected: 印出 `待處理檔：1`，且該檔若已入庫顯示 `skip_exists`、未入庫顯示 `[DRY] would ingest: ...`；無例外、無 DB 寫入。

- [ ] **Step 5: 提交**

```bash
git add scripts/sync_new_reports.py
git commit -m "$(cat <<'EOF'
feat(sync): 增量處理器主體（extract→claude 標註→嵌入→upsert）

接上既有 services 完成單檔流程，支援 --delta/--all-local/--dry-run/--limit；
重型相依延後到 _run() 內 import；沿用 NUL 剝除、file_hash 去重、失敗不中斷、
同步落 tags/<hash>.json 與 append all.jsonl 與批次工具一致。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 編排殼 + Makefile 目標

把「掛載檢查 → rsync 擷取 delta → 增量匯入」串成可被 systemd/手動觸發的殼，含 PID lock 與 nice/ionice。

**Files:**
- Create: `scripts/sync_new_reports.sh`
- Modify: `Makefile`（`.PHONY` 加 `sync-once`；末尾新增 `sync-once` 目標）

**Interfaces:**
- Consumes：`scripts/sync_new_reports.py`（`--delta`）；掛載點 `/mnt/nas-research`；root 掛載包裝 `/usr/local/sbin/mount-nas-research`（Task 4 提供，無 sudoers 時殼會在掛載失敗時安全結束）
- Produces：`make sync-once`、log `data/sync_run_<date>.log`、lock `data/.sync_new_reports.lock`

- [ ] **Step 1: 建立編排殼**

建立 `scripts/sync_new_reports.sh`：

```bash
#!/usr/bin/env bash
# 排程同步殼：掛載檢查 → rsync NAS→本地（擷取 delta）→ 增量匯入。
# 設計給 systemd oneshot；nice/ionice 降優先序，PID lock 防重疊。
set -uo pipefail
cd /mnt/c/Users/User/Desktop/Project/report-mark

MOUNT=/mnt/nas-research
SRC="$MOUNT/02.研究資源/研報自動匯入/"
DST="研報自動匯入/"
DATE=$(date +%Y%m%d)
LOG="data/sync_run_${DATE}.log"
DELTA="data/sync_delta_$(date +%Y%m%d_%H%M%S).txt"
LOCK="data/.sync_new_reports.lock"
UV=/home/kashionz/.local/bin/uv

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 防重入：上一輪仍在跑就跳過
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  log "已有 sync 在跑（lock=$(cat "$LOCK")），本次跳過"; exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

log "=== sync start (pid=$$) ==="

# 1) 確保 NAS 已掛載（未掛則用 root 包裝以快取憑證 drvfs 掛載；需 NOPASSWD sudoers）
if ! mountpoint -q "$MOUNT"; then
  log "嘗試掛載 $MOUNT（drvfs，唯讀，沿用 Windows 快取憑證）"
  sudo -n /usr/local/sbin/mount-nas-research >>"$LOG" 2>&1 || true
fi
if ! mountpoint -q "$MOUNT"; then
  log "掛載失敗或不可用 → 結束（不跑 rsync、不動 DB）"; exit 1
fi

# 2) rsync 只傳新檔，擷取 delta
#    --size-only：本地已有同 NAS 舊副本，避免因 mtime 漂移整批重傳 15G；
#    研報每檔內容唯一，同名同位元組視為相同的風險可忽略。
log "rsync 同步中…（src=$SRC）"
rsync -rt --size-only --no-motd --out-format='%n' "$SRC" "$DST" >"$DELTA" 2>>"$LOG"
RC=$?
NEW=$(grep -cvE '/$' "$DELTA" 2>/dev/null || echo 0)
log "rsync rc=$RC，本次新傳檔列≈${NEW}"
if [ "$RC" -ne 0 ]; then log "rsync 失敗 → 結束"; exit 1; fi

# 3) 增量匯入（nice/ionice 降優先序，勿搶線上服務）
log "增量匯入 delta…"
nice -n 19 ionice -c3 "$UV" run python scripts/sync_new_reports.py --delta "$DELTA" >>"$LOG" 2>&1
log "匯入結束 rc=$?"

rm -f "$DELTA"
log "=== sync done ==="
```

- [ ] **Step 2: 設可執行 + 在 Makefile 註冊目標**

```bash
chmod +x scripts/sync_new_reports.sh
```

在 `Makefile` 的 `.PHONY` 行尾加入 `sync-once`，並在「維運」區塊末尾（`clean-data` 之後）新增：

```makefile
sync-once:  ## 手動跑一次 NAS→本地同步 + 增量匯入（drvfs + rsync）
	bash scripts/sync_new_reports.sh
```

- [ ] **Step 3: 驗證殼的早退路徑（掛載未就緒時安全結束）**

在 NAS 尚未設定掛載/ sudoers 的情況下直接跑，應在掛載檢查處安全結束、不動 DB：

Run: `bash scripts/sync_new_reports.sh; echo "exit=$?"`
Expected: log 出現「掛載失敗或不可用 → 結束」，`exit=1`，**未**執行 rsync、**未**寫入 DB；`data/.sync_new_reports.lock` 已由 trap 清除（`ls data/.sync_new_reports.lock` → 不存在）。

- [ ] **Step 4: 驗證 lock 防重疊**

```bash
( echo 999999 > data/.sync_new_reports.lock )   # 假造一個不存在的 PID
# 用一個「存在中的」PID 測跳過：用目前 shell pid
echo $$ > data/.sync_new_reports.lock
bash scripts/sync_new_reports.sh; echo "exit=$?"
rm -f data/.sync_new_reports.lock
```

Expected: 因 lock 持有者（本 shell PID）存活，log 出現「已有 sync 在跑…本次跳過」，`exit=0`。

- [ ] **Step 5: 提交**

```bash
git add scripts/sync_new_reports.sh Makefile
git commit -m "$(cat <<'EOF'
feat(sync): 編排殼 + make sync-once（掛載檢查/rsync delta/增量匯入）

scripts/sync_new_reports.sh 串接 drvfs 掛載檢查、rsync --size-only 擷取
delta、nice/ionice 增量匯入；PID lock 防重疊、掛載不可用時安全早退不動 DB。
Makefile 新增 sync-once 目標便於手動觸發/驗證。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: systemd 排程單元 + root 掛載包裝 + 部署文件

提供 timer/service、免 sudoers 引號地獄的 root 掛載包裝，以及一次性部署步驟（含實測 drvfs 掛載）。

**Files:**
- Create: `deploy/systemd/report-mark-sync.service`
- Create: `deploy/systemd/report-mark-sync.timer`
- Create: `deploy/systemd/mount-nas-research`（root 掛載包裝；部署時安裝到 `/usr/local/sbin/`）
- Create: `deploy/systemd/report-mark-sync.sudoers`（部署時安裝到 `/etc/sudoers.d/report-mark-sync`）
- Create: `docs/nas_scheduled_sync_deployment.md`

**Interfaces:**
- Consumes：`scripts/sync_new_reports.sh`（service 的 ExecStart）
- Produces：可 `systemctl enable --now` 的 timer；`make sync-once` 與 timer 共用同一條路徑

- [ ] **Step 1: 建立 root 掛載包裝**

建立 `deploy/systemd/mount-nas-research`：

```bash
#!/usr/bin/env bash
# 以快取憑證 drvfs 唯讀掛載 NAS 共享。冪等：已掛則直接成功。
# 部署到 /usr/local/sbin/mount-nas-research（root:root 0755），搭配 sudoers NOPASSWD。
set -eu
mountpoint -q /mnt/nas-research && exit 0
exec mount -t drvfs '\\192.168.1.100\投資研究處' /mnt/nas-research -o ro,uid=1000,gid=1000
```

- [ ] **Step 2: 建立 sudoers 片段**

建立 `deploy/systemd/report-mark-sync.sudoers`：

```
# 安裝到 /etc/sudoers.d/report-mark-sync（chmod 0440），讓排程能無人值守掛載 NAS。
kashionz ALL=(root) NOPASSWD: /usr/local/sbin/mount-nas-research
```

- [ ] **Step 3: 建立 systemd service（oneshot）**

建立 `deploy/systemd/report-mark-sync.service`：

```ini
[Unit]
Description=廷豐智能研報 NAS 增量同步匯入（drvfs + rsync + 增量 ingest）
After=network-online.target report-mark-web.service
Wants=network-online.target

[Service]
Type=oneshot
User=kashionz
Group=kashionz
Environment=HOME=/home/kashionz
Environment=PATH=/home/kashionz/.nvm/versions/node/v24.15.0/bin:/home/kashionz/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WorkingDirectory=/mnt/c/Users/User/Desktop/Project/report-mark
ExecStart=/usr/bin/bash scripts/sync_new_reports.sh
TimeoutStartSec=7200
```

- [ ] **Step 4: 建立 systemd timer（每 3 小時）**

建立 `deploy/systemd/report-mark-sync.timer`：

```ini
[Unit]
Description=每 3 小時觸發 NAS 增量同步匯入

[Timer]
OnCalendar=*-*-* 00/3:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: 建立部署文件**

建立 `docs/nas_scheduled_sync_deployment.md`：

```markdown
# NAS 定時增量同步匯入 — 部署指南

排程：systemd timer 每 3 小時 → oneshot service → `scripts/sync_new_reports.sh`。
同步：drvfs 唯讀掛載 NAS（沿用 Windows Credential Manager 既有「Jacky Yeh」快取
憑證，**免明文密碼**）→ rsync 擷取 delta → 只對新檔 extract→tag→ingest。

## 前置：掛載點 + root 包裝 + sudoers（一次性）

\`\`\`bash
sudo mkdir -p /mnt/nas-research
sudo install -m 0755 deploy/systemd/mount-nas-research /usr/local/sbin/mount-nas-research
sudo install -m 0440 deploy/systemd/report-mark-sync.sudoers /etc/sudoers.d/report-mark-sync
sudo visudo -c        # 驗證 sudoers 語法
\`\`\`

## 實測 drvfs 掛載（關鍵：確認免密碼讀得到）

\`\`\`bash
sudo /usr/local/sbin/mount-nas-research
ls "/mnt/nas-research/02.研究資源/研報自動匯入" | head
\`\`\`

預期：列得出 PDF 檔名。若失敗，多半是 Windows 端「Jacky Yeh」快取憑證失效——
在 Windows 檔案總管手動連一次 \\\\192.168.1.100\\投資研究處（勾「記住認證」）即可重存。

## 安裝排程單元

\`\`\`bash
sudo cp deploy/systemd/report-mark-sync.service deploy/systemd/report-mark-sync.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-sync.timer
systemctl list-timers report-mark-sync.timer
\`\`\`

## 手動驗證一次端到端

\`\`\`bash
make sync-once
tail -n 40 data/sync_run_$(date +%Y%m%d).log
make stats            # 確認 reports 篇數有隨新檔增加
\`\`\`

## 維運排錯

- 502 / 線上變慢：匯入跑在 nice -n 19 + ionice -c3；量大時段可把 timer 改較少頻率
  （改 OnCalendar，如每日凌晨 `*-*-* 03:00:00`）。
- 掛載偶發失敗：service 會記 log 並早退、不動 DB；下次 timer 自動再試。
- 單檔失敗：見 data/sync_failures.log；修因後可 `make sync-once` 或
  `uv run python scripts/sync_new_reports.py --all-local` 全本地對 DB 補漏。
- claude CLI 找不到：確認 service 的 PATH drop-in 含 node bin 目錄。
\`\`\`
```

- [ ] **Step 6: 驗證 unit 語法**

Run: `systemd-analyze verify deploy/systemd/report-mark-sync.service deploy/systemd/report-mark-sync.timer`
Expected: 無 error（可能有「WorkingDirectory 在 /mnt/c」「oneshot 無 [Install]」類 warning，皆可接受）。若回報 `Unknown lvalue` 等 error 則修正後重跑。

- [ ] **Step 7: 提交**

```bash
git add deploy/systemd/report-mark-sync.service deploy/systemd/report-mark-sync.timer \
  deploy/systemd/mount-nas-research deploy/systemd/report-mark-sync.sudoers \
  docs/nas_scheduled_sync_deployment.md
git commit -m "$(cat <<'EOF'
feat(sync): systemd timer/service + root 掛載包裝 + 部署文件

每 3 小時 oneshot 觸發 sync_new_reports.sh；drvfs 掛載走 root 包裝
/usr/local/sbin/mount-nas-research + NOPASSWD sudoers（避免引號地獄、免明文
密碼）；service 帶 PATH drop-in 確保 claude/uv 可用。文件含 drvfs 掛載實測與
快取憑證失效排錯。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 落地後（部署，需使用者 sudo）

依 `docs/nas_scheduled_sync_deployment.md` 執行；本機探勘時 sudo 被擋無法預驗 drvfs-under-systemd 掛載，**部署時 Step「實測 drvfs 掛載」務必先過**，再 enable timer。若 systemd 觸發掛載異常，殼已內建 root 包裝 + 早退保護，可改由 `make sync-once` 在使用者 session 內驗證後再排程。

## Self-Review

- **Spec coverage**：存取（drvfs 掛載）✓Task3/4；同步到本地（rsync delta）✓Task3；增量 extract→tag→ingest ✓Task2；三層去重（rsync size-only / delta 清單 / file_hash）✓Task1+2+3；排程每 3 小時 ✓Task4；nice/ionice + PID lock ✓Task3；錯誤處理（單檔不中斷/掛載早退/NUL）✓Task2/3；測試（純函式單元 + 乾跑煙霧）✓Task1/2；免明文密碼（快取憑證 + root 包裝）✓Task4；不動既有管線/線上服務 ✓（僅新增檔 + Makefile 末尾一目標）。
- **Placeholder scan**：無 TBD/TODO；每個 code step 均為完整可貼程式碼。
- **Type consistency**：`parse_rsync_delta`/`skip_before_tag`/`skip_after_tag` 在 Task1 定義、Task2 `_run()` 取用名稱一致；`ReportRow` 欄位與 `app/services/store.py` 定義逐欄對齊；`extract_text`/`parse_filename`/`embed_texts`/`upsert_report`/`report_exists` 簽名與既有 services 一致。
