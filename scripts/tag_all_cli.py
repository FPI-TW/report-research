"""用 `claude -p`(Haiku)對全量候選研報做市場/標的分類 → data/tags/<hash>.json

- 來源:data/extracted/all.jsonl,濾掉 is_admin / scanned 後為候選
- 每篇用 claude CLI headless(Haiku)分類,parse_tags() 正規化後寫檔
- 可續傳:已存在且可解析的 tag 直接跳過
- 並發(ThreadPool),失敗重試,壞檔記錄到 data/tag_failures.log
用法:uv run python scripts/tag_all_cli.py [--workers 8] [--limit N] [--excerpt 10000]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.tagging import TAG_INSTRUCTION, parse_tags  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALL = ROOT / "data" / "extracted" / "all.jsonl"
TAGS_DIR = ROOT / "data" / "tags"
FAIL_LOG = ROOT / "data" / "tag_failures.log"
MODEL = "claude-haiku-4-5"

_lock = threading.Lock()
_done = 0
_ok = 0
_fail = 0


def build_prompt(file_name: str, text: str, excerpt: int) -> str:
    body = (text or "")[:excerpt]
    return (
        f"{TAG_INSTRUCTION}\n\n"
        f"檔名：{file_name}\n"
        f"報告內文（前 {excerpt} 字摘錄）：\n{body}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    )


def call_cli(prompt: str, timeout: int = 150) -> str | None:
    # 去掉 NUL 位元組：部分 PDF 抽出的文字含 \x00，會讓 subprocess 直接拋
    # ValueError('embedded null byte')（POSIX argv 不可含 NUL），導致該檔永久標註失敗。
    prompt = prompt.replace("\x00", "")
    try:
        # cwd 設 /tmp 避免載入專案 CLAUDE.md 拖慢每次呼叫
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
        )
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, Exception):
        return None


def tag_one(rec: dict, excerpt: int, retries: int = 2) -> str:
    h = rec["file_hash"]
    out_path = TAGS_DIR / f"{h}.json"
    if out_path.exists() and parse_tags(out_path.read_text(encoding="utf-8")):
        return "skip"
    prompt = build_prompt(rec["file_name"], rec.get("text", ""), excerpt)
    for _ in range(retries + 1):
        raw = call_cli(prompt)
        tag = parse_tags(raw) if raw else None
        if tag is not None:
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
            tmp = out_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            tmp.rename(out_path)
            return "ok"
    with _lock, open(FAIL_LOG, "a", encoding="utf-8") as f:
        f.write(f"{h}\t{rec['file_name']}\n")
    return "fail"


def main(workers: int, limit: int | None, excerpt: int) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    recs = []
    with open(ALL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("_failed") or r.get("is_admin") or r.get("scanned"):
                continue
            recs.append(r)
    if limit:
        recs = recs[:limit]
    total = len(recs)
    print(f"candidates: {total} | workers: {workers} | model: {MODEL}", flush=True)

    global _done, _ok, _fail
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(tag_one, r, excerpt): r for r in recs}
        for fut in as_completed(futs):
            res = fut.result()
            with _lock:
                _done += 1
                if res == "ok":
                    _ok += 1
                elif res == "fail":
                    _fail += 1
                if _done % 100 == 0 or _done == total:
                    print(
                        f"  {_done}/{total}  ok+skip={_done - _fail}  fail={_fail}",
                        flush=True,
                    )
    print(f"\ndone. tagged_ok(this run)={_ok} fail={_fail}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--excerpt", type=int, default=10000)
    args = ap.parse_args()
    main(args.workers, args.limit, args.excerpt)
