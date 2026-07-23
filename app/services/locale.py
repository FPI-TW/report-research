"""M10 雙語（zh-Hant / en）：輸出語言解析與系統提示語言覆寫。

設計原則：
- **檢索與證據一律保留原文**——只有『輸出給使用者的文字』隨 locale 切換。
- **fail-open 到 `DEFAULT_LOCALE`（zh-Hant）**：未帶/未知/無法解析的 locale 一律回中文，
  確保未接前端（M10b）前的既有行為零回歸。
- **預設 locale 不改動任何既有 prompt**：`output_directive("zh-Hant")` 回空字串，
  系統提示一字不動；只有 en 才在提示尾端附加覆寫指令。

本模組刻意保持領域無關（不含任何問答/研報專屬字串），供 answer.py 與 report.py 共用。
"""
from __future__ import annotations

DEFAULT_LOCALE = "zh-Hant"
SUPPORTED_LOCALES = ("zh-Hant", "en")


def resolve_locale(raw: str | None) -> str:
    """把請求帶入的 locale 正規化到支援集合；無法解析 → 預設中文（fail-open）。

    寬鬆比對常見寫法（大小寫、`_`/`-`、BCP-47 子標籤），任何不認得的值一律回
    `DEFAULT_LOCALE`，絕不拋錯——locale 永遠不該擋下作答。
    """
    if not raw:
        return DEFAULT_LOCALE
    v = raw.strip().lower().replace("_", "-")
    if v == "en" or v.startswith("en-"):
        return "en"
    # zh、zh-hant、zh-tw、zh-hk 等一律視為繁體中文輸出
    if v == "zh" or v.startswith("zh"):
        return "zh-Hant"
    return DEFAULT_LOCALE


def is_english(locale: str) -> bool:
    """是否輸出英文（唯一非預設分支）。"""
    return locale == "en"


# 系統提示語言覆寫指令：附加於系統提示尾端，覆寫其中『一律用繁體中文』等內建指示。
# 刻意要求保留專有名詞與引用證據原文（證據不翻譯），並固定 [n]／sentinel 不變，
# 避免破壞 EXT_SOURCES 解析與 [n] 引用比對。
_OUTPUT_DIRECTIVE = {
    "en": (
        "\n\nIMPORTANT — OUTPUT LANGUAGE OVERRIDE: Regardless of any instruction "
        "above to answer in Chinese, write your entire response in fluent English. "
        "Keep source titles, company names, tickers, and other proper nouns in "
        "their original language, and do not translate quoted evidence. Keep all "
        "citation markers (e.g. [1], [2]) and any required sentinel tokens (such "
        "as [EXT_SOURCES]) exactly as specified; for web-sourced points use the "
        "suffix (web) instead of （網路）."
    ),
}


def output_directive(locale: str) -> str:
    """回傳要附加到系統提示尾端的語言覆寫指令；預設中文回空字串（零回歸）。"""
    return _OUTPUT_DIRECTIVE.get(locale, "")


def pick(locale: str, zh: str, en: str) -> str:
    """在地化固定字串挑選器；非 en 一律回中文（fail-open）。"""
    return en if locale == "en" else zh
