"""為缺摘要的研報產生 2-3 句中文摘要 → research_report.summary

- 來源：DB 既有 full_text（語料已導入，無須重跑 ingest）
- 每篇用 `claude -p`(Sonnet) headless 產出 JSON {"summary": "..."}，parse_summary() 解析
- 冪等可續傳：只挑 summary IS NULL 者；重跑天然跳過已補的
- 並發用 asyncio.Semaphore 控制同時的 CLI 呼叫數；失敗重試，壞檔記 data/summary_failures.log
- 回應解析不出摘要的研報記入 research.llm_task_failure，連續 3 輪後不再重打
  （規則見 app/services/llm_failures.py；`--retry-blocked` 手動解除）

用法：uv run python scripts/generate_summaries.py [--workers 2] [--limit N] [--excerpt 12000]

注意：每篇都會冷啟動一個 `claude -p` agent；workers 越高、同時冷啟動越多，磁碟
小檔 I/O（使用時間%）越容易被頂滿。預設壓到 2，並用 --setting-sources '' 略過
全域 settings/hooks/plugins 以降低每次冷啟動的 I/O。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services import llm_failures  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402
from scripts._claude_cli import CliNotFoundError, CliResult, run_claude  # noqa: E402
from scripts._claude_cli import build_cli_args as _build_cli_args  # noqa: E402
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
    """組 `claude -p` 的 argv（本腳本固定用 MODEL；實作見 scripts/_claude_cli.py）。"""
    return _build_cli_args(prompt, MODEL)


def call_cli(prompt: str, timeout: int = 180) -> CliResult:
    """呼叫 `claude -p`。回 (stdout, None) 或 (None, 可辨識的失敗原因)。

    原本是 `except (subprocess.TimeoutExpired, Exception): return None`——那個
    tuple 的第二項讓第一項完全沒有意義（Exception 已涵蓋 TimeoutExpired），
    所有失敗一律塌縮成 None。見 scripts/_claude_cli.py 的四天停擺紀錄。
    """
    return run_claude(prompt, MODEL, timeout=timeout)


async def summarize_one(
    sem: asyncio.Semaphore,
    rid: str,
    file_name: str,
    full_text: str,
    excerpt: int,
    total: int,
    retries: int = 2,
    file_hash: Optional[str] = None,
    recorder: Optional[llm_failures.FailureRecorder] = None,
) -> None:
    global _done, _ok, _fail
    prompt = build_prompt(file_name, full_text, excerpt)
    summary: Optional[str] = None
    # 保留最後一次的失敗原因：log 要分得出「環境壞了」與「回了但解析不採信」
    last_error = "CLI 無回應"
    # 只有「回了但不能用」才記入跳過名單；環境型失敗不記（見 llm_failures 模組說明）
    content_failed = False
    async with sem:
        for _ in range(retries + 1):
            # CliNotFoundError 刻意不接：環境層級失敗，讓它拋到 main 中止整批
            res = await asyncio.to_thread(call_cli, prompt)
            if res.text:
                summary = parse_summary(res.text)
                if summary:
                    break
                last_error = "回應無法解析為摘要"
                content_failed = True
            elif res.error:
                last_error = res.error

    if summary:
        async with SessionFactory() as session:
            await session.execute(
                text("UPDATE research.research_report SET summary = :s WHERE id = :id"),
                {"s": summary, "id": rid},
            )
            await session.commit()
        if recorder:
            await recorder.clear(file_hash)
        _ok += 1
    else:
        if recorder and content_failed:
            await recorder.record(file_hash, llm_failures.UNPARSEABLE)
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{rid}\t{file_name}\t{last_error}\n")
        _fail += 1

    _done += 1
    if _done % 20 == 0 or _done == total:
        print(f"  {_done}/{total}  ok={_ok}  fail={_fail}", flush=True)


def read_hashes_file(path: str) -> list[str]:
    """讀殼層寫的 file_hash 清單（每行一個），去除空白行與前後空白。"""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [h.strip() for h in lines if h.strip()]


def build_candidates_sql(by_hashes: bool, skip_blocked: bool, limit: bool) -> str:
    """待補摘要的查詢（純字串，供測試斷言）。skip_blocked 時排除跳過名單上的研報。"""
    sql = (
        "SELECT r.id::text, r.file_name, r.full_text, r.file_hash "
        "FROM research.research_report r "
        "WHERE r.summary IS NULL AND r.full_text IS NOT NULL AND r.is_research IS NOT FALSE "
    )
    if by_hashes:
        sql += "AND r.file_hash = ANY(:hashes) "
    if skip_blocked:
        sql += "AND " + llm_failures.skip_clause_sql("r") + " "
    sql += "ORDER BY r.report_date DESC NULLS LAST, r.file_name"
    if limit:
        sql += " LIMIT :limit"
    return sql


async def fetch_candidates(
    limit: Optional[int], hashes: Optional[list[str]] = None, skip_blocked: bool = False
) -> list[tuple[str, str, str, str]]:
    """挑待補摘要的列：summary IS NULL 的研究報告。

    hashes 為 None（預設）＝掃全表所有 NULL（手動補積壓 make summaries）。
    hashes 為清單＝只補這批 file_hash（定時匯入只針對本輪新研報，避免掃積壓）；
    空清單代表本輪無新研報，直接回空、不查 DB。
    skip_blocked＝排除 research.llm_task_failure 判定該跳過的研報（同 MODEL）。
    """
    params: dict = {}
    if hashes is not None:
        if not hashes:
            return []
        params["hashes"] = hashes
    if skip_blocked:
        params.update(llm_failures.skip_params(llm_failures.TASK_SUMMARY, MODEL))
    if limit:
        params["limit"] = limit
    sql = build_candidates_sql(hashes is not None, skip_blocked, bool(limit))
    async with SessionFactory() as session:
        rows = await session.execute(text(sql), params)
        return [(r[0], r[1], r[2], r[3]) for r in rows.all()]


async def main(
    workers: int,
    limit: Optional[int],
    excerpt: int,
    hashes_file: Optional[str] = None,
    retry_blocked: bool = False,
) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    hashes = read_hashes_file(hashes_file) if hashes_file else None
    scope = f"本輪 {len(hashes)} 篇" if hashes is not None else "全表 NULL"
    recorder = await llm_failures.open_recorder(llm_failures.TASK_SUMMARY, MODEL, SessionFactory)
    cands = await fetch_candidates(limit, hashes, skip_blocked=recorder is not None and not retry_blocked)
    total = len(cands)
    print(
        f"candidates: {total} | scope: {scope} | workers: {workers} | model: {MODEL}",
        flush=True,
    )
    if not total:
        print("nothing to do（皆已有摘要）", flush=True)
        return
    sem = asyncio.Semaphore(workers)
    try:
        await asyncio.gather(
            *(
                summarize_one(sem, rid, fn, ft, excerpt, total, file_hash=fh, recorder=recorder)
                for rid, fn, ft, fh in cands
            )
        )
    except CliNotFoundError as exc:
        # 環境層級失敗：剩下的每一篇都會踩到同一顆地雷 → 中止並以非零碼收場。
        # 這一段在排程殼裡是 best-effort（失敗只記 log 不擋下一輪），所以更需要
        # 讓退出碼說話——否則「跑完 N 次註定失敗、印 ok=0、exit 0」不會留下痕跡。
        print(f"\n中止：{exc}", flush=True)
        print(f"（已完成 {_done}/{total}；ok={_ok} fail={_fail}）", flush=True)
        raise SystemExit(2) from exc
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
    ap.add_argument(
        "--retry-blocked",
        action="store_true",
        help="不套跳過名單（research.llm_task_failure），連已判定跳過的研報也重打",
    )
    args = ap.parse_args()
    with claude_cli_lock_or_exit("generate_summaries"):
        asyncio.run(
            main(args.workers, args.limit, args.excerpt, args.hashes_file, args.retry_blocked)
        )
