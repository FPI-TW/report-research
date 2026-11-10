"""導入驗收的題集草擬工具：分層抽樣 → LLM 草擬候選題 → 研究員審 → 凍結（`docs/WORKFLOW.md`「導入驗收」）。

子命令：

- `draft --profile <名稱> --out <待審檔>`：從目前部署的語料（`PROFILE_DIR` 必須就是這個 profile）依市場、報告類型、
  季度分層抽樣，單篇研報草擬個股／產業／總經題，同市場同季、不同券商的兩篇研報草擬跨篇比較題；每題附參考答案要點
  與支撐研報（`file_hash`）。**會呼叫 LLM**（模型＝離線評測的 `EVAL_JUDGE_MODEL`，未設時查預設表），取 LLM 批次鎖。
  待審檔已存在就拒寫（裡面可能已有研究員的審核），要覆寫加 `--force`。
- `status --draft <待審檔>`：審核進度、核准題的類型涵蓋與還缺什麼（零 LLM）。
- `freeze --profile <名稱> --draft <待審檔>`：全部審完且核准題數 ≥30、每類涵蓋達標時，寫出
  `profiles/<名稱>/eval/questions.json` 與凍結清單 `questions.manifest.json`；否則逐條說明缺什麼、不寫檔（零 LLM）。
  已有凍結題集時拒絕覆寫，換題要加 `--replace`（舊基準線隨之不可比）。

審核規則與檔案格式見 `eval/question_draft.py`，題集政策與指紋核對見 `eval/profile_eval.py`。

刻意的取捨：
- 抽樣、配對、filters、題號與指紋全由 Python 決定（`--seed` 固定就可重現）；LLM 只寫題目文字、判斷單篇題屬於
  個股／產業／總經，以及寫參考答案要點。
- 只用摘要（`research_report.summary`）當 LLM 的輸入：題目要的是「這份語料答得出來的問題」，全文只會讓題目
  變成只有那一篇答得了的細節題。沒有摘要的研報不抽。
- 抽樣只看使用者看得到、可檢索的研報（`visible_report_sql`＋`doc_type_enabled_sql(…, "search")`）：題目的
  答案必須在問答的檢索範圍裡。
- 不寫跳過名單（`llm_task_failure`）：這是導入時跑一次的工具，失敗的候選就少一題，多抽幾篇即可。

退出碼：0 成功／2 前檢、輸入或凍結條件不合格（未寫任何檔）／75 撞 LLM 批次鎖。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services.llm_models import TASK_EVAL_JUDGE, resolve_model  # noqa: E402
from eval import profile_eval as pe  # noqa: E402
from eval import question_draft as qd  # noqa: E402
from eval.question_contract import MARKETS  # noqa: E402
from scripts._claude_cli import CliResult, is_retryable, run_claude  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

# 草擬用離線評測那顆模型（EVAL_JUDGE_MODEL 旋鈕，os.getenv 照 eval/judge.py 的慣例留在呼叫端）。
MODEL = resolve_model(TASK_EVAL_JUDGE, override=os.getenv("EVAL_JUDGE_MODEL"))
# 用量紀錄與 log 的任務名（data/llm_usage.jsonl 的費用歸因）。
TASK_EVAL_DRAFT = "eval_draft"
# 一題的 JSON（題目＋3–5 條要點），2048 綽綽有餘。
MAX_TOKENS = 2048
TIMEOUT = 180
RETRIES = 1
DEFAULT_TARGET = 48  # 候選題數：比凍結門檻 30 多一半，留給研究員剔除
CROSS_SHARE = 4  # 候選裡約 1/4 是跨篇比較題
SUMMARY_MAX_CHARS = 1200
QUESTION_MAX_CHARS = 80
POINT_MAX_CHARS = 200
MAX_POINTS = 6
EXIT_INPUT = 2

SINGLE_CATEGORIES = (pe.CATEGORY_STOCK, pe.CATEGORY_INDUSTRY, pe.CATEGORY_MACRO)

PROMPT_RULES = (
    "要求：\n"
    "1. 題目用繁體中文、6 到 60 字，寫成使用者會對整個研報庫提出的問題（例如「台積電先進封裝的產能規劃如何？」），"
    "不要提到「這份報告」「本文」、檔名、券商名或分析師。\n"
    "2. 題目必須能用上面的研報內容回答，但不要問只有這一份才有的細節數字。\n"
    "3. reference_points 列 3 到 5 條參考答案要點（繁體中文，每條一句），內容只能來自上面的研報。\n"
    "4. 只輸出單一 JSON 物件，不要輸出其他文字。\n"
)


def _report_block(row: dict, n: int | None = None) -> str:
    head = f"研報{n}" if n is not None else "研報"
    meta = "、".join(
        f"{k}：{row[c]}" for k, c in (("市場", "market"), ("類型", "report_type"), ("日期", "report_date"),
                                      ("公司", "company_name"), ("代碼", "stock_code")) if row.get(c)
    )
    summary = (row.get("summary") or "").strip()[:SUMMARY_MAX_CHARS]
    return f"【{head}】{row.get('label') or ''}\n{meta}\n摘要：{summary}\n"


def build_single_prompt(row: dict) -> str:
    return (
        "你是券商研報平台的評測題目設計者。請依下面一份研報出一題評測題。\n\n"
        + _report_block(row)
        + "\n先判斷題目類型 category：stock（個股：問單一公司）、industry（產業：問一個產業、題材或供應鏈）、"
        "macro（總經：問總體經濟、利率、匯率、大盤或資產配置）。\n"
        + PROMPT_RULES
        + '格式：{"category": "stock", "question": "……", "reference_points": ["……", "……", "……"]}'
    )


def build_cross_prompt(a: dict, b: dict) -> str:
    return (
        "你是券商研報平台的評測題目設計者。請依下面兩份研報出一題「跨篇比較」評測題："
        "題目要問兩份研報都談到的主題，回答需要比較或綜合兩份的觀點（例如看法異同、預估差異）。\n\n"
        + _report_block(a, 1) + "\n" + _report_block(b, 2)
        + "\n" + PROMPT_RULES
        + '格式：{"question": "……", "reference_points": ["……", "……", "……"]}'
    )


_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def parse_candidate(raw: str | None, *, cross: bool) -> dict | None:
    """LLM 回應 → {"category", "question", "reference_points"}；不採信就回 None。"""
    if not raw:
        return None
    m = _JSON_OBJ.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    category = pe.CATEGORY_CROSS if cross else obj.get("category")
    if category not in (pe.CATEGORIES if cross else SINGLE_CATEGORIES):
        return None
    question = obj.get("question")
    points = obj.get("reference_points")
    if not isinstance(question, str) or not isinstance(points, list):
        return None
    question = to_traditional(question.strip())
    if not 6 <= len(question) <= QUESTION_MAX_CHARS or question.endswith(("'", '"', "＇", "＂")):
        return None
    points = [to_traditional(p.strip()) for p in points if isinstance(p, str) and p.strip()]
    if not 1 <= len(points) <= MAX_POINTS or any(len(p) > POINT_MAX_CHARS for p in points):
        return None
    return {"category": category, "question": question, "reference_points": points}


def to_traditional(text: str) -> str:
    # 延後 import：opencc 字典載入要時間，status／freeze 用不到。
    from app.services.zh_hant import to_traditional as _convert

    return _convert(text)


# ── 抽樣（決定性）──────────────────────────────────────────────────────────


def _quarter(value: Any) -> str:
    if isinstance(value, date):
        return f"{value.year}Q{(value.month - 1) // 3 + 1}"
    return "unknown"


def stratum(row: dict) -> tuple[str, str, str]:
    return (row.get("market") or "unknown", row.get("report_type") or "unknown", _quarter(row.get("report_date")))


def stratified_sample(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    """依（市場、報告類型、季度）分層，層與層之間輪流取，直到 n 篇。同一個 seed 結果相同。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["file_hash"]):
        groups[stratum(row)].append(row)
    keys = sorted(groups)
    rng.shuffle(keys)
    for k in keys:
        rng.shuffle(groups[k])
    out: list[dict] = []
    while len(out) < n and any(groups[k] for k in keys):
        for k in keys:
            if groups[k] and len(out) < n:
                out.append(groups[k].pop())
    return out


