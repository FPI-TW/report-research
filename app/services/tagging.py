"""市場標籤 schema（標準市場代碼）+ Claude 標註指令 + tag JSON 解析/載入。

市場標籤採用框架固定的**標準市場代碼** TW/US/HK/CN/FX/WTX/MACRO/GLOBAL/CRYPTO（`MARKETS`，
`tests/test_tagging.py` 逐字釘住；標註 prompt 也用這一組）。profile 只能選子集與改顯示名稱
（`app/profile.py` 的 `[markets]`），不能新增代碼。標準詞彙未涵蓋的類別歸到最接近者
（債券→MACRO、原物料→GLOBAL）。

個股代號格式（`CodeFormat`）的資料形狀與框架預設也在這裡：profile 的 `[instruments]` 宣告格式，標註結果的
`stock_targets` 依它驗證（`normalize_stock_targets`），標註 prompt 的代號說明與範例也由它產生（`tag_instruction`）。
prompt 的其餘文字固定，profile 不能覆寫。

這組代碼與 findb 的市場分類（findb: app/models/canonical.py、scripts/seed_data.py）對齊——那是預設
profile 的跨 repo 契約，不是框架的；`LEGACY_TO_FINDB` 等名稱沿用當時的命名。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Optional

# 標準市場代碼（大寫）；預設 profile 與 findb instruments.market 一致
MARKETS = ["TW", "US", "HK", "CN", "FX", "WTX", "MACRO", "GLOBAL", "CRYPTO"]

# 市場代碼 → 顯示名稱（前端/報表用）
MARKET_DISPLAY: dict[str, str] = {
    "TW": "台股",
    "US": "美股",
    "HK": "港股",
    "CN": "陸股",
    "FX": "外匯",
    "WTX": "台指期",
    "MACRO": "總經",
    "GLOBAL": "全球",
    "CRYPTO": "加密",
}

# 舊中文標籤 → 標準市場代碼（含標準詞彙未涵蓋類別歸到最接近者）
LEGACY_TO_FINDB: dict[str, str] = {
    "台股": "TW",
    "美股": "US",
    "港股": "HK",
    "陸股": "CN",
    "外匯": "FX",
    "期貨": "WTX",
    "總體經濟": "MACRO",
    "多元配置": "GLOBAL",
    "債券": "MACRO",  # findb 無債券市場 → 歸利率/總經
    "原物料": "GLOBAL",  # findb 無原物料市場 → 歸全球多市場
}

# 金融商品類型固定詞表（小寫，可多值）
INSTRUMENT_TYPES = [
    "equity",
    "index",
    "futures",
    "options",
    "etf",
    "bond",
    "fx",
    "commodity",
    "crypto",
]

# 商品類型 → 顯示名稱（前端/報表用）
INSTRUMENT_DISPLAY: dict[str, str] = {
    "equity": "股票",
    "index": "指數",
    "futures": "期貨",
    "options": "選擇權",
    "etf": "ETF",
    "bond": "債券",
    "fx": "外匯",
    "commodity": "原物料",
    "crypto": "加密",
}

# 期貨商品標的固定詞表（小詞表，可多值；詞表外不輸出）
FUTURES_TARGETS = [
    "台指期",
    "小型台指",
    "電子期",
    "金融期",
    "個股期貨",
    "其他",
]

# 個股標的清單儲存上限（依重要性排序後截斷）
STOCK_TARGETS_MAX = 8

# 文件類型（`research_report.doc_type`，revision 0012）。標註器的 `is_research` 判斷映射到這兩個類型：
# true＝研報、false＝非研究檔（行政／活動檔，對應改動前的 is_research=false，所有功能關閉）。
# 每種類型啟用哪些功能由 profile 的 `[doc_types]` 決定（`app/profile.py`、`app/services/doc_types.py`）；
# 新增類型時這裡加上該類型的標註判斷，profile 宣告它的功能，各功能的 SQL 不必改。
# 這兩個值同時是 revision 0012 回填與 `is_research` 同步約束的字面值，不可改名。
DOC_TYPE_RESEARCH = "research"
DOC_TYPE_NON_RESEARCH = "non_research"
# 標註器會產生的類型；profile 的 `[doc_types]` 必須全部宣告（缺了會讓那類文件靜默消失）。
TAGGER_DOC_TYPES: tuple[str, ...] = (DOC_TYPE_RESEARCH, DOC_TYPE_NON_RESEARCH)


def doc_type_for(is_research: bool) -> str:
    """標註器的 is_research 判斷 → 文件類型。"""
    return DOC_TYPE_RESEARCH if is_research else DOC_TYPE_NON_RESEARCH


@dataclass
class MarketTag:
    market: Optional[str]  # 標準市場代碼；非研究檔為 None
    is_research: bool
    confidence: float = 0.0
    instrument_types: Optional[list[str]] = None  # 商品類型（詞表見上）
    relates_stock: Optional[bool] = None  # 是否與個股相關
    relates_futures: Optional[bool] = None  # 是否與期貨/指數部位相關
    stock_targets: Optional[list[str]] = None  # 個股標的代號清單（格式見 normalize_stock_targets）
    futures_targets: Optional[list[str]] = None  # 期貨商品（FUTURES_TARGETS 詞表）

    @property
    def doc_type(self) -> str:
        """文件類型（由 is_research 映射）。刻意是屬性而不是欄位：tag JSON 的格式不變。"""
        return doc_type_for(self.is_research)


# ── 個股代號格式 ─────────────────────────────────────────────────────────
# 格式本身由 profile 宣告（`app/profile.py` 的 `[instruments]` 解析成這裡的 `CodeFormat`）；本模組是 profile 的依賴、
# 刻意不 import `app.*`（`tests/test_profile.py` 守），所以資料形狀與預設放在這裡，profile 引用它們。

# 個股代號格式的種類（框架固定的詞彙）：digits＝純數字（台股、港股、陸股），letters＝大寫英文字母（美股 ticker，
# `share_class` 另收「.A」這種一個字母的股別後綴，例如 BRK.B）。只給種類與長度、不收任意 regex：同一份規則要能
# 安全地在每份研報的標註結果上執行，也要講得清楚它收什麼。
CODE_FORMAT_KINDS = ("digits", "letters")


@dataclass(frozen=True)
class CodeFormat:
    market: str  # 這個格式屬於哪個標準市場（prompt 的市場說明用；驗證是所有格式的聯集，見 profile 的 Instruments）
    kind: str  # CODE_FORMAT_KINDS 之一
    min_length: int
    max_length: int
    share_class: bool = False  # letters：另收一個字母的股別後綴（BRK.B）

    def regex(self) -> str:
        span = f"{{{self.min_length},{self.max_length}}}"
        if self.kind == "digits":
            return rf"\d{span}"
        return rf"[A-Z]{span}" + (r"(?:\.[A-Z])?" if self.share_class else "")


# 框架預設（profile 的 `[instruments]` 缺席時）：只收正好 4 碼數字。這是框架化之前寫死的規則，預設 profile 也宣告它。
DEFAULT_CODE_FORMATS: tuple[CodeFormat, ...] = (CodeFormat(market="TW", kind="digits", min_length=4, max_length=4),)

# prompt 裡的代號範例：(標準市場, 種類) → (輸出格式 JSON 的候選, 市場說明的候選)，依序挑長度符合格式者。
# 沒有候選的組合不寫範例（只寫長度），不臨時編造代號。
_CODE_EXAMPLES: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {
    ("TW", "digits"): (("2330", "00878", "006208"), ("1102", "2317", "00878", "006208")),
    ("HK", "digits"): (("0700", "00700"), ("0700", "0005", "09988")),
    ("CN", "digits"): (("600519",), ("600519", "000001")),
    ("US", "letters"): (("NVDA", "AAPL", "GOOGL", "IBM", "F"), ("NVDA", "AAPL", "GOOGL", "IBM", "F")),
}
_SHARE_CLASS_EXAMPLES = ("BRK.B", "BF.B")


def _length_label(fmt: CodeFormat) -> str:
    return f"{fmt.min_length} 碼" if fmt.min_length == fmt.max_length else f"{fmt.min_length}～{fmt.max_length} 碼"


def _fitting(fmt: CodeFormat, pool: tuple[str, ...]) -> list[str]:
    return [c for c in pool if fmt.min_length <= len(c.split(".")[0]) <= fmt.max_length]


def _spread(codes: list[str]) -> list[str]:
    """最多兩個範例；長度有範圍時第二個挑不同長度的，讓範例本身說明長度可變。"""
    if not codes:
        return []
    first = codes[0]
    second = next((c for c in codes[1:] if len(c) != len(first)), codes[1] if len(codes) > 1 else None)
    return [first] + ([second] if second else [])


def code_length_phrase(code_formats: Sequence[CodeFormat]) -> str:
    """prompt 裡「○○代碼」的修飾語：只有一種格式時說出長度（預設 profile 是「4 碼」），多種格式時回空字串
    （格式寫在標註 prompt 的各市場說明；訊號擷取的代碼本來就限定在指定清單內）。"""
    if len(code_formats) != 1:
        return ""
    fmt = code_formats[0]
    return _length_label(fmt) + ("英文字母" if fmt.kind == "letters" else "")


def _market_code_note(fmt: CodeFormat) -> str:
    examples = _spread(_fitting(fmt, _CODE_EXAMPLES.get((fmt.market, fmt.kind), ((), ()))[1]))
    shown = f"如 {'、'.join(examples)}" if examples else ""
    if fmt.kind == "digits":
        return f"{_length_label(fmt)}代碼{shown}"
    note = f"{_length_label(fmt)}大寫英文字母代碼{shown}"
    if fmt.share_class:
        share = _fitting(fmt, _SHARE_CLASS_EXAMPLES)
        note += f"，股別後綴寫成 {share[0]}" if share else "，股別後綴寫成「代碼.字母」"
    return note


# 標註 prompt 各市場的說明：(代碼, 說明, 括號內的補充)。順序＝`MARKETS`。有宣告代號格式的市場，括號內先寫格式。
_MARKET_LINES: tuple[tuple[str, str, str], ...] = (
    ("TW", "台股，台灣上市櫃個股或產業", "本土券商個股報告"),
    ("US", "美股，美國個股或市場", "多為英文外資報告、webcast/flyer 法說"),
    ("HK", "港股，香港掛牌個股或市場", ""),
    ("CN", "陸股，中國 A 股、陸股策略", ""),
    ("FX", "外匯、匯率", ""),
    ("WTX", "台指期，期貨、選擇權、部位限制", "如台指期盤後報"),
    ("MACRO", "總經、利率、跨市場宏觀策略；**債券/固定收益（債券週報、殖利率）也歸 MACRO**", ""),
    ("GLOBAL", "跨市場資產配置、全球策略；**原物料/商品/能源/金屬也歸 GLOBAL**", ""),
    ("CRYPTO", "加密貨幣", ""),
)


def _market_lines(code_formats: Sequence[CodeFormat]) -> str:
    by_market = {f.market: f for f in code_formats}
    lines = []
    for code, desc, extra in _MARKET_LINES:
        parts = [p for p in ((_market_code_note(by_market[code]) if code in by_market else ""), extra) if p]
        lines.append(f"- {code}：{desc}" + (f"（{'；'.join(parts)}）" if parts else ""))
    return "\n".join(lines)


def _json_examples(code_formats: Sequence[CodeFormat]) -> str:
    picks = []
    for fmt in code_formats:
        fits = _fitting(fmt, _CODE_EXAMPLES.get((fmt.market, fmt.kind), ((), ()))[0])
        if fits and fits[0] not in picks:
            picks.append(fits[0])
    return ", ".join(f'"{c}"' for c in picks or ["<代碼>"])


# 給標註 LLM 的規則。代號格式的片段由 profile 的 `[instruments]` 推導（`tag_instruction`），其餘逐字固定：
# profile 不能覆寫 prompt 文字。預設 profile 渲染出的全文與框架化之前的常數逐字相同
# （`tests/test_instrument_prompts.py` 對 golden 逐字比對）。
_TAG_TEMPLATE = Template("""你是研究報告分類助手。閱讀指定的 PDF/Word 檔，判斷它的「主要市場」、「金融商品類型」與「個股/期貨關聯」。

