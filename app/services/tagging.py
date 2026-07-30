"""市場標籤 schema（對齊 findb 市場分類）+ Claude 標註指令 + tag JSON 解析/載入。

市場標籤採用 findb 的市場代碼（findb: app/models/canonical.py、scripts/seed_data.py）：
TW/US/HK/CN/FX/WTX/MACRO/GLOBAL/CRYPTO。findb 未涵蓋的類別歸到最接近者
（債券→MACRO、原物料→GLOBAL）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# findb 市場代碼（大寫），與 findb instruments.market 一致
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

# 舊中文標籤 → findb 代碼（含 findb 未涵蓋類別歸到最接近者）
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


@dataclass
class MarketTag:
    market: Optional[str]  # findb 市場代碼；非研究檔為 None
    is_research: bool
    confidence: float = 0.0
    instrument_types: Optional[list[str]] = None  # 商品類型（詞表見上）
    relates_stock: Optional[bool] = None  # 是否與個股相關
    relates_futures: Optional[bool] = None  # 是否與期貨/指數部位相關
    stock_targets: Optional[list[str]] = None  # 個股標的 4 碼代碼清單
    futures_targets: Optional[list[str]] = None  # 期貨商品（FUTURES_TARGETS 詞表）


# 給 Claude 標註 agent 的規則（嵌入 workflow 指令中）
TAG_INSTRUCTION = """你是研究報告分類助手。閱讀指定的 PDF/Word 檔，判斷它的「主要市場」、「金融商品類型」與「個股/期貨關聯」。

market 只能是下列代碼其一（大寫）：
- TW：台股，台灣上市櫃個股或產業（4 碼代碼如 1102、2317；本土券商個股報告）
- US：美股，美國個股或市場（多為英文外資報告、webcast/flyer 法說）
- HK：港股，香港掛牌個股或市場
- CN：陸股，中國 A 股、陸股策略
- FX：外匯、匯率
- WTX：台指期，期貨、選擇權、部位限制（如台指期盤後報）
- MACRO：總經、利率、跨市場宏觀策略；**債券/固定收益（債券週報、殖利率）也歸 MACRO**
- GLOBAL：跨市場資產配置、全球策略；**原物料/商品/能源/金屬也歸 GLOBAL**
- CRYPTO：加密貨幣

instrument_types：報告「實際提及」的金融商品類型，從下列詞表選 0 到多個（小寫，可複選）：
- equity 股票/個股、index 指數/大盤、futures 期貨、options 選擇權、etf ETF、
  bond 債券/固收、fx 外匯、commodity 原物料/商品、crypto 加密貨幣
（詞表外的一律不要輸出；無明確商品則給空陣列 []。）

relates_stock / relates_futures：兩個獨立布林，判斷報告對該交易族群「是否有參考價值」，與 instrument_types 語意不同：
- relates_stock：對「選股／個股交易」是否有參考價值（個股/產業報告通常 true）。
- relates_futures：對「期貨／指數部位操作」是否有參考價值（台指期、大盤策略、總經部位通常 true，
  即使報告本身不含 futures 商品也可能 true）。

stock_targets：報告「聚焦／評等／重點討論」的個股，輸出 4 碼股票代碼字串清單（依重要性排序，最多 8 檔）：
- 個股報告 → 只放該標的（1 檔）。
- 週報／策略／產業報告 → 只列文中「重點討論」的個股代碼（非順帶提及），控制在代表性的幾檔。
- 純總經/匯率、無明確個股 → []。
- 只輸出能明確判定 4 碼代碼者；只有公司名而無法確定代碼則略過該檔（寧缺勿錯，別把日期/頁碼當代碼）。

futures_targets：報告涉及的期貨商品，從下列固定小詞表選 0 到多個（詞表外不要輸出）：
- 台指期、小型台指、電子期、金融期、個股期貨、其他
- 台指期盤後報 → ["台指期"]（若也談電子/金融期則一併列出）。
- relates_futures=true 但無具體期貨商品（總經/債券對部位有參考價值）→ []。

非研究檔（人事異動、行事曆、組織架構、部位限制公告、webcast flyer、發票帳單等）：is_research 設 false。

輸出規則：
1. 只輸出 JSON，不要任何說明文字或程式碼圍欄。
2. JSON 格式：{"market": "<上列代碼其一>", "is_research": <true/false>, "confidence": <0~1 小數>, "instrument_types": ["<詞表代碼>", ...], "relates_stock": <true/false>, "relates_futures": <true/false>, "stock_targets": ["2330", ...], "futures_targets": ["台指期", ...]}
3. 非研究檔 is_research=false（其餘欄位仍盡量填，instrument_types / stock_targets / futures_targets 可為 []）。
4. confidence 表示你對市場代碼的信心。
5. 若涉及多市場，選報告主軸；真的跨多市場且無單一主軸才用 GLOBAL。
"""  # noqa: E501


def normalize_market(value: Optional[str]) -> Optional[str]:
    """把舊中文標籤或代碼正規化為 findb 市場代碼；無法對應回傳 None。"""
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


_RE_STOCK_CODE = re.compile(r"^\d{4}$")


def normalize_stock_targets(value) -> list[str]:
    """正規化個股標的：只留「正好 4 碼數字」、去重保序、截斷到上限；非 list 回傳 []。

    防呆：擋掉把日期/頁碼（如 0209、Page 6）誤當代碼的情形。
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        v = str(item).strip()
        if _RE_STOCK_CODE.match(v) and v not in out:
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


def build_tagging_prompt(file_path: str, file_name: str) -> str:
    return (
        f"{TAG_INSTRUCTION}\n\n"
        f"檔案路徑：{file_path}\n檔名：{file_name}\n\n"
        f"請用 Read 工具讀取上述檔案後輸出 JSON。"
    )


def parse_tags(raw: str) -> Optional[MarketTag]:
    """容錯解析 Claude 回應；market 一律正規化為 findb 代碼。"""
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
    stock_targets = normalize_stock_targets(obj.get("stock_targets"))
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


def load_tag(tags_dir: Path, file_hash: str) -> Optional[MarketTag]:
    p = tags_dir / f"{file_hash}.json"
    if not p.exists():
        return None
    return parse_tags(p.read_text(encoding="utf-8"))