def pick_pairs(rows: list[dict], n: int, rng: random.Random) -> list[tuple[dict, dict]]:
    """跨篇比較的研報對：同市場、同季、不同券商（缺券商時視為不同）；各組輪流取。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["file_hash"]):
        groups[(row.get("market") or "unknown", _quarter(row.get("report_date")))].append(row)
    keys = sorted(k for k, g in groups.items() if len(g) >= 2)
    rng.shuffle(keys)
    for k in keys:
        rng.shuffle(groups[k])
    pairs: list[tuple[dict, dict]] = []
    progressed = True
    while len(pairs) < n and progressed:
        progressed = False
        for k in keys:
            g = groups[k]
            if len(pairs) >= n or len(g) < 2:
                continue
            a = g.pop()
            j = next((i for i, b in enumerate(g) if not a.get("source") or b.get("source") != a.get("source")), None)
            if j is None:
                continue
            pairs.append((a, g.pop(j)))
            progressed = True
    return pairs


def default_filters(category: str, rows: list[dict]) -> dict:
    """個股題帶市場過濾（與現行題集的寫法一致）；其餘不帶，讓檢索看整個語料。研究員可在審核時改。"""
    market = rows[0].get("market")
    if category == pe.CATEGORY_STOCK and market in MARKETS:
        return {"market": market}
    return {}


def support_of(row: dict) -> dict:
    out = {"file_hash": row["file_hash"], "label": row.get("label")}
    for k in ("market", "report_type", "source"):
        if row.get(k):
            out[k] = row[k]
    if isinstance(row.get("report_date"), date):
        out["report_date"] = row["report_date"].isoformat()
    return out


# ── LLM ───────────────────────────────────────────────────────────────────


def call_llm(prompt: str, *, file_hash: str, report_id: str) -> CliResult:
    return run_claude(
        prompt, MODEL, timeout=TIMEOUT, max_tokens=MAX_TOKENS,
        meta={"task": TASK_EVAL_DRAFT, "file_hash": file_hash, "report_id": report_id},
    )


async def draft_one(sem: asyncio.Semaphore, rows: list[dict], *, call=None) -> dict | None:
    """一份（或一對）研報 → 一題候選；兩次都不採信就回 None（少一題，不中止）。"""
    call = call or call_llm
    cross = len(rows) == 2
    prompt = build_cross_prompt(*rows) if cross else build_single_prompt(rows[0])
    async with sem:
        for _ in range(RETRIES + 1):
            res = await asyncio.to_thread(call, prompt, file_hash=rows[0]["file_hash"], report_id=str(rows[0]["id"]))
            parsed = parse_candidate(res.text, cross=cross)
            if parsed:
                return parsed
            if res.text is None and not is_retryable(res):
                break
    print(f"候選不採信，略過：{rows[0]['file_hash'][:12]}" + ("（跨篇）" if cross else ""), file=sys.stderr)
    return None


SAMPLE_SQL_TEMPLATE = """
SELECT r.id, r.file_hash, COALESCE(NULLIF(r.title, ''), r.file_name) AS label, r.market, r.report_type,
       r.report_date, r.source, r.company_name, r.stock_code, r.summary