market 只能是下列代碼其一（大寫）：
$market_lines

instrument_types：報告「實際提及」的金融商品類型，從下列詞表選 0 到多個（小寫，可複選）：
- equity 股票/個股、index 指數/大盤、futures 期貨、options 選擇權、etf ETF、
  bond 債券/固收、fx 外匯、commodity 原物料/商品、crypto 加密貨幣
（詞表外的一律不要輸出；無明確商品則給空陣列 []。）

relates_stock / relates_futures：兩個獨立布林，判斷報告對該交易族群「是否有參考價值」，與 instrument_types 語意不同：
- relates_stock：對「選股／個股交易」是否有參考價值（個股/產業報告通常 true）。
- relates_futures：對「期貨／指數部位操作」是否有參考價值（台指期、大盤策略、總經部位通常 true，
  即使報告本身不含 futures 商品也可能 true）。

stock_targets：報告「聚焦／評等／重點討論」的個股，輸出${targets_code}股票代碼字串清單（${targets_note}依重要性排序，最多 8 檔）：
- 個股報告 → 只放該標的（1 檔）。
- 週報／策略／產業報告 → 只列文中「重點討論」的個股代碼（非順帶提及），控制在代表性的幾檔。
- 純總經/匯率、無明確個股 → []。
- 只輸出能明確判定${targets_code}代碼者；只有公司名而無法確定代碼則略過該檔（寧缺勿錯，別把日期/頁碼當代碼）。

