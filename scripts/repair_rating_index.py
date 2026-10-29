"""修復被封面索引表污染的既有研報：重標 `stock_targets`、刪除抄自索引列的訊號。

背景見 `app/services/rating_index.py`：新進研報在標註與訊號擷取前已剔除索引表，這支只處理
修正之前入庫的那批。

流程：
1. 候選＝`is_research` 且內文有索引表的研報（`full_text` 逐篇套 `index_line_numbers`），
   加上有訊號的評等證據本身就是索引列長相（`is_index_row`）的研報。
2. 訊號：評等證據抄自索引列（`evidence_from_index`），或證據本身是索引列長相 → 刪除。
   後者補的是剔除規則抓不到的版型（見 `rating_index` 檔頭「抓不到的」）；誤刪的正當訊號
   會在之後的 `make signals` 從內文重新擷取，所以寧可多刪。
3. 標的：經 `_ingest_core._tag_via_cli` 重標（與 sync 行內標註同一個呼叫點，已含剔除），
   **只就地更新 `stock_targets`**——market、商品類型等其他標籤不重寫，免得一次改動語料分類；
   也不走 `store.upsert_report`（先刪後插、換新 report_id，會沿 FK 清掉摘錄與訊號）。
   標註失敗的篇只刪訊號、標的不動。
4. 每篇一個交易（UPDATE 與 DELETE 同進退）。跑完再跑一輪 `make signals`：刪掉的與新進子集的
   標的會被當成「還沒擷取」補上。

模式：
  uv run python scripts/repair_rating_index.py                  # 盤點：候選數與要刪的訊號，不呼叫 LLM、不寫 DB
  uv run python scripts/repair_rating_index.py --preview --limit 20   # 呼叫 LLM 重標、印新舊標的，不寫 DB
  uv run python scripts/repair_rating_index.py --apply          # 重標並寫入

可重跑：已修過的篇，新標的與現值相同就不寫、也沒有訊號可刪；但每次 `--preview`／`--apply`
都會對全部候選重新呼叫 LLM，續跑請用 `--hashes-file` 縮小範圍。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.llm_models import TASK_TAG  # noqa: E402
from app.services.rating_index import evidence_from_index, index_lines, is_index_row  # noqa: E402
from scripts import _ingest_core  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

# 前置過濾只為了不把全庫 full_text 拉回來：內文有行首日期，或有訊號的評等證據以日期開頭。
# 是不是索引表、索引列在 Python 判。
CANDIDATES_SQL = """
SELECT r.id::text, r.file_hash, r.file_name, r.source, r.stock_targets, r.full_text
FROM research.research_report r
WHERE r.is_research
  AND (r.full_text ~ '(^|\\n)\\s*[0-9]{1,2}/[0-9]{1,2}\\s'
       OR EXISTS (SELECT 1 FROM research.report_signal s
                  WHERE s.report_id = r.id
                    AND s.raw_payload->'rating'->>'evidence' ~ '^\\s*[0-9]{1,2}/[0-9]{1,2}\\s'))
  AND (CAST(:hashes AS text[]) IS NULL OR r.file_hash = ANY(CAST(:hashes AS text[])))
