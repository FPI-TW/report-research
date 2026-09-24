"""為研報產生顯示標題 → research_report.title / title_original / title_source

檔名多半是券商流水號（624726992507895929_260728_gs_umt.pdf），對讀者毫無意義；
報告的真正標題印在首頁內文裡。本批次把它抽出來，供前端取代檔名顯示。

三種情形，一次呼叫涵蓋（title_source 記錄走了哪一條）：
- extracted：內文標題本來就是中文 → 原樣保留
- translated：內文標題是英文/其他語言 → 譯為繁體中文，原文存 title_original
- generated：內文根本沒有標題（掃描件、純表格日報）→ 依重點自擬一句話標題

- 來源：DB 既有 full_text 的**開頭**（標題在首頁），故摘錄遠比 summaries 短
- 每篇用 `claude -p`(Sonnet) headless 產出 JSON，parse_title() 解析
- 冪等可續傳：只挑 title IS NULL 者；重跑天然跳過已補的
- 失敗（含內文抽字損毀而無法辨識）記 data/title_failures.log，title 維持 NULL
  → 前端回退檔名，不會顯示錯的標題
- 回應解析不出標題的研報記入 research.llm_task_failure，連續 3 輪後不再重打
  （規則見 app/services/llm_failures.py；`--retry-blocked` 手動解除）

用法：uv run python scripts/generate_titles.py [--workers 2] [--limit N] [--excerpt 3000]
      [--hashes-file F] [--exclude-hashes-file F] [--retry-blocked]

注意：每篇都會冷啟動一個 `claude -p` agent；workers 越高、同時冷啟動越多，磁碟
小檔 I/O 越容易被頂滿（與 generate_summaries.py 同一顆地雷，預設同樣壓到 2）。
與其他 claude 批次的互斥由 scripts/_claude_lock.py 的 flock 強制（撞鎖以 rc=75
結束，不是這支壞掉）——不再只靠這行註解。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from sqlalchemy import text  # noqa: E402

from app.services import llm_failures  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.llm_models import TASK_TITLE, resolve_model  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402
from scripts._claude_cli import (  # noqa: E402
    CliNotFoundError,
    CliResult,
    run_claude,
)
from scripts._claude_cli import build_cli_args as _build_cli_args  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "title_failures.log"
# TITLE_MODEL 旋鈕，未設時查 LLM_PROVIDER 的預設表（app/services/llm_models.py）。
MODEL = resolve_model(TASK_TITLE)
# 走 DeepSeek 時的輸出上限（第二版計畫 §8；CLI 路徑不讀）。一句標題加原文，512 綽綽有餘。
MAX_TOKENS = 512
MAX_TITLE_CHARS = 80  # 安全上限：標題不是摘要，超長多半代表模型把整段抓進來
TITLE_SOURCES = ("extracted", "translated", "generated")

PROMPT_INSTRUCTION = (
    "你是金融研報的編目助理。請閱讀以下研報開頭內文，找出這份報告的標題。要求：\n"
    "1. 標題取自報告首頁最顯著的主標（可含副標）。**不要**用檔名、券商名、分析師"
    "姓名、聯絡方式、頁首頁尾、免責聲明或欄位表頭當標題。\n"
    "2. 標題一律輸出繁體中文；若原標題是英文或其他語言，翻譯成通順的繁體中文，"
    "並把原文放進 title_original。公司名、股票代碼、產品名等專有名詞保留原樣"
    "（例如 ABF、HBM、TSMC 可保留，但已有通用中文名者用中文）。\n"
    "3. 若原標題已是中文，title 直接用原標題（轉繁體），title_original 給 null。\n"
    "3-1. 若這是**單一公司**的個股報告、而標題本身沒有公司名，請在標題前補上公司名"
    "（例：「台達電：近期不確定性已反映在股價」）——標題會出現在清單裡，"
    "看不出寫的是哪一家就沒有用。多標的、產業或總體報告則不要硬加。\n"
    "4. 若內文確實沒有標題（例如純表格日報、掃描件），依內容重點自擬一句話標題，"
    "點出主題與標的，並把 title_source 設為 \"generated\"。\n"
    "5. 標題精簡（不超過 40 字），不要句號結尾、不要 markdown、不要引號包裹、"
    "不要加「研究報告」之類的贅詞。\n"
    "6. 若內文抽字損毀、亂碼或完全無法判讀，title 給 null（不要猜）。\n"
    "7. 只輸出單一 JSON 物件："
    "{\"title\": \"……\", \"title_original\": \"……或 null\", "
    "\"title_source\": \"extracted|translated|generated\"}，不要輸出其他文字。"
)

_done = 0
_ok = 0
_fail = 0


@dataclass
class TitleResult:
    """一篇報告的標題產出。title 必為非空字串；另兩欄可為 None。"""

    title: str
    title_original: Optional[str]
    title_source: Optional[str]


def build_prompt(file_name: str, full_text: str, excerpt: int) -> str:
    """組提示詞。摘錄取**清理後**的開頭：抽字留下的 CJK 間空白（「台 積 電」）
    會讓模型讀錯詞，clean_extracted 正是移除它的單一事實來源。
    """
    head = clean_extracted((full_text or "")[: excerpt * 2])[:excerpt]
    return (
        f"{PROMPT_INSTRUCTION}\n\n"
        f"檔名（僅供參考，通常是流水號、不可當標題）：{file_name}\n"
        f"報告開頭內文（前 {excerpt} 字）：\n{head}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    )


def _clean_title(value: object) -> Optional[str]:
    """標題字串清洗：收斂空白、去外層引號/markdown 記號、截長。非字串或空 → None。"""
    if not isinstance(value, str):
        return None
    s = " ".join(value.split()).strip()
    s = s.lstrip("#").strip()
    # 首尾成對引號（模型偶爾會包起來）；只剝一層，內文中的引號不動
    for lq, rq in (("「", "」"), ("《", "》"), ('"', '"'), ("'", "'"), ('"', '"')):
        if len(s) >= 2 and s.startswith(lq) and s.endswith(rq):
            s = s[1:-1].strip()
            break
    if not s:
        return None
    return s[:MAX_TITLE_CHARS]


def parse_title(raw: str, file_name: str = "") -> Optional[TitleResult]:
    """容錯解析 Claude 回應 → TitleResult；無法採信一律 None（該篇維持 NULL）。

    與 generate_summaries.parse_summary 的關鍵差異：**沒有純文字 fallback**。
    標題是要放到卡片與頁首的短字串，模型若沒照格式輸出，多半是把整段內文吐回來，
    寧可留 NULL 讓前端回退檔名，也不要顯示一段錯的標題。
    """
    if not raw:
        return None
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    title = _clean_title(obj.get("title"))
    if not title:
        return None
    # 規則 2「一律輸出繁體中文」是 prompt 的機率性保證，模型會偶發整句輸出簡體
    # （2026-07-31 的台股頭條即是）。這裡確定性收尾；未達門檻的專有名詞不動。
    title = to_traditional(title)
    # 模型把檔名當標題回來＝規則 1 沒遵守，視為失敗（含去副檔名的形式）
    stem = file_name.rsplit(".", 1)[0].strip() if file_name else ""
    if title == file_name.strip() or (stem and title == stem):
        return None

    # title_original 是**原文**（英文或其他語言），刻意不轉——它存在的意義就是保留原樣。
    original = _clean_title(obj.get("title_original"))
    if original is not None and to_traditional(original) == title:
        original = None  # 中文報告模型常把原文複製一份，無資訊量（含簡體副本）

    source = obj.get("title_source")
    source = source if source in TITLE_SOURCES else None
    return TitleResult(title=title, title_original=original, title_source=source)


def build_cli_args(prompt: str) -> list[str]:
    """組 `claude -p` 的 argv（本腳本固定用 MODEL；實作見 scripts/_claude_cli.py）。"""
    return _build_cli_args(prompt, MODEL)


def call_cli(
    prompt: str, timeout: int = 180, *, file_hash: Optional[str] = None, report_id: Optional[str] = None
) -> CliResult:
    """呼叫 LLM（`run_claude` 依白名單分派 CLI 或 DeepSeek）。回 (text, None) 或 (None, 失敗原因)。

    原本是 `except (subprocess.TimeoutExpired, Exception): return None` —— 那個
    tuple 的第二項讓第一項完全沒有意義，所有失敗一律回 None，而 `title_failures.log`
    連原因欄都沒有，只記 id 與檔名。2026-08 連續四天 titled_ok=0 fail=60 時，
    那個檔對「為什麼」一個字都說不出來。
    """
    return run_claude(
        prompt, MODEL, timeout=timeout, max_tokens=MAX_TOKENS,
        meta={"task": TASK_TITLE, "file_hash": file_hash, "report_id": report_id},
    )


UPDATE_SQL = (
    "UPDATE research.research_report "
    "SET title = :title, title_original = :original, title_source = :source "
    "WHERE id = :id"
)


async def title_one(
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
    result: Optional[TitleResult] = None
    # 保留最後一次的失敗原因：三次都沒回應時，log 要寫得出是逾時、非零退出碼還是
    # 「回了但解析不採信」——後者是資料問題，前者是環境問題，處置完全不同。
    last_error = "CLI 無回應"
    # 本輪有沒有任何一次「回了但不能用」。只有這種才記入跳過名單：逾時、非零退出
    # 是環境問題，記了會讓一次停機把整批研報打入跳過名單。
    content_failed = False
    async with sem:
        for _ in range(retries + 1):
            # CliNotFoundError 刻意不接：那是環境壞了（每篇都會踩），
            # 讓它一路拋到 main 中止整批。
            res = await asyncio.to_thread(call_cli, prompt, file_hash=file_hash, report_id=rid)
            if res.text:
                result = parse_title(res.text, file_name)
                if result:
                    break
                last_error = "回應無法解析為可採信的標題"
                content_failed = True
            elif res.error:
                last_error = res.error

    if result:
        async with SessionFactory() as session:
            await session.execute(
                text(UPDATE_SQL),
                {
                    "title": result.title,
                    "original": result.title_original,
                    "source": result.title_source,
                    "id": rid,
                },
            )
            await session.commit()
        if recorder:
            await recorder.clear(file_hash)
        _ok += 1
    else:
        if recorder and content_failed:
            await recorder.record(file_hash, llm_failures.UNPARSEABLE)
        # 第三欄是 2026-08-13 補的：先前只記 id 與檔名，於是連續四天 fail=60
        # 時這個檔對「為什麼」一個字都說不出來。
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


def build_candidates_sql(by_hashes: bool, skip_blocked: bool, limit: bool, exclude: bool = False) -> str:
    """待補標題的查詢（純字串，供測試斷言）。skip_blocked 時排除跳過名單上的研報；
    exclude 時排除 `:exclude` 列出的 file_hash（空清單時呼叫端不開它）。"""
    sql = (
        "SELECT r.id::text, r.file_name, r.full_text, r.file_hash "
        "FROM research.research_report r "
        "WHERE r.title IS NULL AND r.full_text IS NOT NULL AND r.is_research IS NOT FALSE "
    )
    if by_hashes:
        sql += "AND r.file_hash = ANY(:hashes) "
    if exclude:
        sql += "AND NOT (r.file_hash = ANY(:exclude)) "
    if skip_blocked:
        sql += "AND " + llm_failures.skip_clause_sql("r") + " "
    sql += "ORDER BY r.report_date DESC NULLS LAST, r.file_name"
    if limit:
        sql += " LIMIT :limit"
    return sql


async def fetch_candidates(
    limit: Optional[int],
    hashes: Optional[list[str]] = None,
    skip_blocked: bool = False,
    exclude_hashes: Optional[list[str]] = None,
) -> list[tuple[str, str, str, str]]:
    """挑待補標題的列：title IS NULL 的研究報告。

    hashes 為 None（預設）＝掃全表所有 NULL（手動補積壓 make titles）。
    hashes 為清單＝只補這批 file_hash（定時匯入只針對本輪新研報，避免掃積壓）；
    空清單代表本輪無新研報，直接回空、不查 DB。
    skip_blocked＝排除 research.llm_task_failure 判定該跳過的研報（同 MODEL）。
    exclude_hashes＝排除這批 file_hash（sync 積壓段排掉本輪 4b 剛打過的新研報）；
    None 或空清單不加條件。

    排序刻意 report_date DESC：全語料一萬多篇跑不完時，先讓最近的報告有標題。
    """
    params: dict = {}
    if hashes is not None:
        if not hashes:
            return []
        params["hashes"] = hashes
    if skip_blocked:
        params.update(llm_failures.skip_params(llm_failures.TASK_TITLE, MODEL))
    if exclude_hashes:
        params["exclude"] = exclude_hashes
    if limit:
        params["limit"] = limit
    sql = build_candidates_sql(hashes is not None, skip_blocked, bool(limit), bool(exclude_hashes))
    async with SessionFactory() as session:
        rows = await session.execute(text(sql), params)
        return [(r[0], r[1], r[2], r[3]) for r in rows.all()]


async def main(
    workers: int,
    limit: Optional[int],
    excerpt: int,
    hashes_file: Optional[str] = None,
    retry_blocked: bool = False,
    exclude_hashes_file: Optional[str] = None,
) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    hashes = read_hashes_file(hashes_file) if hashes_file else None
    exclude = read_hashes_file(exclude_hashes_file) if exclude_hashes_file else None
    scope = f"本輪 {len(hashes)} 篇" if hashes is not None else "全表 NULL"
    if exclude:
        scope += f"（排除 {len(exclude)} 篇）"
    recorder = await llm_failures.open_recorder(llm_failures.TASK_TITLE, MODEL, SessionFactory)
    cands = await fetch_candidates(
        limit, hashes, skip_blocked=recorder is not None and not retry_blocked, exclude_hashes=exclude
    )
    total = len(cands)
    print(
        f"candidates: {total} | scope: {scope} | workers: {workers} | model: {MODEL}",
        flush=True,
    )
    if not total:
        print("nothing to do（皆已有標題）", flush=True)
        return
    sem = asyncio.Semaphore(workers)
    try:
        await asyncio.gather(
            *(
                title_one(sem, rid, fn, ft, excerpt, total, file_hash=fh, recorder=recorder)
                for rid, fn, ft, fh in cands
            )
        )
    except CliNotFoundError as exc:
        # 環境層級失敗：剩下的每一篇都會踩到同一顆地雷 → 中止並以非零碼收場，
        # 而不是跑完 N 次註定失敗的呼叫、印 titled_ok=0、然後 exit 0。
        print(f"\n中止：{exc}", flush=True)
        print(f"（已完成 {_done}/{total}；ok={_ok} fail={_fail}）", flush=True)
        raise SystemExit(2) from exc
    print(f"\ndone. titled_ok={_ok} fail={_fail}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--excerpt", type=int, default=3000)
    ap.add_argument(
        "--hashes-file",
        default=None,
        help="只補此檔列出的 file_hash（每行一個）；不給＝補全表所有 title IS NULL",
    )
    ap.add_argument(
        "--exclude-hashes-file",
        default=None,
        help=(
            "排除此檔列出的 file_hash（每行一個）。sync 的標題積壓段傳本輪 .sync_last_hashes："
            "同一輪 4b 剛打過的新研報不再打第二次，失敗也不會一輪記兩次"
        ),
    )
    ap.add_argument(
        "--retry-blocked",
        action="store_true",
        help="不套跳過名單（research.llm_task_failure），連已判定跳過的研報也重打",
    )
    args = ap.parse_args()
    # 取鎖之前預檢模型與金鑰（缺金鑰是「跑了也白跑」，要在撞鎖 rc=75 之前說出來）。
    require_llm_key({TASK_TITLE: MODEL})
    with claude_cli_lock_or_exit("generate_titles"):
        asyncio.run(
            main(
                args.workers, args.limit, args.excerpt, args.hashes_file, args.retry_blocked,
                exclude_hashes_file=args.exclude_hashes_file,
            )
        )