futures_targets：報告涉及的期貨商品，從下列固定小詞表選 0 到多個（詞表外不要輸出）：
- 台指期、小型台指、電子期、金融期、個股期貨、其他
- 台指期盤後報 → ["台指期"]（若也談電子/金融期則一併列出）。
- relates_futures=true 但無具體期貨商品（總經/債券對部位有參考價值）→ []。

非研究檔（人事異動、行事曆、組織架構、部位限制公告、webcast flyer、發票帳單等）：is_research 設 false。

輸出規則：
1. 只輸出 JSON，不要任何說明文字或程式碼圍欄。
2. JSON 格式：{"market": "<上列代碼其一>", "is_research": <true/false>, "confidence": <0~1 小數>, "instrument_types": ["<詞表代碼>", ...], "relates_stock": <true/false>, "relates_futures": <true/false>, "stock_targets": [$json_examples, ...], "futures_targets": ["台指期", ...]}
3. 非研究檔 is_research=false（其餘欄位仍盡量填，instrument_types / stock_targets / futures_targets 可為 []）。
4. confidence 表示你對市場代碼的信心。
5. 若涉及多市場，選報告主軸；真的跨多市場且無單一主軸才用 GLOBAL。
""")  # noqa: E501


def tag_instruction(code_formats: Sequence[CodeFormat]) -> str:
    """標註 prompt 的規則全文；代號格式的說明與範例依 `code_formats` 產生（呼叫端傳 `get_profile().instruments.
    code_formats`）。參數必填：漏傳就 TypeError，不會靜默退回某一套格式。"""
    phrase = code_length_phrase(code_formats)
    return _TAG_TEMPLATE.substitute(
        market_lines=_market_lines(code_formats),
        targets_code=f" {phrase}" if phrase else "",
        targets_note="" if len(code_formats) == 1 else "代碼格式見上方各市場的說明，不限報告的主要市場；",
        json_examples=_json_examples(code_formats),
    )


# 預設 profile（`DEFAULT_CODE_FORMATS`）的版本。送給 LLM 的 prompt 一律在呼叫時以 `tag_instruction` 依 profile 渲染，
# 這個常數只留給不經 profile 的參照。
TAG_INSTRUCTION = tag_instruction(DEFAULT_CODE_FORMATS)


def normalize_market(value: Optional[str]) -> Optional[str]:
    """把舊中文標籤或代碼正規化為標準市場代碼；無法對應回傳 None。"""
    if not value:
        return None
    v = value.strip()
    v = LEGACY_TO_FINDB.get(v, v).upper()
    return v if v in MARKETS else None


def normalize_instruments(value) -> list[str]:
    """正規化商品類型：小寫、去詞表外、去重保序；非 list 回傳 []。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        v = item.strip().lower()
        if v in INSTRUMENT_TYPES and v not in out:
            out.append(v)
    return out


