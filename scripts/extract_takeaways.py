"""重點摘錄擷取批次（近 N 天先行、冪等可續傳）→ research.report_takeaway

閱讀頁 `/app/report/:hash` 左欄「重點摘錄」的資料來源：每篇 3-5 條論點，每條帶一句
原文逐字引文。**摘錄一律離線批次產生、落 DB，閱讀頁讀取時零 LLM**
（對齊 extract_signals.py 之於觀點雷達的分工）。

**錨定（quote_start/quote_end）目前沒有前端讀取路徑**——「點條目跳到原文並高亮」已於
2026-08-03 隨閱讀頁文字檢視一併移除。仍照樣算、照樣寫入，理由有二：db_audit 的
「摘錄與全文是否同源」稽核靠它，而事後補算的代價是對 674+ 篇重跑 Sonnet 並搶
_claude_lock 的 flock。**不要因為「沒有消費端」就把第 4 步拿掉。**

流程（對齊 scripts/extract_signals.py 的 asyncio + Semaphore + claude CLI 慣例）：
1. 撈工作集：近 --since-days 天、有全文的研究報告。
2. checkpoint-resume：該報告已有列、且 extraction_version 與 text_sha256 皆相符、
   且狀態 ∈ (valid, partial) → 跳過。
3. 逐報告 spawn `claude -p`(Sonnet) 依固定 schema 擷取 {claim, quote}。
4. Python 端用 reading/anchor.locate_quote 把引文確定性錨回正典文字（LLM 不給 offset）。
5. 每份報告在單一 transaction 內 DELETE + 全量 INSERT（**不是 upsert**，見 _replace_rows）。
6. 單筆失敗只寫 data/takeaway_failures.log，不中斷、不影響檢索/問答。**例外**：
   `claude` 不在 PATH 屬環境層級失敗（每篇都會踩），整批立即中止並回非零退出碼。

════════════════════════════════════════════════════════════════════════
不可妥協的不變量：正典文字＝clean_extracted(full_text)
════════════════════════════════════════════════════════════════════════
餵給 LLM 的文字、錨點基準字串、API 回傳給前端的文字，**三者必須同源**：

    canonical = clean_extracted(report.full_text)     # 絕對不是 full_text 本身

`research_report.full_text` 存的是「未清理」的原始抽取文字，保留 PDF 抽字的 CJK 間
空白（「台 積 電」）—— 見 app/services/reading/anchor.py 模組 docstring 事實一。
拿 full_text 當基準會讓**所有 offset 全錯**，而且測試抓不到（引文照樣「錨得到」，
只是錨在錯的座標系）。故本檔一取到 full_text 就立刻轉成 canonical，之後只用 canonical：
excerpt 取它的前 N 字、text_sha256 是它的 sha256、locate_quote 也搜它。

用法：
  uv run python scripts/extract_takeaways.py --dry-run       # 只印工作集大小
  uv run python scripts/extract_takeaways.py --limit 5       # 小跑試驗
  uv run python scripts/extract_takeaways.py                 # 近 90 天
  uv run python scripts/extract_takeaways.py --since-days 365
  uv run python scripts/extract_takeaways.py --reextract     # 版本升級後強制重跑

成本：--since-days 預設 90（約 549 篇、約 2-3 小時）。全語料 14,575 篇要跑十天以上，
故預設不跑全量；要補歷史請自行放大 --since-days 並有心理準備。

注意：每份研報都會冷啟動一個 `claude -p`；--workers 越高越容易頂滿磁碟小檔 I/O
（見 generate_summaries.py 註）。預設壓到 2。**且不可與其他 claude CLI 批次同時跑**
—— 併發搶 claude CLI 曾導致擷取大量被誤判 rejected（真因不是資料壞、也不是模型壞，
是搶資源）。這條規約現由 scripts/_claude_lock.py 的跨進程 flock 強制：撞車時本腳本
會印出持有者並以 rc=75 結束，不會產出壞資料。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.reading.anchor import locate_quote  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402
from scripts._claude_cli import CliNotFoundError, CliResult, run_claude  # noqa: E402
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "takeaway_failures.log"

# 擷取 schema / prompt 版本；schema 或 prompt 一改就 bump（舊列版本不符 → 自動重跑）
EXTRACTION_VERSION = "takeaway-2026-07-17.v1"

# 逐字引文重準確度（改寫一個字就錨不到）→ 預設 Sonnet；批次可用 --model 覆寫
TAKEAWAY_MODEL_DEFAULT = "claude-sonnet-5"

# 每篇最多幾條（prompt 要 3-5；多回的截掉。少於 3 條不算錯 —— prompt 明說「寧可少一
# 條也不要編造」，只有 0 條才 rejected）
MAX_TAKEAWAYS = 5

# 文字安全上限（防模型暴走輸出整段）。prompt 規定 claim ≤ 60 字、quote 15-60 字，
# 這裡放寬到約兩倍才截，避免把「只超標一點」的正常輸出攔腰砍斷。
CLAIM_MAX = 120
QUOTE_MAX = 200
RAW_TEXT_MAX = 4000  # rejected 時寫進 log 的原始回應長度上限


# ── LLM 必須回傳的固定 JSON schema（供 prompt 與解析對齊）──
# 非 f-string：schema 的大括號要原樣出現在 prompt 裡。
TAKEAWAY_INSTRUCTION = """你是金融研報的重點摘錄助理。閱讀以下券商研報內文，摘出這篇報告最重要的 3-5 條論點，
每條論點都要附一句「從原文逐字複製」的句子當證據。

