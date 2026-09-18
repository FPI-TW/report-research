"""列出摘錄錨點失效的研報，輸出 hashes 檔給 `extract_takeaways.py --hashes-file … --reextract`。

抽取回填（`scripts/backfill_extraction.py`）會改寫正典文字並用 `store.reanchor_takeaways`
重算 `quote_start`／`quote_end`；引文在新文字裡找不到（表格改成 markdown、閱讀順序變了、
句子跨到不同區塊）就置 NULL，閱讀頁從此不能跳到那條摘錄。v3 回填實測 15% 的摘錄如此。
這支把「有 NULL 錨點的研報」列成 delta，補救路徑與 `scripts/failures_to_delta.py` 同形：

    uv run python scripts/lost_anchors_to_delta.py --out data/takeaway_reanchor_delta.txt
    uv run python scripts/extract_takeaways.py --hashes-file data/takeaway_reanchor_delta.txt --reextract

**唯讀**：只查 DB、只寫 `--out`。`--reextract` 會對這些研報重跑一次 LLM（每篇一次 Sonnet），
所以預設只列 `--min-lost` 條以上的研報，且可用 `--limit` 分批。退出碼 0；沒有失效錨點時
寫出空檔並印 0。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402

LOST_SQL = """
SELECT r.file_hash,
       count(*) FILTER (WHERE t.anchor_method IS NULL) AS lost,
       count(*) AS total,
       max(r.report_date) AS report_date
FROM research.report_takeaway t
JOIN research.research_report r ON r.id = t.report_id
WHERE (:since_days IS NULL OR r.report_date >= current_date - (:since_days::int))
GROUP BY r.file_hash
HAVING count(*) FILTER (WHERE t.anchor_method IS NULL) >= :min_lost
ORDER BY lost DESC, report_date DESC NULLS LAST
LIMIT :limit
"""


def render_delta(rows: list[tuple]) -> str:
    """一行一個 file_hash（與 `read_hashes_file` 的格式相同）。"""
    return "".join(f"{r[0]}\n" for r in rows)


async def fetch(min_lost: int, since_days: int | None, limit: int) -> list[tuple]:
    async with SessionFactory() as session:
        result = await session.execute(
            sql_text(LOST_SQL), {"min_lost": min_lost, "since_days": since_days, "limit": limit}
        )
        return [tuple(r) for r in result.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="hashes 檔落點（一行一個 file_hash）")
    ap.add_argument("--min-lost", type=int, default=1, help="至少幾條摘錄錨不回才列入")
    ap.add_argument("--since-days", type=int, default=None, help="只看 report_date 在幾天內的研報")
    ap.add_argument("--limit", type=int, default=10**6, help="最多列幾篇（分批補救用）")
    args = ap.parse_args()

    try:
        rows = asyncio.run(fetch(args.min_lost, args.since_days, args.limit))
    except Exception as exc:  # noqa: BLE001
        print(f"DB 不可用：{exc!r}", file=sys.stderr)
        return 2
    lost = sum(r[1] for r in rows)
    total = sum(r[2] for r in rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_delta(rows), encoding="utf-8")
    print(f"錨點失效的研報 {len(rows)} 篇｜失效摘錄 {lost}/{total}｜已寫 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
