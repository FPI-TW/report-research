"""用 `claude -p`(Haiku)對全量候選研報做市場/標的分類 → data/tags/<hash>.json

- 來源:data/extracted/<hash>.json（per-hash 快取，E1c）,濾掉 is_admin / scanned 後為候選
- 每篇用 claude CLI headless(Haiku)分類,parse_tags() 正規化後寫檔
- 可續傳:已存在且可解析的 tag 直接跳過
- 並發(ThreadPool),失敗重試,壞檔記錄到 data/tag_failures.log
用法:uv run python scripts/tag_all_cli.py [--workers 8] [--limit N] [--excerpt 10000]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services.llm_models import TASK_TAG, resolve_model  # noqa: E402
from app.services.tagging import TAG_INSTRUCTION, parse_tags  # noqa: E402
from scripts._claude_cli import CliNotFoundError, CliResult, is_retryable, run_claude  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
from app.services.extraction import cache  # noqa: E402

TAGS_DIR = ROOT / "data" / "tags"
FAIL_LOG = ROOT / "data" / "tag_failures.log"
# TAG_MODEL 旋鈕（與 sync_new_reports 的行內標註共用），未設時查 LLM_PROVIDER 的預設表。
MODEL = resolve_model(TASK_TAG)
# 走 DeepSeek 時的輸出上限（第二版計畫 §8；CLI 路徑不讀）；與 sync 的行內標註同值。
MAX_TOKENS = 1024

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


def call_cli(prompt: str, timeout: int = 150, *, file_hash: str | None = None) -> CliResult:
    """呼叫 LLM（`run_claude` 依白名單分派 CLI 或 DeepSeek）。回 (text, None) 或 (None, 失敗原因)。

    實作在 scripts/_claude_cli.py（全批次共用）。標註失敗特別值得說得出原因：
    它會讓該檔在匯入時被記成 `skip_untagged` 而**不入庫**，而排程 log 只印一行
    「本次無新研報入庫」——與「NAS 真的沒有新檔」在畫面上完全一樣。
    """
    return run_claude(
        prompt, MODEL, timeout=timeout, max_tokens=MAX_TOKENS,
        meta={"task": TASK_TAG, "file_hash": file_hash, "report_id": None},
    )


def tag_one(rec: dict, excerpt: int, retries: int = 2) -> str:
    h = rec["file_hash"]
    out_path = TAGS_DIR / f"{h}.json"
    if out_path.exists() and parse_tags(out_path.read_text(encoding="utf-8")):
        return "skip"
    prompt = build_prompt(rec["file_name"], rec.get("text", ""), excerpt)
    last_error = "CLI 無回應"
    for _ in range(retries + 1):
        # CliNotFoundError 刻意不接：環境層級失敗，讓它拋到 main 中止整批
        res = call_cli(prompt, file_hash=h)
        tag = parse_tags(res.text) if res.text else None
        if res.text and tag is None:
            last_error = "回應無法解析為標籤"
        elif res.error:
            last_error = res.error
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
        if res.text is None and not is_retryable(res):
            # HTTP 失敗：傳輸層已重試過，或本來就是決定性的（見 scripts/_claude_cli.py）。
            # 本支只寫 log、不接 DB（審查 L5）：原因已在 last_error 的 API[<kind>] 裡。
            break
    with _lock, open(FAIL_LOG, "a", encoding="utf-8") as f:
        f.write(f"{h}\t{rec['file_name']}\t{last_error}\n")
    return "fail"


def main(workers: int, limit: int | None, excerpt: int) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    recs = []
    n_unreadable = 0
    for r in cache.iter_records():
        if r.get("_unreadable"):
            n_unreadable += 1
            continue
        if r.get("_failed") or r.get("is_admin") or r.get("scanned"):
            continue
        recs.append(r)
    if n_unreadable:
        print(f"warning: {n_unreadable} 個快取檔讀不出來（半寫或非 JSON），已跳過", flush=True)
    if limit:
        recs = recs[:limit]
    total = len(recs)
    print(f"candidates: {total} | workers: {workers} | model: {MODEL}", flush=True)

    global _done, _ok, _fail
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(tag_one, r, excerpt): r for r in recs}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except CliNotFoundError as exc:
                # 環境層級失敗：每一篇都會踩到同一顆地雷。取消還沒開始的工作、
                # 中止並以非零碼收場，而不是把 N 篇全部記成 fail 然後 exit 0。
                print(f"\n中止：{exc}", flush=True)
                print(f"（已完成 {_done}/{total}；ok={_ok} fail={_fail}）", flush=True)
                for pending in futs:
                    pending.cancel()
                raise SystemExit(2) from exc
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
    # 取鎖之前預檢模型與金鑰（缺金鑰是「跑了也白跑」，要在撞鎖 rc=75 之前說出來）。
    require_llm_key({TASK_TAG: MODEL})
    # 全語料標註是最長的一支（數小時），也是最容易把排程的匯入／摘要／摘錄擠掉的一支。
    with claude_cli_lock_or_exit("tag_all_cli"):
        main(args.workers, args.limit, args.excerpt)
