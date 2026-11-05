"""修復既有訊號列的評等正規化與誤讀目標價（零 LLM）。

`app/services/signal_extract.py` 的評等表與目標價閘門只管之後新擷取的列，這支把同一套判斷
套回已入庫的列，不重打 LLM、不動 `created_at`（每日簡報的窗期看它，修復不會被當成新訊號）：

- 評等：對 `rating_raw` 重跑 `normalize_rating`，結果不同就更新 `rating_normalized`。
  原本判「無可擷取內容」的列，評等認得之後改回 `valid`。
- 目標價：`suspect_target_note`（數字是括號裡的現價、或表格的現價欄）與
  `implausible_target_note`（同券商 ±45 天中位數差 3 倍以上）命中的列，清空目標價四欄、
  狀態改 `partial`、原因附在 `error_detail`。**只清不補**：錯的值不換成猜的值；原始值留在
  `raw_payload.target_price`。鄰近比對用先過第一道閘門之後還留著的值當基準，現價不當基準。

已經發出去的每日簡報（`report_brief.markdown`）不會跟著改。要重產的話注意：
`scripts/generate_brief.py --date … --force` 對過去的日期會把窗期算成「現在往前 24 小時」，
不是該列原本的窗期，要先處理這點再用（會呼叫 LLM）。

模式：
  uv run python scripts/repair_signal_values.py                     # 試跑：只列出會改什麼，不寫 DB
  uv run python scripts/repair_signal_values.py --out plan.csv      # 試跑並把明細寫成 CSV
  uv run python scripts/repair_signal_values.py --apply --confirm <host:port/db> --out before.csv

`--apply` 必須帶 `--confirm`（逐字等於目標庫）與 `--out`（改動前的值，要還原就靠它），全部在
一個交易內。每句 UPDATE 都帶上讀取時的值當條件：讀完之後才被批次改過的列不覆寫，只計數回報。
可重跑：修過的列不會再命中。

批次不讀 repo 根 `.env`：沒設 `REPORT_MARK_DB_URL` 時連預設的 `localhost:5436/research`；
EC2 要先載入 `/etc/default/report-mark-sync`。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.services.db import DATABASE_URL, SessionFactory  # noqa: E402
from app.services.signal_extract import (  # noqa: E402
    TARGET_NEIGHBOR_DAYS,
    implausible_target_note,
    normalize_rating,
    suspect_target_note,
)

NOTHING_EXTRACTED_NOTE = "無可擷取內容"

LOAD_SQL = """
SELECT id::text, report_id::text, instrument_code, broker, report_date,
       rating_raw, rating_normalized, target_price, target_currency, target_horizon,
       target_price_evidence, extraction_status, error_detail
FROM research.report_signal
"""

UPDATE_SQL = """
UPDATE research.report_signal
SET rating_normalized = :new_rating,
    target_price = CASE WHEN CAST(:clear_target AS boolean) THEN NULL ELSE target_price END,
    target_currency = CASE WHEN CAST(:clear_target AS boolean) THEN NULL ELSE target_currency END,
    target_horizon = CASE WHEN CAST(:clear_target AS boolean) THEN NULL ELSE target_horizon END,
    target_price_evidence = CASE WHEN CAST(:clear_target AS boolean) THEN NULL ELSE target_price_evidence END,
    extraction_status = :new_status,
    error_detail = :new_error
WHERE id = CAST(:id AS uuid)
  AND rating_normalized = :old_rating
  AND target_price IS NOT DISTINCT FROM CAST(:old_target_price AS numeric)
  AND extraction_status = :old_status
  AND error_detail IS NOT DISTINCT FROM CAST(:old_error AS text)
