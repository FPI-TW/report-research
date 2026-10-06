#!/usr/bin/env python3
"""把監控 spool 冪等匯入 DB（report-mark-load-observations.timer，每 5 分鐘）。

用法：
    uv run python scripts/load_observations.py                 # 匯入 $OPS_SPOOL_DIR（預設 data/ops_spool）
    uv run python scripts/load_observations.py --dry-run       # 只解析、印出會匯入多少，不連 DB
    uv run python scripts/load_observations.py --spool-dir DIR

spool 的寫入端：`scripts/collect_resource_usage.py`（observations-*.jsonl、jobs-*.jsonl）與
`scripts/incident_handler.sh`（incidents-*.jsonl，FIRING 另有 journal/*.log 片段）。兩者都不連 DB——
DB 掛掉的時候正是最需要觀測的時候，所以觀測先落在本機，這支事後補匯入。

**冪等**：每列都有自然鍵（觀測：對象＋指標＋時間＋scope＋主機；批次：主機＋unit＋InvocationID；
事件：event_id），重匯同一份 spool 不會重複。進度記在 `<spool>/.loader-state.json`（每個檔讀到哪個位元組
＋inode），**只在 DB commit 之後才前進**：commit 前被砍、DB 不可用、交易失敗，下一輪從同一個位置重讀，
靠自然鍵去重。最後一行沒有換行（寫入端正寫到一半）不讀，下一輪再讀。

清理：匯入完成的舊日檔（檔名日期早於今天、而且讀到檔尾）刪掉；FIRING 的 journal 片段匯入後刪掉
（片段的正本在 journald）。journal 片段匯入前以 `ops_agent.backends.redact` 遮掉形似祕密的片段。

退出碼：0 正常（含沒有東西可匯入）；2 DB 不可用或交易失敗（spool 保留，下一輪補匯入）；
75 另一份 loader 正在跑（不跑，不是跑壞）。
**刻意不掛 OnFailure 告警**：DB 掛掉時這支每 5 分鐘 rc=2，而 report-mark-alert@ 沒有去重；DB 掛掉
本身已由 web 探針（/healthz 探 DB）經 P5 帶去重地告警。
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
from ops_agent.backends import redact  # noqa: E402

EXIT_OK, EXIT_DB, EXIT_LOCKED = 0, 2, 75
STATE_FILE = ".loader-state.json"
LOCK_FILE = ".loader.lock"
SPOOL_FILE = re.compile(r"(observations|jobs|incidents)-(\d{8})\.jsonl")
JOURNAL_FILE = re.compile(r"journal/[A-Za-z0-9_.:-]{1,200}\.log")
JOURNAL_READ_MAX = 1024 * 1024
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
JOURNAL_ORPHAN_DAYS = 7


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
        if isinstance(entry, dict) and isinstance(entry.get("offset"), int) and isinstance(entry.get("ino"), int):
            out[name] = {"offset": entry["offset"], "ino": entry["ino"]}
    return out


def save_state(spool: Path, files: dict[str, dict]) -> None:
    tmp = spool / f"{STATE_FILE}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps({"v": 1, "files": files}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, spool / STATE_FILE)


def spool_files(spool: Path) -> list[tuple[Path, str, str]]:
    """(路徑, 類型, 日期)，依日期再依類型排序。不是自己命名規則的檔一律不碰。"""
    out = []
    for path in spool.iterdir():
        m = SPOOL_FILE.fullmatch(path.name)
        if m and path.is_file():
            out.append((path, m.group(1), m.group(2)))
    return sorted(out, key=lambda t: (t[2], t[1]))


def read_journal(spool: Path, rel) -> tuple[str | None, Path | None]:
    """FIRING 的 journal 片段：只接受 `journal/<安全檔名>.log`，有讀取上限，逐行遮祕密。"""
    if not isinstance(rel, str) or not JOURNAL_FILE.fullmatch(rel):
        return None, None
    path = spool / rel
    try:
        with open(path, "rb") as fh:
            raw = fh.read(JOURNAL_READ_MAX)
    except OSError:
        return None, None
    text = raw.decode("utf-8", "replace")
    return "\n".join(redact(line) for line in text.split("\n")), path


class Batch:
    def __init__(self) -> None:
        self.observations: list[dict] = []
        self.jobs: list[dict] = []
        self.events: list[dict] = []
        self.journals: list[Path] = []
        self.malformed = 0
        self.rejected = 0
        self.offsets: dict[str, dict] = {}
        self.bytes = 0


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
        if prev and (prev["ino"] != st.st_ino or prev["offset"] > st.st_size):
            offset = 0  # 檔案被換掉或截短：從頭讀，自然鍵會擋掉重複
        budget = max_bytes - batch.bytes
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(budget)
        except OSError:
            continue
        end = data.rfind(b"\n")
        if end < 0:
            batch.offsets[path.name] = {"offset": offset, "ino": st.st_ino}
            continue
        chunk = data[: end + 1]
        batch.bytes += len(chunk)
        batch.offsets[path.name] = {"offset": offset + len(chunk), "ino": st.st_ino}
        for line in chunk.split(b"\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                batch.malformed += 1
                continue
            if not isinstance(rec, dict):
                batch.malformed += 1
                continue
            _add_record(spool, batch, kind, rec)
    return batch


def _add_record(spool: Path, batch: Batch, kind: str, rec: dict) -> None:
    if kind == "observations":
        rows = ops_monitoring.observation_rows(rec)
        if rows is None:
            batch.rejected += 1
        else:
            batch.observations.extend(rows)
    elif kind == "jobs":
        row = ops_monitoring.job_row(rec)
        if row is None:
            batch.rejected += 1
        else:
            batch.jobs.append(row)
    else:
        journal, jpath = read_journal(spool, rec.get("journal_file"))
        row = ops_monitoring.incident_event_row(rec, journal)
        if row is None:
            batch.rejected += 1
        else:
            batch.events.append(row)
            if jpath is not None:
                batch.journals.append(jpath)


def cleanup(spool: Path, state: dict[str, dict], journals: list[Path], today: str) -> dict[str, dict]:
    """commit 之後：刪掉已匯入的 journal 片段，與「日期早於今天且讀到檔尾」的舊日檔。"""
    for path in journals:
        path.unlink(missing_ok=True)
    # 孤兒片段（事件那一行不合格被略過、或 handler 寫完片段後沒寫成事件行）：放一週後刪
    cutoff = time.time() - JOURNAL_ORPHAN_DAYS * 86400
    for path in (spool / "journal").glob("*.log") if (spool / "journal").is_dir() else ():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    present = {p.name: (p, d) for p, _k, d in spool_files(spool)}
    kept: dict[str, dict] = {}
    for name, entry in state.items():
        if name not in present:
            continue  # 檔案已不在（被收集器的保留期修剪或人工刪除）：狀態一併丟掉
        path, day = present[name]
        try:
            st = path.stat()
        except OSError:
            continue
        if day < today and entry["ino"] == st.st_ino and entry["offset"] >= st.st_size:
            path.unlink(missing_ok=True)
            continue
        kept[name] = entry
    return kept


async def import_batch(batch: Batch, session_factory=None) -> ops_monitoring.ImportStats:
    if session_factory is None:
        from app.services.db import SessionFactory as session_factory  # noqa: N813
    from sqlalchemy import text

    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
        stats = await ops_monitoring.import_records(
            session, observations=batch.observations, jobs=batch.jobs, events=batch.events,
        )
        await session.commit()
    return stats


def run(args, session_factory=None, today: str | None = None) -> int:
    spool = Path(args.spool_dir) if args.spool_dir else default_spool_dir()
    if not spool.is_dir():
        print(f"spool 目錄不存在（{spool}），沒有東西可匯入")
        return EXIT_OK
    lock = open(spool / LOCK_FILE, "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("另一份 load_observations 正在執行，本次不跑", file=sys.stderr)
            return EXIT_LOCKED
        state = load_state(spool)
        batch = collect(spool, state, args.max_bytes)
        summary = (f"observations={len(batch.observations)} jobs={len(batch.jobs)} events={len(batch.events)} "
                   f"malformed={batch.malformed} invalid={batch.rejected} bytes={batch.bytes}")
        if args.dry_run:
            print(f"[dry-run] 會匯入 {summary}")
            return EXIT_OK
        if batch.observations or batch.jobs or batch.events:
            try:
                stats = asyncio.run(import_batch(batch, session_factory))
            except Exception as exc:  # noqa: BLE001 - DB 不可用或交易失敗：一律保留 spool、下一輪重來
                print(f"DB 不可用或匯入失敗，spool 保留、下一輪補匯入：{exc!r}", file=sys.stderr)
                return EXIT_DB
            summary += f" db_rejected={stats.rejected} incidents={len(stats.incident_ids)}"
        merged = {**state, **batch.offsets}
        merged = cleanup(spool, merged, batch.journals, today or datetime.now().strftime("%Y%m%d"))
        save_state(spool, merged)
        print(f"匯入完成 {summary}")
        return EXIT_OK
    finally:
        lock.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="監控 spool → DB（冪等）")
    p.add_argument("--spool-dir", default=None, help="spool 目錄（預設 $OPS_SPOOL_DIR 或 data/ops_spool）")
    p.add_argument("--dry-run", action="store_true", help="只解析、不連 DB、不前進進度")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                   help="單輪最多讀多少位元組（DB 長期掛掉後的積壓分批補；預設 64 MiB）")
    return p


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
