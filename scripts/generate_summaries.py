"""為缺摘要的研報生成 2-3 句中文摘要 → research_report.summary

- 來源：DB 既有 full_text（語料已導入，無須重跑 ingest）
- 每篇用 `claude -p`(Sonnet) headless 產出 JSON {"summary": "..."}，parse_summary() 解析
- 冪等可續傳：只挑 summary IS NULL 者；重跑天然跳過已補的
- 並發用 asyncio.Semaphore 控制同時的 CLI 呼叫數；失敗重試，壞檔記 data/summary_failures.log

用法：uv run python scripts/generate_summaries.py [--workers 2] [--limit N] [--excerpt 12000]

注意：每篇都會冷啟動一個 `claude -p` agent；workers 越高、同時冷啟動越多，磁碟
小檔 I/O（使用時間%）越容易被頂滿。預設壓到 2，並用 --setting-sources '' 略過
全域 settings/hooks/plugins 以降低每次冷啟動的 I/O。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "summary_failures.log"
MODEL = "claude-sonnet-5"
MAX_SUMMARY_CHARS = 400  # 安全上限，避免模型暴走輸出整段

PROMPT_INSTRUCTION = (
    "你是金融研報的摘要助理。請閱讀以下研報內文，為它寫一段繁體中文摘要，"
    "讓讀者不必打開全文就能掌握重點。要求：\n"
    "1. 2-3 句、約 100-150 字。\n"
    "2. 點出本篇的主題、核心觀點/結論，以及涵蓋的標的或產業（若有）。\n"
    "3. 客觀轉述，不要加入你的評論、不要用 markdown、不要加標題或前後綴。\n"
    "4. 只輸出單一 JSON 物件：{\"summary\": \"……\"}，不要輸出其他文字。"
)

_done = 0
_ok = 0
_fail = 0


def build_prompt(file_name: str, full_text: str, excerpt: int) -> str:
    body = (full_text or "")[:excerpt]
    return (
        f"{PROMPT_INSTRUCTION}\n\n"
        f"檔名：{file_name}\n"
        f"報告內文（前 {excerpt} 字摘錄）：\n{body}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    )


def parse_summary(raw: str) -> Optional[str]:
    """容錯解析 Claude 回應，取出 summary 純文字。

    優先解析 {"summary": "..."} JSON（容忍 ``` 圍欄與前後雜訊）；
    若回應根本沒有大括號則退而把整段文字當摘要。JSON 在但解析失敗 → None。
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
    # 有大括號 → 視為應輸出 JSON：解析失敗/欄位不對一律當壞檔回 None；
    # 完全沒有大括號才退而把整段純文字當摘要。
    if "{" in s:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            return None
        val = obj.get("summary")
        if not isinstance(val, str):
            return None
        s = val
    cleaned = " ".join(s.split()).strip()
    if not cleaned:
        return None
    # 「繁體中文摘要」是 prompt 的機率性保證；這裡確定性收尾。轉換在截長之前，
    # 讓存進 DB 的字串與長度上限描述的是同一個（詞組表可能改變長度）。
    return to_traditional(cleaned)[:MAX_SUMMARY_CHARS]


def build_cli_args(prompt: str) -> list[str]:
    """組 `claude -p` 的 argv。

    `--setting-sources ""`＝不載入任何 settings 來源（user/project/local），
    連帶略過全域 hooks/plugins/CLAUDE.md。摘要只是單次補全、不需這些，而每次
    冷啟動載入它們正是磁碟小檔 I/O 的主因（實測加此 flag 後 page fault 降約 74%）。
    """
    # 去掉 NUL：部分 PDF 抽出的文字含 \x00，POSIX argv 不可含 NUL，否則 subprocess 直接拋
    prompt = prompt.replace("\x00", "")
    return ["claude", "-p", prompt, "--model", MODEL, "--setting-sources", ""]


def call_cli(prompt: str, timeout: int = 180) -> Optional[str]:
    try:
        # cwd 設 /tmp 避免載入專案 CLAUDE.md 拖慢每次呼叫
        r = subprocess.run(
            build_cli_args(prompt),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
        )
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, Exception):
        return None


async def summarize_one(
    sem: asyncio.Semaphore,
    rid: str,
    file_name: str,
    full_text: str,
    excerpt: int,
    total: int,
    retries: int = 2,
) -> None:
    global _done, _ok, _fail
    prompt = build_prompt(file_name, full_text, excerpt)
    summary: Optional[str] = None
    async with sem:
        for _ in range(retries + 1):
            raw = await asyncio.to_thread(call_cli, prompt)
            summary = parse_summary(raw) if raw else None
            if summary:
                break

    if summary:
        async with SessionFactory() as session:
            await session.execute(
                text("UPDATE research.research_report SET summary = :s WHERE id = :id"),
                {"s": summary, "id": rid},
            )
            await session.commit()
        _ok += 1
    else:
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{rid}\t{file_name}\n")
        _fail += 1

    _done += 1
    if _done % 20 == 0 or _done == total:
        print(f"  {_done}/{total}  ok={_ok}  fail={_fail}", flush=True)


def read_hashes_file(path: str) -> list[str]:
    """讀殼層寫的 file_hash 清單（每行一個），去除空白行與前後空白。"""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [h.strip() for h in lines if h.strip()]


async def fetch_candidates(
    limit: Optional[int], hashes: Optional[list[str]] = None
) -> list[tuple[str, str, str]]:
    """挑待補摘要的列：summary IS NULL 的研究報告。

    hashes 為 None（預設）＝掃全表所有 NULL（手動補積壓 make summaries）。
    hashes 為清單＝只補這批 file_hash（定時匯入只針對本輪新研報，避免掃積壓）；
    空清單代表本輪無新研報，直接回空、不查 DB。
    """
    sql = (
        "SELECT id::text, file_name, full_text "
        "FROM research.research_report "
        "WHERE summary IS NULL AND full_text IS NOT NULL AND is_research IS NOT FALSE "
    )
    params: dict = {}
    if hashes is not None:
        if not hashes:
            return []
        sql += "AND file_hash = ANY(:hashes) "
        params["hashes"] = hashes
    sql += "ORDER BY report_date DESC NULLS LAST, file_name"
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    async with SessionFactory() as session:
        rows = await session.execute(text(sql), params)
        return [(r[0], r[1], r[2]) for r in rows.all()]


async def main(
    workers: int, limit: Optional[int], excerpt: int, hashes_file: Optional[str] = None
) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    hashes = read_hashes_file(hashes_file) if hashes_file else None
    scope = f"本輪 {len(hashes)} 篇" if hashes is not None else "全表 NULL"
    cands = await fetch_candidates(limit, hashes)
    total = len(cands)
    print(
        f"candidates: {total} | scope: {scope} | workers: {workers} | model: {MODEL}",
        flush=True,
    )
    if not total:
        print("nothing to do（皆已有摘要）", flush=True)
        return
    sem = asyncio.Semaphore(workers)
    await asyncio.gather(
        *(summarize_one(sem, rid, fn, ft, excerpt, total) for rid, fn, ft in cands)
    )
    print(f"\ndone. summarized_ok={_ok} fail={_fail}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--excerpt", type=int, default=12000)
    ap.add_argument(
        "--hashes-file",
        default=None,
        help="只補此檔列出的 file_hash（每行一個）；不給＝補全表所有 summary IS NULL",
    )
    args = ap.parse_args()
    with claude_cli_lock_or_exit("generate_summaries"):
        asyncio.run(main(args.workers, args.limit, args.excerpt, args.hashes_file))