ORDER BY r.created_at
"""

SIGNALS_SQL = """
SELECT s.id::text, s.report_id::text, s.instrument_code, s.raw_payload->'rating'->>'evidence'
FROM research.report_signal s
WHERE s.report_id = ANY(CAST(:ids AS uuid[]))
"""

UPDATE_TARGETS_SQL = """
UPDATE research.research_report SET stock_targets = CAST(:targets AS text[])
WHERE id = CAST(:id AS uuid)
"""

DELETE_SIGNALS_SQL = """
DELETE FROM research.report_signal WHERE id = ANY(CAST(:ids AS uuid[]))
"""


class Candidate:
    __slots__ = ("report_id", "file_hash", "file_name", "source", "old_targets", "full_text",
                 "has_index", "bad_signals", "new_targets", "tag_error")

    def __init__(self, report_id, file_hash, file_name, source, old_targets, full_text):
        self.report_id = report_id
        self.file_hash = file_hash
        self.file_name = file_name
        self.source = source
        self.old_targets = list(old_targets or [])
        self.full_text = full_text
        # 內文沒有索引表、只有可疑訊號的篇：重標的輸入跟當初一樣，不花 LLM、只刪訊號
        self.has_index = False
        self.bad_signals: list[tuple[str, str, str]] = []  # (signal id, code, evidence)
        self.new_targets: list[str] | None = None
        self.tag_error: str | None = None


async def load_candidates(hashes: list[str] | None) -> list[Candidate]:
    async with SessionFactory() as s:
        rows = (await s.execute(text(CANDIDATES_SQL), {"hashes": hashes})).all()
        cands: dict[str, tuple[Candidate, list[str]]] = {}
        for rid, fh, fn, src, targets, full_text in rows:
            cand = Candidate(rid, fh, fn, src, targets, full_text)
            lines = index_lines(full_text)
            cand.has_index = bool(lines)
            cands[rid] = (cand, lines)
        if cands:
            sigs = (await s.execute(text(SIGNALS_SQL), {"ids": list(cands)})).all()
            for sid, rid, code, evidence in sigs:
                cand, lines = cands[rid]
                if is_index_row(evidence or "") or evidence_from_index(evidence, code, lines):
                    cand.bad_signals.append((sid, code, evidence))
    return [c for c, _ in cands.values() if c.has_index or c.bad_signals]


def retag(cand: Candidate) -> None:
    tag, error = _ingest_core._tag_via_cli(cand.file_name, cand.full_text, file_hash=cand.file_hash)
    if tag is None:
        cand.tag_error = error
    else:
        cand.new_targets = list(tag.stock_targets or [])


async def apply_one(cand: Candidate) -> None:
    async with SessionFactory() as s, s.begin():
        if cand.new_targets is not None and cand.new_targets != cand.old_targets:
            await s.execute(text(UPDATE_TARGETS_SQL), {"targets": cand.new_targets, "id": cand.report_id})
        if cand.bad_signals:
            await s.execute(text(DELETE_SIGNALS_SQL), {"ids": [sid for sid, _, _ in cand.bad_signals]})


def _print_plan(cands: list[Candidate]) -> None:
    by_source: dict[str, int] = {}
    for c in cands:
        by_source[c.source or "?"] = by_source.get(c.source or "?", 0) + 1
    n_sig = sum(len(c.bad_signals) for c in cands)
    n_tag = sum(1 for c in cands if c.has_index)
    print(f"候選研報 {len(cands)} 篇（{by_source}），其中 {n_tag} 篇要重標"
          f"｜抄自索引列的訊號 {n_sig} 筆", flush=True)


async def main(args) -> None:
    hashes = None
    if args.hashes_file:
        hashes = [h.strip() for h in Path(args.hashes_file).read_text().split() if h.strip()]
    cands = await load_candidates(hashes)
    if args.limit:
        cands = cands[: args.limit]
    _print_plan(cands)
    if not (args.preview or args.apply):
        for c in cands:
            for _, code, evidence in c.bad_signals:
                print(f"  刪 {c.file_name}｜{code}｜{evidence}", flush=True)
        print("[盤點] 未呼叫 LLM、未寫 DB；加 --preview 或 --apply", flush=True)
        return

    sem = asyncio.Semaphore(args.workers)
    done = 0

    async def run(c: Candidate) -> None:
        nonlocal done
        async with sem:
            if c.has_index:
                await asyncio.to_thread(retag, c)
            if args.apply:
                await apply_one(c)
        done += 1
        if not c.has_index:
            status = "內文無索引表，不重標"
        elif c.tag_error:
            status = f"標註失敗（{c.tag_error}），只刪訊號"
        elif c.new_targets == c.old_targets:
            status = "標的不變"
        else:
            status = f"{c.old_targets} → {c.new_targets}"
        print(f"[{done}/{len(cands)}] {c.file_name}｜{status}｜刪訊號 {len(c.bad_signals)}", flush=True)

    await asyncio.gather(*(run(c) for c in cands))
    changed = sum(1 for c in cands if c.new_targets is not None and c.new_targets != c.old_targets)
    failed = sum(1 for c in cands if c.tag_error)
    verb = "已寫入" if args.apply else "[preview] 未寫入"
    print(f"{verb}：標的變動 {changed} 篇、標註失敗 {failed} 篇、"
          f"刪訊號 {sum(len(c.bad_signals) for c in cands)} 筆", flush=True)
    if args.apply:
        print("下一步：make signals（補擷取被刪與新增標的的訊號）", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="呼叫 LLM 重標並印出新舊標的，不寫 DB")
    mode.add_argument("--apply", action="store_true", help="重標並寫入（每篇一個交易）")
    ap.add_argument("--hashes-file", default=None, help="只處理這些 file_hash（空白分隔）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4, help="同時的 LLM 呼叫數")
    args = ap.parse_args()
    # 取鎖之前預檢模型與金鑰（缺金鑰是「跑了也白跑」，要在撞鎖 rc=75 之前說出來）。
    if args.preview or args.apply:  # 盤點不呼叫 LLM
        require_llm_key({TASK_TAG: _ingest_core.TAG_MODEL})
    with claude_cli_lock_or_exit("repair_rating_index"):
        asyncio.run(main(args))
