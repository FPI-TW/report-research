#!/usr/bin/env python3
"""受控負載壓測：量「一條問答／一份研報實際吃掉多少硬體」（會消耗 Claude 額度）。

存在理由：`scripts/collect_resource_usage.py` 是被動觀測，而 2026-08-28 查 `qa_log`
近 21 天只有 7 次問答——**被動監控量得到的只有閒置與批次，問答的 CPU 尖峰從來沒有
被觀測過**。拿沒有負載的窗期去定雲端機型的 vCPU，會嚴重低估。這支腳本補的就是那一段：
在取樣器仍在跑的同時，對真實端點打出可控的負載，事後用
`analyze_resource_usage.py --bench <本檔輸出>` 把那段窗期框出來換算單條成本。

**與 `eval/run_ragas.py` 的分工**：那支評的是**品質**，且刻意直接呼叫函式、不經 HTTP；
本檔評的是**資源成本**，所以必須走真實端點——併發閘（`/api/ask` 上限 3）、SSE 串流、
認證中介層都在 HTTP 那一層，繞過去就量不到它們。兩者不合流。

**題目取自 `eval/ragas_questions.json` 的凍結題集**，不自己造題：repo 的規約是量測不要
另建一套（見 CLAUDE.md 的 eval 段）。同一份題集也讓不同時間點的壓測可以互相比較。

**這支腳本會真的花錢**：每題會呼叫 DeepSeek 數次（分類器、主回答、忠實度
抽查、追問建議）。它刻意沒有預設就跑的模式——題數與併發都要顯式指定或用預設的小值，
而且會在開跑前把「將發出幾個請求」印出來。

認證：讀 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`，環境變數沒有就從 repo 根 `.env`
**唯讀**取得。密碼不會出現在任何輸出或錯誤訊息裡。**本檔絕不寫入 `.env`**（那條規約的
血淋淋前例見 `tests/test_env_loading.py` 的 docstring）。

用法：
  python3 scripts/bench_load.py --repeat 1 --concurrency 1        # 單條成本（預設）
  python3 scripts/bench_load.py --repeat 2 --concurrency 3        # 打滿併發閘
  python3 scripts/bench_load.py --dry-run                         # 只印計畫，不送請求

退出碼：0＝全部請求完成；1＝有請求失敗（仍會寫出結果檔）；2＝前置條件不成立
（登入失敗、題集讀不到、服務沒起來）。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "metrics"
QUESTION_SET = REPO_ROOT / "eval" / "ragas_questions.json"


def read_env_file(path: Path) -> dict[str, str]:
    """最小 .env 解析（唯讀）。刻意不做 shell 展開——與 web/env_loader.py 的語意一致。"""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def resolve_credentials() -> tuple[str, str]:
    env = os.environ
    user = env.get("REPORT_MARK_ACCESS_USERNAME")
    pw = env.get("REPORT_MARK_ACCESS_PASSWORD")
    if user and pw:
        return user, pw
    dotenv = read_env_file(REPO_ROOT / ".env")
    user = user or dotenv.get("REPORT_MARK_ACCESS_USERNAME", "")
    pw = pw or dotenv.get("REPORT_MARK_ACCESS_PASSWORD", "")
    return user, pw


def login(base: str, user: str, password: str, timeout: float) -> str:
    """回傳 `tf_session` cookie 值。失敗時拋 RuntimeError（訊息內不含密碼）。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"username": user, "password": password}).encode()
    req = urllib.request.Request(f"{base}/login", data=data, method="POST")
    try:
        opener.open(req, timeout=timeout).read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"登入被拒（HTTP {exc.code}）——請確認帳密與服務狀態") from None
    except OSError as exc:
        raise RuntimeError(f"連不上 {base}：{exc.__class__.__name__}") from None
    for cookie in jar:
        if cookie.name == "tf_session" and cookie.value:
            return cookie.value
    raise RuntimeError("登入沒有取得 tf_session cookie（帳密可能不正確）")


def load_questions(limit: int | None) -> list[str]:
    try:
        payload = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"讀不到題集 {QUESTION_SET}：{exc}") from None
    qs = [q["question"] for q in payload.get("questions", []) if q.get("question")]
    if not qs:
        raise RuntimeError("題集是空的")
    return qs[:limit] if limit else qs


