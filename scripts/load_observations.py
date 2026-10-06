#!/usr/bin/env python3
"""把監控 spool 冪等匯入 DB（report-mark-load-observations.timer，每 5 分鐘；零 LLM）。

用法：
    uv run python scripts/load_observations.py                 # 匯入 $OPS_SPOOL_DIR（預設 data/ops_spool）
    uv run python scripts/load_observations.py --dry-run       # 只解析、印出會匯入多少，不連 DB、不前進進度
    uv run python scripts/load_observations.py --spool-dir DIR

spool 的寫入端有兩個，都不連 DB——DB 掛掉的時候正是最需要觀測的時候，所以先落在本機，這支事後補匯入
（SQL 在 `app/services/ops_monitoring.py`）：
- `scripts/collect_resource_usage.py`：observations-*.jsonl、jobs-*.jsonl → `research.service_observation`／
  `research.job_execution`
- `scripts/incident_handler.sh`（P5）：incidents-*.jsonl（每次已落地的狀態轉換一行）與 `journal/*.log`
  （FIRING／RESOLVED 當下擷取的 journal 片段，行內以 `journal_file` 引用）→ `research.incident`／
  `research.incident_event`。片段匯入前去控制字元並遮掉形似祕密的片段；DB commit 之後才刪片段檔。

**冪等**：每列都有自然鍵（觀測：對象＋指標＋時間＋scope＋主機；批次：主機＋unit＋InvocationID；事件：P5 決定的
event_id），重匯同一份
spool 不會重複。進度記在 `<spool>/.loader-state.json`（每個檔讀到哪個位元組＋inode），**只在 DB commit 之後
才前進**：commit 前被砍、DB 不可用、交易失敗，下一輪從同一個位置重讀，靠自然鍵去重。最後一行沒有換行
（寫入端正寫到一半）不讀，下一輪再讀。

**只碰自己的檔**：只讀 `observations-`／`jobs-`／`incidents-YYYYMMDD.jsonl` 與 `journal/` 底下被事件行引用的
片段；同目錄其他的檔一律不讀、不刪。自己的檔裡若出現不認得的紀錄（`type` 與檔案種類不符，或 `v` 不是 1），
原樣留在 spool：不匯入，而且那個檔從此不會被清理刪除，留給認得它的版本處理。

清理：匯入完成的舊日檔（檔名日期早於今天、讀到檔尾、五分鐘內沒有再被寫過、沒有不認得的紀錄）刪掉；事件行
引用的 journal 片段在 commit 之後刪掉（正本在 journald）；沒有任何事件行引用、而且一小時沒動過的片段（P5 寫完
片段卻沒寫進那一行，例如磁碟滿）當孤兒刪掉。收集器另有保留期修剪當安全網，但它只修剪觀測與批次兩種檔——
事件檔量很小、又是不可重建的歷史，只在匯入之後才刪。

退出碼：0 正常（含沒有東西可匯入）；2 DB 不可用或交易失敗（spool 保留，下一輪補匯入）；
75 另一份 loader 正在跑（不跑，不是跑壞）。
**刻意不掛 OnFailure 告警**：DB 掛掉時這支每 5 分鐘 rc=2，而 report-mark-alert@ 沒有去重；DB 掛掉本身
已由 web 探針（/healthz 探 DB）經 P5 帶去重地告警，匯入停擺只會讓管理頁的監控資料變舊。
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import ops_monitoring  # noqa: E402

EXIT_OK, EXIT_DB, EXIT_LOCKED = 0, 2, 75
STATE_FILE = ".loader-state.json"
LOCK_FILE = ".loader.lock"
# 檔案種類 → 該檔每行應有的 type。只認這兩種，其餘的檔不碰。
FILE_TYPES = {"observations": "observation", "jobs": "job", "incidents": "incident_event"}
SPOOL_FILE = re.compile(r"(observations|jobs|incidents)-(\d{8})\.jsonl")
JOURNAL_DIR = "journal"
# 事件行裡的 journal_file 只接受 `journal/<安全檔名>.log`（不得跳出 spool）
JOURNAL_REF = re.compile(r"journal/([A-Za-z0-9_.:-]{1,200}\.log)")
JOURNAL_READ_MAX = ops_monitoring.JOURNAL_MAX_BYTES + 4096
JOURNAL_ORPHAN_SECONDS = 3600
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
# 舊日檔要「這麼久沒再被寫過」才刪：收集器在午夜前一刻算好檔名、午夜後才寫完的那一批不會被刪掉。
CLEANUP_QUIET_SECONDS = 300


def default_spool_dir() -> Path:
    return Path(os.environ.get("OPS_SPOOL_DIR") or (REPO_ROOT / "data" / "ops_spool"))


def load_state(spool: Path) -> dict[str, dict]:
    try:
        data = json.loads((spool / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    out = {}
    for name, entry in files.items():
        if (isinstance(entry, dict) and isinstance(entry.get("offset"), int) and entry["offset"] >= 0
                and isinstance(entry.get("ino"), int)):
            out[name] = {"offset": entry["offset"], "ino": entry["ino"], "keep": entry.get("keep") is True}
    return out


def save_state(spool: Path, files: dict[str, dict]) -> None:
    tmp = spool / f"{STATE_FILE}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps({"v": 1, "files": files}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, spool / STATE_FILE)


def spool_files(spool: Path) -> list[tuple[Path, str, str]]:
    """(路徑, 種類, 日期)，依日期再依種類排序。不是自己命名規則的檔一律不碰。"""
    out = []
    for path in spool.iterdir():
        m = SPOOL_FILE.fullmatch(path.name)
        if m and path.is_file():
            out.append((path, m.group(1), m.group(2)))
    return sorted(out, key=lambda t: (t[2], t[1]))


class Batch:
    def __init__(self) -> None:
        self.observations: list[dict] = []
        self.jobs: list[dict] = []
        self.events: list[dict] = []
        self.journals: list[Path] = []  # commit 之後要刪的片段檔
        self.malformed = 0
        self.rejected = 0
        self.deferred = 0
        self.offsets: dict[str, dict] = {}
        self.bytes = 0

    def summary(self) -> str:
        return (f"observations={len(self.observations)} jobs={len(self.jobs)} events={len(self.events)} "
                f"malformed={self.malformed} "
                f"invalid={self.rejected} deferred={self.deferred} bytes={self.bytes}")


def collect(spool: Path, state: dict[str, dict], max_bytes: int) -> Batch:
    """讀每個 spool 檔從上次位置到最後一個換行為止，轉成待匯入的列（不連 DB）。"""
    batch = Batch()
    for path, kind, _day in spool_files(spool):
        if batch.bytes >= max_bytes:
            break  # 這一輪的量夠了，其餘下一輪（DB 長期掛掉後的積壓分批補）
        try:
            st = path.stat()
        except OSError:
            continue
        prev = state.get(path.name)
        offset = prev["offset"] if prev else 0
        keep = bool(prev and prev["keep"])
        if prev and (prev["ino"] != st.st_ino or prev["offset"] > st.st_size):
            offset, keep = 0, False  # 檔案被換掉或截短：從頭讀，自然鍵會擋掉重複
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(max_bytes - batch.bytes)
        except OSError:
            continue
        end = data.rfind(b"\n")
        chunk = data[: end + 1] if end >= 0 else b""
        batch.bytes += len(chunk)
        for line in chunk.split(b"\n"):
            if not line.strip():
                continue
            try:
                # 事件行由 bash 組出來，摘要可能帶截斷到一半的多位元組字：替換掉而不是整行丟掉
                rec = json.loads(line.decode("utf-8", "replace") if kind == "incidents" else line)
            except ValueError:
                batch.malformed += 1  # 寫入端被砍時留下的半行：之前之後的行都還有效
                continue
            if not isinstance(rec, dict):
                batch.malformed += 1
                continue
            if rec.get("type") != FILE_TYPES[kind] or rec.get("v") != ops_monitoring.SPOOL_VERSION:
                batch.deferred += 1  # 不認得：原樣留在 spool，這個檔不再被清理刪除
                keep = True
                continue
            _add_record(batch, kind, rec, spool)
        batch.offsets[path.name] = {"offset": offset + len(chunk), "ino": st.st_ino, "keep": keep}
    return batch


def journal_path(spool: Path, ref) -> Path | None:
    if not isinstance(ref, str):
        return None
    m = JOURNAL_REF.fullmatch(ref)
    return spool / JOURNAL_DIR / m.group(1) if m else None


def read_journal(path: Path | None) -> str | None:
    """片段檔 → 文字（有讀取上限；超過時讀最後那一段，交給 clean_journal 再截）。檔案不在就是沒有片段。"""
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > JOURNAL_READ_MAX:
                fh.seek(size - JOURNAL_READ_MAX)
            return fh.read(JOURNAL_READ_MAX).decode("utf-8", "replace")
    except OSError:
        return None


def _add_record(batch: Batch, kind: str, rec: dict, spool: Path) -> None:
    if kind == "incidents":
        jpath = journal_path(spool, rec.get("journal_file"))
        row = ops_monitoring.incident_event_row(rec, read_journal(jpath))
        if row is None:
            batch.rejected += 1
        else:
            batch.events.append(row)
        if jpath is not None:
            batch.journals.append(jpath)  # 這一行已被消費（匯入或判不合格），commit 後片段一併刪
        return
    if kind == "observations":
        rows = ops_monitoring.observation_rows(rec)
        if rows is None:
            batch.rejected += 1
        else:
            batch.observations.extend(rows)
    else:
        row = ops_monitoring.job_row(rec)
        if row is None:
            batch.rejected += 1
        else:
            batch.jobs.append(row)


def cleanup(spool: Path, state: dict[str, dict], today: str, now: float | None = None) -> dict[str, dict]:
    """commit 之後：刪掉「日期早於今天、讀到檔尾、安靜夠久、沒有不認得的紀錄」的舊日檔，回傳留下的狀態。"""
    now = time.time() if now is None else now
    present = {p.name: (p, d) for p, _k, d in spool_files(spool)}
    kept: dict[str, dict] = {}
    for name, entry in state.items():
        if name not in present:
            continue  # 檔案已不在（收集器的保留期修剪或人工刪除）：狀態一併丟掉
        path, day = present[name]
        try:
            st = path.stat()
        except OSError:
            continue
        if (day < today and not entry["keep"] and entry["ino"] == st.st_ino and entry["offset"] >= st.st_size
                and now - st.st_mtime >= CLEANUP_QUIET_SECONDS):
            path.unlink(missing_ok=True)
            continue
        kept[name] = entry
    return kept


def referenced_journals(spool: Path) -> set[str]:
    """spool 裡所有事件行引用到的片段檔名（含尚未讀到的部分；讀不了的檔視為全部引用中——寧可不刪）。"""
    names: set[str] = set()
    for path, kind, _day in spool_files(spool):
        if kind != "incidents":
            continue
        data = path.read_bytes()  # OSError 交給呼叫端：讀不了就不判孤兒
        for m in re.finditer(rb'"journal_file"\s*:\s*"journal/([A-Za-z0-9_.:-]{1,200}\.log)"', data):
            names.add(m.group(1).decode("ascii"))
    return names


def cleanup_journals(spool: Path, consumed: list[Path], now: float | None = None) -> None:
    """commit 之後：刪掉已消費的片段；再刪「沒有任何事件行引用、而且一小時沒動過」的孤兒與殘留暫存檔。"""
    now = time.time() if now is None else now
    for path in consumed:
        path.unlink(missing_ok=True)
    jdir = spool / JOURNAL_DIR
    if not jdir.is_dir():
        return
    try:
        referenced = referenced_journals(spool)
    except OSError:
        return  # 讀不了事件檔就不判孤兒（寧可多留，不可刪掉還沒匯入的片段）
    for path in jdir.iterdir():
        name = path.name
        is_tmp = name.startswith(".") and ".log.tmp." in name
        if not (is_tmp or JOURNAL_REF.fullmatch(f"{JOURNAL_DIR}/{name}")) or name in referenced:
            continue
        try:
            if path.is_file() and now - path.stat().st_mtime >= JOURNAL_ORPHAN_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            continue


async def import_batch(batch: Batch, session_factory=None) -> ops_monitoring.ImportStats:
    if session_factory is None:
        from app.services.db import SessionFactory as session_factory  # noqa: N813

    async with session_factory() as session:
        stats = await ops_monitoring.import_records(session, observations=batch.observations, jobs=batch.jobs,
                                                    events=batch.events)
        await session.commit()
    return stats


def run(args, session_factory=None, today: str | None = None) -> int:
    spool = Path(args.spool_dir) if args.spool_dir else default_spool_dir()
    if not spool.is_dir():
        print(f"spool 目錄不存在（{spool}），沒有東西可匯入")
        return EXIT_OK
    try:
        lock = open(spool / LOCK_FILE, "a")
    except OSError as exc:
        print(f"開不了鎖檔（{exc}），本次不跑", file=sys.stderr)
        return EXIT_DB
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("另一份 load_observations 正在執行，本次不跑", file=sys.stderr)
            return EXIT_LOCKED
        state = load_state(spool)
        batch = collect(spool, state, args.max_bytes)
        summary = batch.summary()
        if args.dry_run:
            print(f"[dry-run] 會匯入 {summary}")
            return EXIT_OK
        if batch.observations or batch.jobs or batch.events:
            try:
                stats = asyncio.run(import_batch(batch, session_factory))
            except Exception as exc:  # noqa: BLE001 - DB 不可用或交易失敗：一律保留 spool 與進度、下一輪重來
                print(f"DB 不可用或匯入失敗，spool 保留、下一輪補匯入：{exc!r}", file=sys.stderr)
                return EXIT_DB
            summary += f" db_rejected={stats.rejected} lost={stats.lost} incidents={stats.incidents}"
        merged = {**state, **batch.offsets}
        merged = cleanup(spool, merged, today or datetime.now().strftime("%Y%m%d"))
        save_state(spool, merged)
        # 進度存好之後才刪片段：在這之前被砍，下一輪重讀那幾行時片段還在（自然鍵擋掉重複的事件列）
        cleanup_journals(spool, batch.journals)
        print(f"匯入完成 {summary}")
        return EXIT_OK
    finally:
        lock.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="監控 spool → DB（冪等，零 LLM）")
    p.add_argument("--spool-dir", default=None, help="spool 目錄（預設 $OPS_SPOOL_DIR 或 data/ops_spool）")
    p.add_argument("--dry-run", action="store_true", help="只解析、不連 DB、不前進進度")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                   help="單輪最多讀多少位元組（DB 長期掛掉後的積壓分批補；預設 64 MiB）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_bytes <= 0:
        print("--max-bytes 必須大於 0", file=sys.stderr)
        return EXIT_DB
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
