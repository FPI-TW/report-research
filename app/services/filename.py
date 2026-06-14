"""檔名解析服務。

僅用於：分層抽樣分桶、輔助 metadata（股票代碼/券商/日期/報告類型）。
市場標籤本身由 Claude 讀 PDF 判定，不在此決定。
"""

from __future__ import annotations

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
    "fubon": "富邦",
    "sinopac": "永豐",
    "mega": "兆豐",
    "president": "統一",
    "jihsun": "日盛",
    "first": "第一金",
    "ibf": "國票",
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
RE_DATE8 = re.compile(r"(20\d{2})(\d{2})(\d{2})")


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


def _parse_date8(text: str) -> Optional[date]:
    m = RE_DATE8.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


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
            if token in stem:
                meta.source = name
                meta.matched_patterns.append("BROKER_TOKEN")
                break

    meta.report_date = _parse_date8(stem)
    meta.report_type = _detect_report_type(stem)
    return meta