# 個股代號的驗證規則：一個標註輸出的代號 → 正規化後的代號，不合格回 None。由呼叫端傳入 profile 的
# `get_profile().instruments.normalize_code`（`app/profile.py` 的 `Instruments`）：本模組是 profile 的依賴、
# 刻意不 import `app.*`（`tests/test_profile.py` 守），所以規則不在這裡讀。參數一律必填：漏傳就 TypeError，
# 不會靜默退回某一套規則。
CodeRule = Callable[[object], Optional[str]]


def normalize_stock_targets(value, *, normalize_code: CodeRule) -> list[str]:
    """正規化個股標的：只留通過 `normalize_code` 的值、去重保序、截斷到上限；非 list 回傳 []。

    格式由 profile 的 `[instruments]` 宣告（預設 profile 只收「正好 4 碼數字」，section 缺席時也是這條），
    標註 prompt 的代號說明也由同一份格式產生（`tag_instruction`）。防呆：擋掉把日期/頁碼（如 0209、Page 6）
    誤當代碼的情形。
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        v = normalize_code(item)
        if v is not None and v not in out:
            out.append(v)
    return out[:STOCK_TARGETS_MAX]


def normalize_futures_targets(value) -> list[str]:
    """正規化期貨標的：僅保留 FUTURES_TARGETS 詞表內的值、去重保序；非 list 回傳 []。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        v = item.strip()
        if v in FUTURES_TARGETS and v not in out:
            out.append(v)
    return out