嚴格規則（違反會被系統丟棄）：
1. 只輸出「單一 JSON 物件」，不要任何說明文字、不要 markdown、不要程式碼圍欄。
2. 3-5 條，依報告中的重要性由高到低排序。
3. claim＝一句話論點，繁體中文，不超過 60 字。客觀轉述研報的說法，不要加入你的評論。
4. quote＝**從上方研報內文逐字複製**的一句話，15-60 字，作為該論點的證據。
   - 不得改寫、不得補標點、不得加省略號、不得跨段落拼接。
   - 必須是內文中「連續出現」的一段字元：系統會逐字回頭比對，對不上就無法定位。
   - 引文請挑在內文中獨一無二的句子；頁首、頁尾、目錄、免責聲明這類重複出現的
     樣板文字不要拿來當引文。
5. 某條論點找不到可以逐字引用的句子時，寧可少一條，也不要編造或改寫引文。

JSON 格式：
{
  "takeaways": [
    {"claim": "<一句話論點，繁體中文，不超過 60 字>",
     "quote": "<研報內文的逐字片段，15-60 字>"}
  ]
}
"""


def build_takeaway_prompt(
    file_name: str,
    report_date: Optional[str],
    source: Optional[str],
    body_excerpt: str,
) -> str:
    """組裝擷取 prompt（仿 extract_signals.build_signal_prompt）。

    `body_excerpt` 必須是 canonical（clean_extracted 後）的前綴，不是 full_text 的前綴。
    """
    meta = [f"檔名：{file_name}"]
    if report_date:
        meta.append(f"報告日：{report_date}")
    if source:
        meta.append(f"券商：{source}")
    header = "\n".join(meta)
    return (
        f"{TAKEAWAY_INSTRUCTION}\n\n"
        f"{header}\n\n"
        f"研報內文：\n{body_excerpt}\n\n"
        f"請依上述 schema 只輸出單一 JSON 物件。"
    )


# ── 正典文字 / 指紋（不變量的唯一入口）──

def excerpt_without_tables(full_text: Optional[str], file_hash: Optional[str], canonical: str) -> str:
    """餵給 LLM 的文字：依 per-hash 快取的 Block 索引拿掉表格，再做同一套正典化。

    表格列被當成引文時，前端的關鍵字階梯沒有一階會剝掉 `|`，使用者會被帶到任意一處
    或看到「原文中找不到」。模型看不到表格列就不可能引用它。索引對不上（快取沒有、
    NUL 剝除改了長度、pypdf 路徑無索引）一律退回 canonical——寧可多看表格，不要切錯。"""
    from app.services.extraction import cache as extraction_cache

    if not full_text or not file_hash:
        return canonical
    rec = extraction_cache.read_record(file_hash)
    if not rec or not rec.get("blocks"):
        return canonical
    stripped = extraction_cache.strip_tables(full_text, rec["blocks"], expected_len=rec.get("char_count"))
    return clean_extracted(stripped) if stripped is not full_text else canonical


def canonical_text(full_text: Optional[str]) -> str:
    """full_text → 正典文字。**本檔取得 full_text 後唯一允許的轉換**。"""
    return clean_extracted(full_text or "")


def sha256_of(canonical: str) -> str:
    """正典文字的指紋。全文一變 sha 就變 → checkpoint 自動失效重跑，免人工介入。"""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 純 SQL builder（供 test_extract_takeaways_sql.py 字串斷言、無 DB）──
# 轉型一律用 CAST(:x AS ...)，**絕不寫 :x::type**：SQLAlchemy 的 text() 會把
# 「參數名緊接 ::」回溯成短名，導致參數完全沒綁上、冒號原樣進 PG → 生產 500。

def build_reports_sql(by_hashes: bool = False) -> str:
    """工作集：近 N 天（或指定 file_hash 清單）、有全文的研究報告。

    is_research 用 IS NOT FALSE（含 NULL：未判定的也算研報），不是 = true。
    report_date 為 NULL 者天然被 >= 比較排除（NULL 比較結果非 true）。

    by_hashes=True：改以 file_hash 清單選取，**且不套 report_date 條件**。
    定時同步必須走這條——`--since-days` 濾的是 `report_date` 而非入庫時間，而
    NAS 匯入的研報日期常常比入庫日早：實測近 10 天入庫的 90 篇裡有 79 篇（88%）
    的 report_date 超過一天前。用 `--since-days 1` 接排程會漏掉近九成新研報，
    而且是靜默漏——正是這次要修的那種失效。
    """
    where_scope = (
        "  AND r.file_hash = ANY(:hashes) "
        if by_hashes
        else "  AND r.report_date >= current_date - CAST(:since_days AS int) "
    )
    return (
        "SELECT r.id::text, r.file_name, r.report_date, r.source, r.full_text, r.file_hash "
        "FROM research.research_report r "
        "WHERE r.full_text IS NOT NULL "
        "  AND r.full_text <> '' "
        "  AND r.is_research IS NOT FALSE "
        + where_scope
        + "ORDER BY r.report_date DESC, r.file_name"
    )


def read_hashes_file(path: str) -> list[str]:
    """讀殼層寫的 file_hash 清單（每行一個），去除空白行與前後空白。

    與 generate_summaries.read_hashes_file 同語義（同一個 data/.sync_last_hashes）。
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [h.strip() for h in lines if h.strip()]


