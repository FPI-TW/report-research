"""檔名解析服務。

僅用於：分層抽樣分桶、輔助 metadata（股票代碼/券商/日期/報告類型）。
市場標籤本身由 Claude 讀 PDF 判定，不在此決定。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# 券商代碼 → 正規化名稱（外資 + 本土）
BROKER_MAP: dict[str, str] = {
    "MS": "morgan_stanley",
    "GS": "goldman_sachs",
    "JPM": "jpmorgan",
    "UBS": "ubs",
    "NMR": "nomura",
    "MQ": "macquarie",
    "DW": "daiwa",
    "CLSA": "clsa",
    "CITI": "citi",
    "BOFA": "bofa",
    "BAML": "bofa",
    "HSBC": "hsbc",
    "HTI": "haitong",
    "JEF": "jefferies",
    "ALETHEIA": "aletheia",
    "中信": "citic",
    "群益": "capital",
    "國泰": "cathay",
    "凱基": "kgi",
    "元大": "yuanta",
    "元富": "masterlink",
    "宏遠": "hongyuan",
    "富邦": "fubon",
    "永豐": "sinopac",
    "兆豐": "mega",
    "統一": "president",
    "日盛": "jihsun",
    "第一金": "first",
    "國票": "ibf",
}

# 正規化 source → 顯示用名稱（給前端卡片）
SOURCE_DISPLAY: dict[str, str] = {
    "morgan_stanley": "摩根士丹利",
    "goldman_sachs": "高盛",
    "jpmorgan": "摩根大通",
    "ubs": "瑞銀",
    "nomura": "野村",
    "macquarie": "麥格理",
    "daiwa": "大和",
    "clsa": "里昂",
    "citi": "花旗",
    "bofa": "美銀",
    "hsbc": "滙豐",
    "haitong": "海通",
    "jefferies": "傑富瑞",
    "aletheia": "Aletheia",
    "citic": "中信",
    "capital": "群益",
    "cathay": "國泰",
    "kgi": "凱基",
    "yuanta": "元大",
    "masterlink": "元富",
    "fubon": "富邦",
    "sinopac": "永豐",
    "mega": "兆豐",
    "president": "統一",
    "jihsun": "日盛",
    "first": "第一金",
    "ibf": "國票",
    "hongyuan": "宏遠",
}


def source_display(source: str | None) -> str | None:
    if not source:
        return None
    return SOURCE_DISPLAY.get(source, source)


# 外資券商（英文報告 → 通常美股/海外）
FOREIGN_BROKERS = {
    "morgan_stanley", "goldman_sachs", "jpmorgan", "ubs", "nomura",
    "macquarie", "daiwa", "clsa", "citi", "bofa", "hsbc", "haitong",
    "jefferies", "aletheia",
}

REPORT_TYPE_KEYWORDS = [
    "雙週報", "週報", "月報", "速報", "策略", "評析", "報告", "snapshot", "memo",
]

# 行政/活動檔關鍵字（檔名即可判定的非研究檔）
ADMIN_KEYWORDS = [
    "人事異動", "晉升", "組織架構", "行事曆", "連續假期", "部位限制",
    "施工", "清洗", "webcast", "flyer", "公告", "通知",
]

# regex（依優先序）
RE_NAME_CODE = re.compile(r"([一-鿿]{2,10})\s*\((\d{4})\)")        # 鴻海(2317)
RE_CODE_NAME = re.compile(r"^(\d{4})\s*([一-鿿]{2,10})")            # 1102亞泥
RE_CODE_TT = re.compile(r"^(\d{4})\s+TT\b", re.IGNORECASE)                  # 1216 TT
RE_SOURCE_DATE = re.compile(r"-([A-Z]{2,8})(\d{8})")                        # -MS20240314

# 檔名日期（依信心序）。年份明確者才在此決定；只有 MMDD / 6 位數歧義者回 None，
# 交由檔案 mtime 補（見 mtime_report_date()／backfill／sync）。
RE_YMD = re.compile(r"(?<!\d)(20\d{2})[._\-/]?(\d{2})[._\-/]?(\d{2})(?!\d)")  # 2025_05_04 / 20250504 / 2025-05-04
RE_MDY8 = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")                # 03252025（MMDDYYYY）
RE_D6 = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")                    # 240510 / 093024（6 位數）


@dataclass
class FilenameMeta:
    file_name: str
    stock_code: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    report_date: Optional[date] = None
    report_type: Optional[str] = None
    is_admin: bool = False
    matched_patterns: list[str] = field(default_factory=list)


def _valid_ymd(y: int, m: int, d: int) -> bool:
    """是否為合法西元日期（月 1–12、日不超過該月天數）。"""
    if not (1 <= m <= 12):
        return False
    try:
        return 1 <= d <= calendar.monthrange(y, m)[1]
    except (ValueError, IndexError):
        return False


def _parse_date(stem: str) -> Optional[date]:
    """從檔名抽出版日；僅在年份明確時回日期，否則 None（歧義交內文補）。

    依信心序：YYYY[_-]MM[_-]DD（錨定 20XX，避開股票代碼前綴）→ MMDDYYYY →
    6 位數（YYMMDD/MMDDYY，唯一可解者採用，兩解皆有效視為歧義回 None）。
    """
    for m in RE_YMD.finditer(stem):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_ymd(y, mo, d):
            return date(y, mo, d)
    for m in RE_MDY8.finditer(stem):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_ymd(y, mo, d):
            return date(y, mo, d)
    for m in RE_D6.finditer(stem):
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # YYMMDD 與 MMDDYY 兩解；同一天（去重後僅一個）不算歧義。
        cands = {(2000 + a, b, c), (2000 + c, a, b)}
        valid = [t for t in cands if _valid_ymd(*t)]
        if len(valid) == 1:
            return date(*valid[0])
    return None


def mtime_report_date(
    mtime: Optional[date],
    created_at: Optional[date],
    *,
    copy_tolerance_days: int = 2,
) -> Optional[date]:
    """以檔案 mtime 推定出版日（檔名無日期時的全覆蓋補法，實測中位數僅差 1 天）。

    防呆：mtime 與 created_at（匯入時間）相差 ≤copy_tolerance_days 天時，視為「批次複製
    當下的時間戳」而非真實出版日 → 回 None（保守留空＝視為舊，避免把舊報告誤標成全新）。
    created_at 為 None（如即時匯入、無參照）時直接採用 mtime。
    """
    if mtime is None:
        return None
    if created_at is not None and abs((mtime - created_at).days) <= copy_tolerance_days:
        return None
    return mtime


def _detect_report_type(text: str) -> Optional[str]:
    low = text.lower()
    for kw in REPORT_TYPE_KEYWORDS:
        if kw.lower() in low:
            return kw
    return None


def _normalize_source(token: str) -> Optional[str]:
    return BROKER_MAP.get(token.upper()) or BROKER_MAP.get(token)


def is_admin_doc(file_name: str) -> bool:
    low = file_name.lower()
    return any(kw.lower() in low for kw in ADMIN_KEYWORDS)


def parse_filename(file_name: str) -> FilenameMeta:
    meta = FilenameMeta(file_name=file_name)
    stem = re.sub(r"\.(pdf|docx|doc|jpg|jpeg|png)$", "", file_name, flags=re.IGNORECASE)

    meta.is_admin = is_admin_doc(file_name)

    if (m := RE_NAME_CODE.search(stem)):
        meta.company_name, meta.stock_code = m.group(1), m.group(2)
        meta.matched_patterns.append("NAME_CODE")
    elif (m := RE_CODE_NAME.match(stem)):
        meta.stock_code, meta.company_name = m.group(1), m.group(2)
        meta.matched_patterns.append("CODE_NAME")
    elif (m := RE_CODE_TT.match(stem)):
        meta.stock_code = m.group(1)
        meta.matched_patterns.append("CODE_TT")

    if (m := RE_SOURCE_DATE.search(stem)):
        src = _normalize_source(m.group(1))
        if src:
            meta.source = src
            meta.matched_patterns.append("SOURCE_DATE")

    if meta.source is None:
        for token, name in BROKER_MAP.items():
            # 拉丁券商代碼（MS/GS/DW…）以詞邊界比對，避免子字串誤判：裸 "MS" 會命中
            # "MSCI"/"MSFT"/"EMS"/"Memory" 等（曾使多篇研報被錯標 morgan_stanley）。
            # 外資代碼的精確錨點是上方 RE_SOURCE_DATE（-MS20240314）；此處保留 -MS-/-MS<6碼>
            # 等詞邊界形式。CJK 券商名為多字、distinctive，維持子字串比對。
            if token.isascii():
                hit = re.search(
                    rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", stem
                )
            else:
                hit = token in stem
            if hit:
                meta.source = name
                meta.matched_patterns.append("BROKER_TOKEN")
                break

    meta.report_date = _parse_date(stem)
    meta.report_type = _detect_report_type(stem)
    return meta


# ── 內文券商來源偵測 ──────────────────────────────────────────────────────────
# 語料中約 67% 研報檔名不帶券商 token（如 daily story_/Takeaway_/速報/國際金融市場焦點），
# 但內文一定載明發行機構。以高精度「發行機構指紋」補 source。
#
# 關鍵：指紋只用「發行者自我指稱」形式——自有研究網站 URL、著作權／免責聲明、圖表
# 「資料來源：…投顧」自我標註，以及機構簡稱「X投顧」。**不可**用裸券商公司名「X證券」，
# 因彙整型研報（如凱基 Taiwan daily/daily story）常「提及」競爭對手主辦的法說會
# （「參加國票綜合證券舉辦之法說會」「永豐金證券…」），裸名會誤判（曾使 161 篇凱基研報
# 被誤標為對手）。發行者指稱自己用「投顧」，提及他人主辦則用「證券」——以此切分。
#
# 偵測採「最早指紋優先」：發行機構自我指稱位於報頭（char≈0），他人提及在內文深處，
# 故位置最前者即為發行者。CJK＋本土自有 URL 同視窗（4000，含前數頁圖表標註）；外資
# 拉丁指紋只掃表頭（2500）且詞邊界比對（避免 'ubs' 命中 'substrates'），且僅在無本土
# 發行機構指紋時才採（保護本土晨報「提及」外資估值的情形）。指紋字串一律小寫存放
# （比對前整段轉小寫；CJK 不受 lower() 影響）。
# 指紋亦含「X期貨」「X金融控股」等子公司自我指稱：CFTC 籌碼快報／債券雙週報／盤後快訊
# 等大宗格式由券商的期貨子公司發行，報頭自稱「本簡報由凱基期貨股份有限公司編製」「永豐期貨
# 股份有限公司」「元大期貨法人總經講座」，而非「X投顧」。這些仍是發行者自我指稱（非裸公司名
# 提及），且映射到與「X投顧」相同的母券商，故同樣安全；最早指紋優先規則保證報頭自稱勝過內文
# 提及。實測補回 375 篇 NULL，且修正 2 篇被檔名「MS」誤命中「MSCI」而錯標 morgan_stanley 者。
CONTENT_SIGNATURES_CJK: list[tuple[str, list[str]]] = [
    ("kgi", ["凱基投顧", "kgisia", "kgi凱基", "凱基期貨", "凱基金融控股", "凱基金控"]),
    ("masterlink", ["元富投顧", "masterlink", "元富期貨"]),
    ("sinopac", ["永豐晨訊", "永豐證券投資顧問", "永豐金證券投資顧問", "永豐投顧", "永豐期貨"]),
    ("capital", ["群益投顧", "群益證券投資顧問", "群益期貨"]),
    ("cathay", ["國泰證券投資顧問", "國泰綜合證券股份", "國泰期貨"]),
    ("fubon", ["富邦投顧", "富邦期貨"]),
    ("mega", ["兆豐證券投資顧問", "兆豐投顧", "兆豐期貨"]),
    ("president", ["統一投顧", "統一綜合證券股份", "統一期貨"]),
    ("jihsun", ["日盛投顧", "日盛證券投資顧問", "日盛期貨"]),
    ("first", ["第一金投顧", "第一金證券投資顧問", "第一金期貨"]),
    ("ibf", ["國票證券投資顧問", "國票投顧", "國票期貨"]),
    ("citic", ["中信投顧", "中國信託綜合證券股份", "中信期貨"]),
    ("yuanta", ["元大投顧", "元大期貨"]),
    ("hongyuan", ["宏遠投顧"]),
    ("haitong", ["海通國際"]),
]
CONTENT_SIGNATURES_LATIN: list[tuple[str, list[str]]] = [
    ("morgan_stanley", ["morgan stanley"]),
    ("goldman_sachs", ["goldman sachs"]),
    ("jpmorgan", ["j.p. morgan", "jpmorgan"]),
    ("ubs", ["ubs ag", "ubs securities", "ubs limited"]),
    ("nomura", ["nomura"]),
    ("macquarie", ["macquarie"]),
    ("daiwa", ["daiwa"]),
    ("clsa", ["clsa"]),
    ("citi", ["citigroup", "citi research", "citivelocity"]),
    ("bofa", ["bofa securities", "merrill lynch", "bofaml"]),
    ("hsbc", ["hsbc"]),
    ("jefferies", ["jefferies"]),
]
_LATIN_SIG_RE: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        name,
        [re.compile(r"(?<![a-z])" + re.escape(m) + r"(?![a-z])") for m in markers],
    )
    for name, markers in CONTENT_SIGNATURES_LATIN
]

# 本土發行機構指紋掃前 4000 字（含前數頁圖表自我標註）；外資拉丁只掃前 2500 字（防提及）。
CJK_SIG_WINDOW = 4000
LATIN_SIG_WINDOW = 2500


def _detect_issuer(full_text: str, window: int) -> Optional[str]:
    """本土發行機構指紋（含自有 URL），最早出現者勝（報頭＝發行者，內文深處＝提及）。"""
    head = full_text[:window].lower()
    best: Optional[str] = None
    best_pos = len(head) + 1
    for name, markers in CONTENT_SIGNATURES_CJK:
        for mk in markers:
            i = head.find(mk)
            if 0 <= i < best_pos:
                best_pos, best = i, name
    return best


def _detect_foreign(full_text: str, window: int) -> Optional[str]:
    """外資券商指紋：表頭限定 + 詞邊界比對（避免內文提及與子字串誤判）。"""
    head = full_text[:window].lower()
    best: Optional[str] = None
    best_pos = len(head) + 1
    for name, patterns in _LATIN_SIG_RE:
        for pattern in patterns:
            if (m := pattern.search(head)) and m.start() < best_pos:
                best_pos, best = m.start(), name
    return best


def extract_source_from_text(
    full_text: Optional[str],
    *,
    cjk_window: int = CJK_SIG_WINDOW,
    latin_window: int = LATIN_SIG_WINDOW,
) -> Optional[str]:
    """從報告內文偵測發行券商（正規化 source 名），無高精度指紋則回 None。

    與 parse_filename().source 互補：檔名沒帶券商時的補法。本土發行機構指紋（最早出現者）
    優先於外資（拉丁）提及。只用發行者自我指稱形式，故安全。
    """
    if not full_text:
        return None
    return _detect_issuer(full_text, cjk_window) or _detect_foreign(
        full_text, latin_window
    )


def resolve_source(
    file_name: str,
    full_text: Optional[str],
    *,
    cjk_window: int = CJK_SIG_WINDOW,
    latin_window: int = LATIN_SIG_WINDOW,
) -> Optional[str]:
    """決定一篇研報的來源券商（backfill / sync / 校正共用的單一真相）。

    優先序：本土發行機構內文指紋 → 檔名券商 token → 外資內文指紋 → None。
    本土發行機構指紋（著作權／投顧自稱／自有 URL）置於檔名之前，是為了校正檔名把
    「標的公司」誤當券商的情形（如「2882國泰金…-報告.pdf」實為元富投顧發行 →
    檔名解析成 cathay，內文指紋正確判為 masterlink）。
    """
    issuer = _detect_issuer(full_text or "", cjk_window)
    if issuer:
        return issuer
    fn = parse_filename(file_name).source
    if fn:
        return fn
    return _detect_foreign(full_text or "", latin_window)