def stream_sse(opener, url: str, body: dict, cookie: str, timeout: float) -> dict:
    """送一個請求並吃完整條 SSE，回傳這一條的時間戳與事件統計。

    **逾時要給得比 llm.py 的上限還寬**：問答預設 120s、開網搜 240s、研報以分鐘計。
    這裡逾時會被記成 error，而那會讓報表誤判成「服務在高負載下失敗」——
    實際上只是壓測工具自己不耐煩。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cookie": f"tf_session={cookie}",
        },
    )
    result: dict = {
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "events": {},
        "queued": False,
        "error": None,
        "answer_chars": 0,
        "n_sources": 0,
    }
    t0 = time.monotonic()
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        result["error"] = f"http_{exc.code}"
        result["total_s"] = round(time.monotonic() - t0, 3)
        return result
    except OSError as exc:
        result["error"] = exc.__class__.__name__
        result["total_s"] = round(time.monotonic() - t0, 3)
        return result

    result["ttfb_s"] = round(time.monotonic() - t0, 3)
    event = None
    try:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith(":"):  # 心跳註解
                continue
            if line.startswith("event: "):
                event = line[7:].strip()
                result["events"][event] = result["events"].get(event, 0) + 1
                key = f"t_{event}_s"
                if key not in result:
                    result[key] = round(time.monotonic() - t0, 3)
                if event == "queued":
                    result["queued"] = True
                continue
            if line.startswith("data: ") and event:
                try:
                    payload = json.loads(line[6:])
                except ValueError:
                    continue
                if event == "token" and isinstance(payload, dict):
                    result["answer_chars"] += len(str(payload.get("text", "")))
                elif event == "sources" and isinstance(payload, (list, dict)):
                    items = payload if isinstance(payload, list) else payload.get("sources", [])
                    result["n_sources"] = len(items or [])
                elif event == "error":
                    result["error"] = str(payload)[:200]
    except OSError as exc:
        result["error"] = f"stream_{exc.__class__.__name__}"
    finally:
        resp.close()
    result["total_s"] = round(time.monotonic() - t0, 3)
    result["ended"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def run_bench(args) -> dict:
    questions = load_questions(args.limit)
    plan = [questions[i % len(questions)] for i in range(args.repeat * len(questions))]

    print(f"題集      {QUESTION_SET.name}（{len(questions)} 題）")
    print(f"端點      /api/{args.endpoint}")
    print(f"計畫      {len(plan)} 個請求 × 併發 {args.concurrency}")
    print(f"逾時      {args.timeout:.0f}s／請求")
    print("提醒      每個請求都會呼叫 DeepSeek 數次，實際扣款（按量計費）。")
    if args.dry_run:
        print("（--dry-run：不送出任何請求）")
        return {"kind": "bench", "dry_run": True, "planned": len(plan)}

    user, password = resolve_credentials()
    if not user or not password:
        raise RuntimeError(
            "找不到 REPORT_MARK_ACCESS_USERNAME／_PASSWORD（環境變數或 repo 根 .env）"
        )
    cookie = login(args.base, user, password, args.timeout)
    del password  # 之後的流程不再需要它

    opener = urllib.request.build_opener()
    url = f"{args.base}/api/{args.endpoint}"
    results: list[dict] = []
    lock = threading.Lock()
    counter = {"i": 0}
    started = datetime.now().astimezone()
    t_start = time.monotonic()

    def worker() -> None:
        while True:
            with lock:
                i = counter["i"]
                if i >= len(plan):
                    return
                counter["i"] = i + 1
            question = plan[i]
            body = {"question": question, "request_id": str(uuid.uuid4())}
            if args.endpoint == "ask":
                body["k"] = 8
            rec = stream_sse(opener, url, body, cookie, args.timeout)
            rec["index"] = i
            rec["question"] = question
            with lock:
                results.append(rec)
                done = len(results)
            status = rec.get("error") or f"{rec['total_s']:.1f}s"
            print(f"  [{done}/{len(plan)}] {question[:24]:24s} {status}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ended = datetime.now().astimezone()
    ok = [r for r in results if not r.get("error")]
    totals = [r["total_s"] for r in ok]
    first_token = [r["t_token_s"] for r in ok if "t_token_s" in r]
    return {
        "kind": "bench",
        "endpoint": args.endpoint,
        "base": args.base,
        "concurrency": args.concurrency,
        "started": started.isoformat(timespec="seconds"),
        "ended": ended.isoformat(timespec="seconds"),
        "wall_s": round(time.monotonic() - t_start, 1),
        "requests": sorted(results, key=lambda r: r["index"]),
        "summary": {
            "n": len(results),
            "ok": len(ok),
            "errors": len(results) - len(ok),
            "queued": sum(1 for r in results if r.get("queued")),
            "total_p50_s": round(percentile(totals, 0.5), 2),
            "total_p95_s": round(percentile(totals, 0.95), 2),
            "total_max_s": round(max(totals), 2) if totals else 0.0,
            "first_token_p50_s": round(percentile(first_token, 0.5), 2),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="受控負載壓測（會消耗 Claude 額度）")
    p.add_argument("--base", default="http://127.0.0.1:8097", help="服務位址")
    p.add_argument("--endpoint", choices=("ask",), default="ask")
    p.add_argument("--repeat", type=int, default=1, help="整份題集重複幾輪（預設 1）")
    p.add_argument("--limit", type=int, default=None, help="只取題集前 N 題")
    p.add_argument("--concurrency", type=int, default=1, help="併發數（/api/ask 的閘上限是 3）")
    p.add_argument("--timeout", type=float, default=600.0, help="單一請求逾時秒數")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="結果檔輸出目錄")
    p.add_argument("--dry-run", action="store_true", help="只印計畫，不送請求")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_bench(args)
    except RuntimeError as exc:
        print(f"壓測前置條件不成立：{exc}", file=sys.stderr)
        return 2
    if result.get("dry_run"):
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"bench-{args.endpoint}-{stamp}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print("")
    print(f"完成      {s['ok']}/{s['n']} 成功｜錯誤 {s['errors']}｜排隊 {s['queued']}")
    print(f"延遲      p50 {s['total_p50_s']}s／p95 {s['total_p95_s']}s／max {s['total_max_s']}s")
    print(f"首 token  p50 {s['first_token_p50_s']}s")
    print(f"結果      {out_path}")
    print(f"下一步    python3 scripts/analyze_resource_usage.py --bench {out_path}")
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
