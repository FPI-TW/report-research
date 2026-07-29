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

用法：uv run python scripts/generate_titles.py [--workers 2] [--limit N] [--excerpt 3000]

注意：每篇都會冷啟動一個 `claude -p` agent；workers 越高、同時冷啟動越多，磁碟
小檔 I/O 越容易被頂滿（與 generate_summaries.py 同一顆地雷，預設同樣壓到 2）。
勿與 make signals / make takeaways 同時跑：多批次搶 claude CLI 會大量誤判失敗。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "title_failures.log"
MODEL = "claude-sonnet-5"
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
    # 模型把檔名當標題回來＝規則 1 沒遵守，視為失敗（含去副檔名的形式）
    stem = file_name.rsplit(".", 1)[0].strip() if file_name else ""
    if title == file_name.strip() or (stem and title == stem):
        return None

    original = _clean_title(obj.get("title_original"))
    if original == title:
        original = None  # 中文報告模型常把原文複製一份，無資訊量

    source = obj.get("title_source")
    source = source if source in TITLE_SOURCES else None
    return TitleResult(title=title, title_original=original, title_source=source)


def build_cli_args(prompt: str) -> list[str]:
    """組 `claude -p` 的 argv（與 generate_summaries 同策略）。

    `--setting-sources ""`＝不載入任何 settings 來源（user/project/local），連帶略過
    全域 hooks/plugins/CLAUDE.md —— 每次冷啟動載入它們正是磁碟小檔 I/O 的主因。
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
) -> None:
    global _done, _ok, _fail
    prompt = build_prompt(file_name, full_text, excerpt)
    result: Optional[TitleResult] = None
    async with sem:
        for _ in range(retries + 1):
            raw = await asyncio.to_thread(call_cli, prompt)
            result = parse_title(raw, file_name) if raw else None
            if result:
                break

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
    """挑待補標題的列：title IS NULL 的研究報告。

    hashes 為 None（預設）＝掃全表所有 NULL（手動補積壓 make titles）。
    hashes 為清單＝只補這批 file_hash（定時匯入只針對本輪新研報，避免掃積壓）；
    空清單代表本輪無新研報，直接回空、不查 DB。

    排序刻意 report_date DESC：全語料一萬多篇跑不完時，先讓最近的報告有標題。
    """
    sql = (
        "SELECT id::text, file_name, full_text "
        "FROM research.research_report "
        "WHERE title IS NULL AND full_text IS NOT NULL AND is_research IS NOT FALSE "
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
        print("nothing to do（皆已有標題）", flush=True)
        return
    sem = asyncio.Semaphore(workers)
    await asyncio.gather(
        *(title_one(sem, rid, fn, ft, excerpt, total) for rid, fn, ft in cands)
    )
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
    args = ap.parse_args()
    asyncio.run(main(args.workers, args.limit, args.excerpt, args.hashes_file))