"""

CSV_FIELDS = [
    "id", "report_id", "instrument_code", "broker", "report_date", "rating_raw",
    "old_rating", "new_rating", "old_target_price", "old_target_currency", "old_target_horizon",
    "old_target_evidence", "target_note", "old_status", "new_status", "old_error", "new_error",
]


@dataclass
class SignalRecord:
    id: str
    report_id: str
    instrument_code: str
    broker: Optional[str]
    report_date: Optional[date]
    rating_raw: Optional[str]
    rating_normalized: str
    target_price: Optional[float]
    target_currency: Optional[str] = None
    target_horizon: Optional[str] = None
    target_price_evidence: Optional[str] = None
    extraction_status: str = "valid"
    error_detail: Optional[str] = None
    # DB 的原值，UPDATE 的比對條件用它：float 綁成 numeric 會帶二進位誤差（19.6 → 19.60000000000000142），
    # 跟 numeric(18,4) 的 19.6000 永遠不相等，那列就被靜默略過。
    target_price_exact: Optional[Decimal] = None


@dataclass
class Change:
    record: SignalRecord
    new_rating: str
    target_note: Optional[str] = None
    new_status: str = ""
    new_error: Optional[str] = None


def _append_note(existing: Optional[str], note: str) -> str:
    return f"{existing}；{note}" if existing else note


def _target_notes(records: list[SignalRecord]) -> dict[str, str]:
    """回 {訊號 id: 丟棄目標價的原因}。先看列自己的證據，留下來的值再互相比鄰近中位數。"""
    notes: dict[str, str] = {}
    survivors: dict[tuple[str, str], list[SignalRecord]] = {}
    for rec in records:
        if rec.target_price is None:
            continue
        note = suspect_target_note(rec.target_price, rec.target_price_evidence)
        if note:
            notes[rec.id] = note
        elif rec.broker and rec.report_date:
            survivors.setdefault((rec.instrument_code, rec.broker), []).append(rec)

    window = timedelta(days=TARGET_NEIGHBOR_DAYS)
    for group in survivors.values():
        for rec in group:
            neighbors = [
                (o.target_price, o.target_currency)
                for o in group
                if o.report_id != rec.report_id and abs(o.report_date - rec.report_date) <= window
            ]
            note = implausible_target_note(rec.target_price, rec.target_currency, neighbors)
            if note:
                notes[rec.id] = note
    return notes


def plan_changes(records: list[SignalRecord]) -> list[Change]:
    """純函式：算出每一列要改什麼，沒有要改的列不回。`rejected` 列是另一條重跑路徑，不碰。"""
    target_notes = _target_notes(records)
    changes: list[Change] = []
    for rec in records:
        if rec.extraction_status == "rejected":
            continue
        new_rating = normalize_rating(rec.rating_raw)
        note = target_notes.get(rec.id)
        if new_rating == rec.rating_normalized and note is None:
            continue

        new_status, new_error = rec.extraction_status, rec.error_detail
        if note:
            new_status, new_error = "partial", _append_note(rec.error_detail, note)
        elif rec.error_detail == NOTHING_EXTRACTED_NOTE and new_rating != "unknown":
            new_status, new_error = "valid", None
        changes.append(Change(rec, new_rating, note, new_status, new_error))
    return changes


def _update_params(c: Change) -> dict:
    rec = c.record
    return {
        "id": rec.id,
        "new_rating": c.new_rating,
        "clear_target": c.target_note is not None,
        "new_status": c.new_status,
        "new_error": c.new_error,
        "old_rating": rec.rating_normalized,
        "old_target_price": rec.target_price_exact,
        "old_status": rec.extraction_status,
        "old_error": rec.error_detail,
    }


def _csv_row(c: Change) -> dict:
    rec = c.record
    return {
        "id": rec.id,
        "report_id": rec.report_id,
        "instrument_code": rec.instrument_code,
        "broker": rec.broker,
        "report_date": rec.report_date,
        "rating_raw": rec.rating_raw,
        "old_rating": rec.rating_normalized,
        "new_rating": c.new_rating,
        "old_target_price": rec.target_price,
        "old_target_currency": rec.target_currency,
        "old_target_horizon": rec.target_horizon,
        "old_target_evidence": rec.target_price_evidence,
        "target_note": c.target_note,
        "old_status": rec.extraction_status,
        "new_status": c.new_status,
        "old_error": rec.error_detail,
        "new_error": c.new_error,
    }


def db_target() -> str:
    """目標庫的 `host:port/db`（`--confirm` 要逐字相同，輸出不含帳密）。"""
    url = make_url(DATABASE_URL)
    return f"{url.host}:{url.port or 5432}/{url.database}"


def summarize(changes: list[Change]) -> dict:
    return {
        "rating": dict(Counter(
            f"{c.record.rating_normalized}→{c.new_rating}"
            for c in changes if c.new_rating != c.record.rating_normalized
        ).most_common()),
        "target": dict(Counter(c.target_note for c in changes if c.target_note).most_common()),
        "rows": len(changes),
    }


def write_csv(path: Path, changes: list[Change]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for c in changes:
            writer.writerow(_csv_row(c))


async def load_records() -> list[SignalRecord]:
    async with SessionFactory() as session:
        res = await session.execute(text(LOAD_SQL))
        return [
            SignalRecord(
                id=r[0], report_id=r[1], instrument_code=r[2], broker=r[3], report_date=r[4],
                rating_raw=r[5], rating_normalized=r[6],
                target_price=float(r[7]) if r[7] is not None else None,
                target_currency=r[8], target_horizon=r[9], target_price_evidence=r[10],
                extraction_status=r[11], error_detail=r[12], target_price_exact=r[7],
            )
            for r in res.all()
        ]


async def apply_changes(changes: list[Change]) -> tuple[int, int]:
    """同一個交易全部寫入，回（已更新, 條件不符而略過）。"""
    updated = skipped = 0
    async with SessionFactory() as session:
        async with session.begin():
            for c in changes:
                res = await session.execute(text(UPDATE_SQL), _update_params(c))
                if res.rowcount:
                    updated += 1
                else:
                    skipped += 1
    return updated, skipped


async def main(args) -> int:
    target = db_target()
    print(f"目標庫：{target}", flush=True)
    if args.apply:
        if args.confirm != target:
            print(f"--apply 要帶 --confirm {target}（逐字）", file=sys.stderr)
            return 2
        if not args.out:
            print("--apply 要帶 --out：改動前的值只留在這份 CSV", file=sys.stderr)
            return 2

    records = await load_records()
    changes = plan_changes(records)
    print(f"訊號 {len(records)} 列，要改 {len(changes)} 列", flush=True)
    print(json.dumps(summarize(changes), ensure_ascii=False, indent=2), flush=True)
    for c in changes:
        if c.target_note:
            rec = c.record
            print(
                f"  清目標價 {rec.report_date} {rec.instrument_code} {rec.broker} "
                f"{rec.target_price:g}｜{(rec.target_price_evidence or '')[:60]}",
                flush=True,
            )

    if args.out:
        write_csv(Path(args.out), changes)
        print(f"明細：{args.out}", flush=True)
    if not args.apply:
        print("[試跑] 未寫 DB；加 --apply --confirm … --out … 才寫入", flush=True)
        return 0

    updated, skipped = await apply_changes(changes)
    print(f"已更新 {updated} 列；條件不符而略過 {skipped} 列", flush=True)
    if skipped:
        print("略過的列在讀取後被批次改過；重跑一次，仍有略過就是規則或資料有問題", flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="修復既有訊號列的評等正規化與誤讀目標價（零 LLM）")
    ap.add_argument("--apply", action="store_true", help="寫入 DB（預設只試跑）")
    ap.add_argument("--confirm", help="目標庫 host:port/db，須逐字相同（--apply 必填）")
    ap.add_argument("--out", help="明細 CSV，含改動前的值（--apply 必填）")
    sys.exit(asyncio.run(main(ap.parse_args())))