def build_tagging_prompt(file_path: str, file_name: str, *, code_formats: Sequence[CodeFormat]) -> str:
    return (
        f"{tag_instruction(code_formats)}\n\n"
        f"檔案路徑：{file_path}\n檔名：{file_name}\n\n"
        f"請用 Read 工具讀取上述檔案後輸出 JSON。"
    )


def parse_tags(raw: str, *, normalize_code: CodeRule) -> Optional[MarketTag]:
    """容錯解析 Claude 回應；market 一律正規化為標準市場代碼，stock_targets 經 `normalize_code` 過濾。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    market = normalize_market(obj.get("market"))
    is_research = bool(obj.get("is_research", market is not None))
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    instrument_types = normalize_instruments(obj.get("instrument_types"))
    rs = obj.get("relates_stock")
    rf = obj.get("relates_futures")
    relates_stock = bool(rs) if rs is not None else None
    relates_futures = bool(rf) if rf is not None else None
    stock_targets = normalize_stock_targets(obj.get("stock_targets"), normalize_code=normalize_code)
    futures_targets = normalize_futures_targets(obj.get("futures_targets"))
    return MarketTag(
        market=market,
        is_research=is_research,
        confidence=confidence,
        instrument_types=instrument_types,
        relates_stock=relates_stock,
        relates_futures=relates_futures,
        stock_targets=stock_targets,
        futures_targets=futures_targets,
    )


def load_tag(tags_dir: Path, file_hash: str, *, normalize_code: CodeRule) -> Optional[MarketTag]:
    p = tags_dir / f"{file_hash}.json"
    if not p.exists():
        return None
    return parse_tags(p.read_text(encoding="utf-8"), normalize_code=normalize_code)