def build_existing_takeaways_sql() -> str:
    """撈既有摘錄列供 checkpoint 判斷（以 report_id::text 比對避免 uuid 陣列轉型）。"""
    return (
        "SELECT report_id::text, extraction_status, extraction_version, text_sha256 "
        "FROM research.report_takeaway "
        "WHERE report_id::text = ANY(:report_ids)"
    )


# 每份報告在單一 transaction 內：先 DELETE 該報告全部舊列，再全量 INSERT。
#
# **刻意不用 upsert（ON CONFLICT (report_id, ordinal) DO UPDATE）—— 不要「順手」改掉。**
# 摘錄是**變長列表**：重擷取可能從 5 條變 3 條，upsert 只會蓋掉 ordinal 1-3，
# 留下 ordinal 4-5 的陳舊尾列，閱讀頁就會顯示上一版的論點（且指向舊 offset）。
# delete + insert 是唯一能讓「列數變少」正確收斂的寫法。
#
# 對比 report_signal 之所以能用 upsert：它的鍵集合＝requested 標的代碼，事前已知且
# 每次相同，不會有「這次少了一個鍵」的情況。摘錄沒有這個性質。
TAKEAWAY_DELETE_SQL = text(
    "DELETE FROM research.report_takeaway WHERE report_id = CAST(:report_id AS uuid)"
)

TAKEAWAY_INSERT_SQL = text(
    """
    INSERT INTO research.report_takeaway
        (id, report_id, ordinal, claim, quote, quote_start, quote_end,
         anchor_method, text_sha256, extraction_version, extraction_status,
         raw_payload, error_detail)
    VALUES
        (CAST(:id AS uuid), CAST(:report_id AS uuid), :ordinal, :claim, :quote,
         :quote_start, :quote_end, :anchor_method, :text_sha256,
         :extraction_version, :extraction_status,
         CAST(:raw_payload AS jsonb), :error_detail)
    """
)


# ── 資料結構 ──