FROM research.research_report r
WHERE r.summary IS NOT NULL AND btrim(r.summary) <> ''
  AND {visible} AND {doc_type}
"""


def sample_sql() -> str:
    # 片段在呼叫時依 profile 渲染（AGENTS.md「資料層陷阱」：不做成 import 期定值的常數）。
    from app.services.doc_types import doc_type_enabled_sql
    from app.services.visibility import visible_report_sql

    return SAMPLE_SQL_TEMPLATE.format(visible=visible_report_sql("r"), doc_type=doc_type_enabled_sql("r", "search"))


async def load_rows() -> list[dict]:
    from sqlalchemy import text

    from app.services.db import SessionFactory

    async with SessionFactory() as session:
        result = await session.execute(text(sample_sql()))
        return [dict(m) for m in result.mappings()]


async def build_draft(rows: list[dict], *, target: int, seed: int, workers: int, profile: str,
                      now: datetime, call=None) -> dict:
    rng = random.Random(seed)
    n_cross = target // CROSS_SHARE
    singles = stratified_sample(rows, target - n_cross, rng)
    chosen = {r["file_hash"] for r in singles}
    pairs = pick_pairs([r for r in rows if r["file_hash"] not in chosen], n_cross, rng)
    jobs = [[r] for r in singles] + [list(p) for p in pairs]
    sem = asyncio.Semaphore(max(1, workers))
    results = await asyncio.gather(*(draft_one(sem, job, call=call) for job in jobs))
    items = []
    for job, cand in zip(jobs, results):
        if cand is None:
            continue
        items.append(qd.new_item(
            f"d{len(items) + 1:03d}", category=cand["category"], question=cand["question"],
            filters=default_filters(cand["category"], job), reference_points=cand["reference_points"],
            supporting_reports=[support_of(r) for r in job],
        ))
    sample = {
        "seed": seed, "target": target, "corpus_reports": len(rows), "single_reports": len(singles),
        "cross_pairs": len(pairs), "strata": len({stratum(r) for r in singles}), "candidates": len(items),
    }
    return qd.new_draft(profile=profile, generated_at=now.isoformat(), model=MODEL, sample=sample, items=items)


# ── 檔案 ──────────────────────────────────────────────────────────────────


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cmd_status(draft_path: Path) -> int:
    try:
        items = qd.validate_draft(json.loads(draft_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"讀不了待審檔：{exc}", file=sys.stderr)
        return EXIT_INPUT
    except qd.FreezeRefused as exc:
        print("待審檔格式不對：\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return EXIT_INPUT
    s = qd.summarize(items)
    print(f"候選 {s['total']} 題：" + "、".join(f"{k} {v}" for k, v in s["by_status"].items()))
    print("核准題的類型涵蓋：" + "、".join(
        f"{pe.CATEGORY_LABELS[c]} {s['accepted_coverage'].get(c, 0)}" for c in pe.CATEGORIES))
    if s["problems"]:
        print("還不能凍結：\n" + "\n".join(f"  - {p}" for p in s["problems"]))
    else:
        print("可以凍結（freeze）")
    return 0


def cmd_freeze(profile_spec: str, draft_path: Path, *, replace: bool, now: datetime) -> int:
    try:
        profile_dir = pe.resolve_profile_arg(profile_spec)
        config = pe.load_eval_config(profile_dir)
        raw = draft_path.read_bytes()
        draft = json.loads(raw)
    except (OSError, json.JSONDecodeError, pe.EvalProfileError) as exc:
        print(f"凍結前檢失敗：{exc}", file=sys.stderr)
        return EXIT_INPUT
    if config.legacy_repo_path is not None:
        print(f"{profile_dir.name} 沿用 repo 內的凍結題集（{config.legacy_repo_path}），不以草擬工具產生",
              file=sys.stderr)
        return EXIT_INPUT
    out_dir = profile_dir / pe.EVAL_DIR
    questions_path, manifest_path = out_dir / pe.QUESTIONS_FILE, out_dir / pe.MANIFEST_FILE
    if (questions_path.exists() or manifest_path.exists()) and not replace:
        print(f"{questions_path} 已凍結：換題會讓既有基準線不可比，確定要換就加 --replace", file=sys.stderr)
        return EXIT_INPUT
    try:
        data, manifest = qd.freeze(draft, draft_sha256=pe.sha256_bytes(raw), profile=profile_dir.name, frozen_at=now)
    except qd.FreezeRefused as exc:
        print("不能凍結：\n" + "\n".join(f"  - {p}" for p in exc.problems), file=sys.stderr)
        return EXIT_INPUT
    write_atomic(questions_path, data)
    write_atomic(manifest_path, qd.dumps(manifest))
    pe.load_profile_dataset(profile_dir)  # 寫完立刻以讀取端的規則核對一次
    print(f"已凍結 {manifest['count']} 題 → {pe.display_path(questions_path)}（sha256 {manifest['questions_sha256']}）")
    print("類型涵蓋：" + "、".join(f"{pe.CATEGORY_LABELS[c]} {manifest['coverage'][c]}" for c in pe.CATEGORIES))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="導入驗收的題集草擬：draft → 研究員審 → freeze")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="分層抽樣並以 LLM 草擬候選題（會呼叫 LLM、取批次鎖）")
    d.add_argument("--profile", required=True, help="profile 名稱或目錄；必須就是目前部署的 PROFILE_DIR")
    d.add_argument("--out", type=Path, required=True, help="待審檔路徑（JSON）")
    d.add_argument("--target", type=int, default=DEFAULT_TARGET, help=f"候選題數（預設 {DEFAULT_TARGET}）")
    d.add_argument("--seed", type=int, default=0, help="抽樣種子（相同語料＋相同種子＝相同抽樣）")
    d.add_argument("--workers", type=int, default=4, help="同時的 LLM 請求數")
    d.add_argument("--force", action="store_true", help="覆寫既有的待審檔（會丟掉其中的審核）")
    s = sub.add_parser("status", help="審核進度與還缺什麼（零 LLM）")
    s.add_argument("--draft", type=Path, required=True)
    f = sub.add_parser("freeze", help="凍結核准題（零 LLM）")
    f.add_argument("--profile", required=True, help="profile 名稱或目錄")
    f.add_argument("--draft", type=Path, required=True)
    f.add_argument("--replace", action="store_true", help="取代既有的凍結題集（舊基準線隨之不可比）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.cmd == "status":
        return cmd_status(args.draft)
    if args.cmd == "freeze":
        return cmd_freeze(args.profile, args.draft, replace=args.replace, now=now)
    try:
        profile_dir = pe.resolve_profile_arg(args.profile)
    except pe.EvalProfileError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INPUT
    if profile_dir != pe.active_profile_dir().resolve():
        print(f"draft 要在該 profile 的部署環境執行：--profile 是 {profile_dir}，"
              f"{pe.PROFILE_DIR_ENV} 是 {pe.active_profile_dir()}", file=sys.stderr)
        return EXIT_INPUT
    if args.out.exists() and not args.force:
        print(f"{args.out} 已存在（可能已有審核紀錄）；確定要重新草擬就加 --force", file=sys.stderr)
        return EXIT_INPUT
    if args.target < pe.MIN_QUESTIONS:
        print(f"--target 至少要 {pe.MIN_QUESTIONS}（凍結門檻）", file=sys.stderr)
        return EXIT_INPUT
    # 取鎖之前預檢模型與金鑰（缺金鑰是「跑了也白跑」，要在撞鎖 rc=75 之前說出來）。
    require_llm_key({TASK_EVAL_JUDGE: MODEL})
    with claude_cli_lock_or_exit("draft_eval_questions"):
        rows = asyncio.run(load_rows())
        if len(rows) < args.target:
            print(f"有摘要且可檢索的研報只有 {len(rows)} 份，不夠抽 {args.target} 題的候選", file=sys.stderr)
            return EXIT_INPUT
        draft = asyncio.run(build_draft(rows, target=args.target, seed=args.seed, workers=args.workers,
                                        profile=profile_dir.name, now=now))
    write_atomic(args.out, qd.dumps(draft))
    print(f"已寫出 {len(draft['items'])} 題候選 → {args.out}（抽樣：{draft['sample']}）")
    print("下一步：研究員逐題標 review.status，再以 status 檢查、freeze 凍結")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