@dataclass
class ParsedTakeaways:
    """parse_takeaways 的結果：容錯、不 raise。ok=False 代表整份 payload 無法解析。

    ok=True 但 takeaways 為空（LLM 回了合法 JSON 的空陣列）也是常見情況，
    由 build_rows 判為「無列可寫」→ 批次記 rejected。
    """

    ok: bool
    takeaways: list[dict] = field(default_factory=list)  # [{"claim": str, "quote": str|None}]
    raw_text: str = ""
    error: Optional[str] = None


@dataclass
class TakeawayRow:
    """對應 research.report_takeaway 一列（id 由 row_to_params 產生）。"""

    report_id: str
    ordinal: int
    claim: str
    quote: Optional[str]
    quote_start: Optional[int]
    quote_end: Optional[int]
    anchor_method: Optional[str]
    text_sha256: str
    extraction_version: str
    extraction_status: str  # valid | partial（rejected 不寫列，見 extract_one）
    raw_payload: Optional[dict]
    error_detail: Optional[str]


# ── 解析（純函式）──

def _clean_claim(value: object) -> Optional[str]:
    """論點：收斂空白 + 轉繁體 + 截長。非字串/空字串 → None（該條目丟棄）。

    論點是 LLM 自己的轉述文字（prompt 規則 3 要繁體，但那是機率性保證），所以
    轉繁體。與下面的 `_clean_quote` **刻意相反**——引文絕對不能轉，理由見該處。
    截長在轉換之後，讓 DB 存的字串與長度上限描述的是同一個。
    """
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return None
    return to_traditional(cleaned)[:CLAIM_MAX]


def _clean_quote(value: object) -> Optional[str]:
    """引文：**只去頭尾空白 + 截長，內部空白原樣保留，且絕不轉繁體**。

    不轉繁體，兩個理由：**第一個與任何功能無關**——改一個字它就不再是逐字引文，
    而「原文就是這麼寫的」正是它存在的全部意義。第二個是它同時是 locate_quote 的
    錨定基準：全語料有 63 篇研報原文本身就是簡體，把引文轉成繁體會讓它在原文裡
    再也找不到，而 locate_quote 錨不到不會報錯，只會讓 quote_start/quote_end 靜默留空。

    內部空白不可動：canonical 的拉丁文字之間本來就有空白，改動內部空白會讓
    locate_quote 的 exact 層失手、掉到 normalized 層（能錨到但品質標示變差）。
    截長刻意在錨定「之前」做，好讓 DB 存的 quote 與 quote_start/quote_end 描述
    的是同一個字串（截長在後會讓 offset 對應到一段沒被存下來的文字）。
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:QUOTE_MAX]


def parse_takeaways(raw: str) -> ParsedTakeaways:
    """容錯解析 LLM 回應：去圍欄、抓首個 '{' 到末個 '}'。

    解析不出合法 JSON / 缺 takeaways 陣列 → ok=False（**不 raise**），保留原文供 log。
    claim 缺失或空白的條目直接丟棄（無論點的條目無意義）；quote 缺失的條目保留，
    quote=None → 錨不到 → 該報告落 partial。多回的條目截到 MAX_TAKEAWAYS。
    """
    raw_text = (raw or "")[:RAW_TEXT_MAX]
    if not raw or not raw.strip():
        return ParsedTakeaways(ok=False, raw_text=raw_text, error="空回應")
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ParsedTakeaways(ok=False, raw_text=raw_text, error="找不到 JSON 物件")
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError as exc:
        return ParsedTakeaways(ok=False, raw_text=raw_text, error=f"JSON 解析失敗：{exc}")
    if not isinstance(obj, dict):
        return ParsedTakeaways(ok=False, raw_text=raw_text, error="頂層非物件")
    arr = obj.get("takeaways")
    if not isinstance(arr, list):
        return ParsedTakeaways(ok=False, raw_text=raw_text, error="缺 takeaways 陣列")

    items: list[dict] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        claim = _clean_claim(it.get("claim"))
        if not claim:
            continue
        items.append({"claim": claim, "quote": _clean_quote(it.get("quote"))})
        if len(items) >= MAX_TAKEAWAYS:
            break
    return ParsedTakeaways(ok=True, takeaways=items, raw_text=raw_text)


def build_rows(
    report_id: str, canonical: str, text_sha256: str, parsed: ParsedTakeaways
) -> list[TakeawayRow]:
    """ParsedTakeaways → 可寫入的列（含確定性錨定）。無列可寫時回 []（＝rejected）。

    `canonical` 必須是 clean_extracted(full_text)，且與 text_sha256 同源。

    錨定回 None **不是失敗**：該條目照樣寫入（讀者看得到論點與引文），只是
    quote_start/anchor_method 為 NULL、前端不給跳。整份報告的狀態：
    全部錨到 → valid；有任一條錨不到 → partial。
    """
    if not parsed.ok:
        return []

    rows: list[TakeawayRow] = []
    for ordinal, item in enumerate(parsed.takeaways, start=1):
        quote = item.get("quote")
        anchor = locate_quote(canonical, quote) if quote else None
        if anchor is not None:
            error_detail = None
        elif quote:
            error_detail = "引文錨定失敗（原文找不到或多處出現）"
        else:
            error_detail = "LLM 未提供引文"
        rows.append(
            TakeawayRow(
                report_id=report_id,
                ordinal=ordinal,
                claim=item["claim"],
                quote=quote,
                quote_start=anchor.start if anchor else None,
                quote_end=anchor.end if anchor else None,
                anchor_method=anchor.method if anchor else None,
                text_sha256=text_sha256,
                extraction_version=EXTRACTION_VERSION,
                extraction_status="valid",  # 下方依整份錨定結果覆寫
                raw_payload=dict(item),
                error_detail=error_detail,
            )
        )
    if not rows:
        return []

    status = "valid" if all(r.quote_start is not None for r in rows) else "partial"
    for row in rows:
        row.extraction_status = status
    return rows


def row_to_params(row: TakeawayRow) -> dict:
    """TakeawayRow → insert named params（jsonb 欄位序列化為字串供 CAST）。"""
    return {
        "id": str(uuid.uuid4()),
        "report_id": row.report_id,
        "ordinal": row.ordinal,
        "claim": row.claim,
        "quote": row.quote,
        "quote_start": row.quote_start,
        "quote_end": row.quote_end,
        "anchor_method": row.anchor_method,
        "text_sha256": row.text_sha256,
        "extraction_version": row.extraction_version,
        "extraction_status": row.extraction_status,
        "raw_payload": (
            json.dumps(row.raw_payload, ensure_ascii=False)
            if row.raw_payload is not None
            else None
        ),
        "error_detail": row.error_detail,
    }


# ── claude CLI 呼叫（實作在 scripts/_claude_cli.py，全批次共用）──

def call_cli(prompt: str, model: str, timeout: int = 180) -> CliResult:
    """呼叫 `claude -p`。回 (stdout, None) 或 (None, 可辨識的失敗原因)。

    實作已抽到 `scripts/_claude_cli.py` 供所有批次共用——這個「失敗原因必須可區分」
    的設計最早長在這裡，抽出去是為了讓下一支腳本抄得到對的那份（其餘四支曾經各自
    抄了 `except Exception: return None` 的版本，見該模組 docstring 的四天停擺）。
    """
    return run_claude(prompt, model, timeout=timeout)


# ── 進度計數 ──
_done = 0
_ok = 0
_rejected = 0
_fail = 0


class WorkItem:
    """一份待擷取的研報。canonical 已是正典文字，全程不再碰 full_text。"""

    __slots__ = ("report_id", "file_name", "report_date", "source", "canonical",
                 "text_sha256", "excerpt_source")

    def __init__(self, report_id, file_name, report_date, source, canonical, text_sha256,
                 excerpt_source=None):
        self.report_id = report_id
        self.file_name = file_name
        self.report_date = report_date
        self.source = source
        self.canonical = canonical
        self.text_sha256 = text_sha256
        # 餵給 LLM 的文字：表格已拿掉的正典文字（E1c，§4.2「逐字引文的防護」）。
        # 沒有 Block 索引（pypdf 快取）時就是 canonical 本身。text_sha256 永遠對 canonical 算。
        self.excerpt_source = excerpt_source if excerpt_source is not None else canonical


async def _fetch_reports(session, since_days, hashes: list[str] | None = None):
    if hashes is not None:
        if not hashes:
            return []  # 本輪無新研報 → 不查 DB（比照 generate_summaries）
        return (
            await session.execute(
                text(build_reports_sql(by_hashes=True)), {"hashes": hashes}
            )
        ).all()
    return (
        await session.execute(text(build_reports_sql()), {"since_days": since_days})
    ).all()


async def _fetch_done_map(session, report_ids):
    """report_id → [(status, version, text_sha256), ...]，供 checkpoint 判斷。"""
    if not report_ids:
        return {}
    rows = (
        await session.execute(
            text(build_existing_takeaways_sql()), {"report_ids": list(report_ids)}
        )
    ).all()
    out: dict[str, list[tuple[str, str, str]]] = {}
    for rid, status, version, sha in rows:
        out.setdefault(rid, []).append((status, version, sha))
    return out


def _is_done(existing: list[tuple[str, str, str]], text_sha256: str, reextract: bool) -> bool:
    """該報告是否可跳過。checkpoint 由「內容」決定而非時間。

    existing = [(status, version, sha), ...]（該報告既有的全部列）。
    跳過條件：有列、且每列版本相符、sha 相符、狀態 ∈ (valid, partial)。

    - 版本不符 → prompt/schema 已升級，重跑。
    - sha 不符 → 全文變了（重新抽取/重新 ingest），舊 offset 已失效，重跑。
    - rejected → 上次沒擷出東西，重跑（正常路徑不會寫 rejected 列，此處是防禦性
      判斷：萬一有別的路徑寫了，checkpoint 也要能自動修）。
    - 無列 → 沒做過（或上次 rejected 什麼都沒寫），重跑。
    """
    if reextract:
        return False
    if not existing:
        return False
    for status, version, sha in existing:
        if status not in ("valid", "partial"):
            return False
        if version != EXTRACTION_VERSION:
            return False
        if sha != text_sha256:
            return False
    return True


async def build_worklist(
    since_days: int, reextract: bool, hashes: list[str] | None = None
) -> tuple[int, list[WorkItem]]:
    """回傳 (掃描到的報告數, 待擷取的 WorkItem)。

    hashes 非 None＝只處理這批 file_hash（定時同步用），忽略 since_days。
    """
    async with SessionFactory() as session:
        reports = await _fetch_reports(session, since_days, hashes)
        done_map = await _fetch_done_map(session, [r[0] for r in reports])

        worklist: list[WorkItem] = []
        for rid, file_name, report_date, source, full_text, file_hash in reports:
            canonical = canonical_text(full_text)
            if not canonical:
                continue  # 清理後空白（極端壞檔）→ 沒東西可摘
            sha = sha256_of(canonical)
            if _is_done(done_map.get(rid, []), sha, reextract):
                continue
            worklist.append(
                WorkItem(rid, file_name, report_date, source, canonical, sha,
                         excerpt_source=excerpt_without_tables(full_text, file_hash, canonical))
            )
    return len(reports), worklist


async def _replace_rows(report_id: str, rows: list[TakeawayRow]) -> None:
    """單一 transaction 內：先刪該報告全部舊列，再全量插入新列。

    見 TAKEAWAY_DELETE_SQL 上方註解 —— **不可改成 upsert**。
    """
    async with SessionFactory() as session:
        await session.execute(TAKEAWAY_DELETE_SQL, {"report_id": report_id})
        for row in rows:
            await session.execute(TAKEAWAY_INSERT_SQL, row_to_params(row))
        await session.commit()


def _log_failure(item: WorkItem, reason: str) -> None:
    with open(FAIL_LOG, "a", encoding="utf-8") as f:
        f.write(f"{item.report_id}\t{item.file_name}\t{reason}\n")


async def extract_one(
    sem: asyncio.Semaphore, item: WorkItem, excerpt: int, model: str, total: int,
    retries: int = 2,
) -> None:
    global _done, _ok, _rejected, _fail
    date_str = item.report_date.isoformat() if item.report_date else None
    prompt = build_takeaway_prompt(
        item.file_name, date_str, item.source, item.excerpt_source[:excerpt]
    )
    parsed: Optional[ParsedTakeaways] = None
    # 保留最後一次的失敗原因：三次都沒回應時，log 要寫得出是逾時、非零退出碼還是別的
    last_error = "CLI 無回應"
    async with sem:
        for _ in range(retries + 1):
            # CliNotFoundError 刻意不接：那是環境壞了（每篇都會踩），
            # 讓它一路拋到 main 中止整批，而不是靜靜地把 N 篇都記成 rejected。
            res = await asyncio.to_thread(call_cli, prompt, model)
            if res.text:
                parsed = parse_takeaways(res.text)
                if parsed.ok:
                    break
            elif res.error:
                last_error = res.error
        if parsed is None:
            parsed = ParsedTakeaways(ok=False, error=last_error)

    try:
        rows = build_rows(item.report_id, item.canonical, item.text_sha256, parsed)
        if not rows:
            # rejected：**不寫任何列**（也不刪既有列 —— 一次 CLI 抽風不該毀掉上一版
            # 好的摘錄）。下次批次看不到符合的列/或 sha 仍不符 → 自動重跑。
            _rejected += 1
            _log_failure(item, parsed.error or "0 條摘錄")
        else:
            await _replace_rows(item.report_id, rows)
            _ok += 1
    except Exception as exc:  # 單筆例外只記 log，不中斷長跑
        _fail += 1
        _log_failure(item, f"EXC:{exc}")

    _done += 1
    if _done % 10 == 0 or _done == total:
        print(f"  {_done}/{total}  ok={_ok} rejected={_rejected} fail={_fail}", flush=True)


async def main(args) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    hashes = read_hashes_file(args.hashes_file) if args.hashes_file else None
    scanned, worklist = await build_worklist(args.since_days, args.reextract, hashes)
    scope = f"本輪 {len(hashes)} 個 file_hash" if hashes is not None else f"近 {args.since_days} 天"
    print(
        f"{scope}研報：{scanned} 篇｜待擷取：{len(worklist)} 篇"
        f"｜version={EXTRACTION_VERSION}｜model={args.model}",
        flush=True,
    )

    if args.dry_run:
        print(f"\n[dry-run] 待擷取 {len(worklist)} 篇（未呼叫 LLM）", flush=True)
        return

    if args.limit:
        worklist = worklist[: args.limit]
    total = len(worklist)
    if not total:
        print("nothing to do（近期研報皆已擷取）", flush=True)
        return

    sem = asyncio.Semaphore(args.workers)
    try:
        await asyncio.gather(
            *(extract_one(sem, item, args.excerpt, args.model, total) for item in worklist)
        )
    except CliNotFoundError as exc:
        # 環境層級失敗：剩下的每一篇都會踩到同一顆地雷。中止並以非零退出碼收場 ——
        # 「跑完 549 次註定失敗的呼叫、印 ok=0 rejected=549、然後 exit 0」是最糟的結局。
        print(f"\n中止：{exc}", flush=True)
        print(f"（已完成 {_done}/{total}；ok={_ok} rejected={_rejected} fail={_fail}）", flush=True)
        raise SystemExit(2) from exc
    print(f"\ndone. ok={_ok} rejected={_rejected} fail={_fail}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=int, default=90,
                    help="只擷取近 N 天的研報（預設 90；全語料成本過高）。"
                         "注意它濾的是 report_date 而非入庫時間")
    ap.add_argument("--hashes-file", default=None,
                    help="只處理這個檔案裡列出的 file_hash（每行一個），忽略 --since-days。"
                         "定時同步用（data/.sync_last_hashes）——因為 --since-days 濾 "
                         "report_date，而 NAS 匯入的研報日期常比入庫日早（實測近 10 天"
                         "入庫者有 88%% 的 report_date 超過一天前），用天數接排程會靜默漏掉近九成")
    ap.add_argument("--workers", type=int, default=2,
                    help="同時 claude CLI 呼叫數（勿調高；與其他批次的互斥由 _claude_lock.py 強制）")
    ap.add_argument("--limit", type=int, default=None, help="最多擷取幾篇（試跑用）")
    ap.add_argument("--excerpt", type=int, default=24000, help="餵給 LLM 的正典文字上限")
    ap.add_argument("--model", default=TAKEAWAY_MODEL_DEFAULT)
    ap.add_argument("--reextract", action="store_true", help="忽略 checkpoint，強制重跑")
    ap.add_argument("--dry-run", action="store_true", help="只印工作集大小，不呼叫 LLM")
    # --dry-run 也一起擋：鎖的涵蓋範圍若隨旗標而變，日後有人在「不呼叫 LLM」的路徑上
    # 加了一個 LLM 呼叫，就會出現一個沒人發現的洞。要在批次跑到一半時查工作集，
    # 用 CLAUDE_LOCK_DISABLE=1（它只讀 DB，不搶 CLI）。
    with claude_cli_lock_or_exit("extract_takeaways"):
        asyncio.run(main(ap.parse_args()))
