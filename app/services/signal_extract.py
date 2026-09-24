"""觀點雷達訊號擷取：固定 JSON schema prompt + Python 端正規化/驗證（純函式）。

分工（對齊 tagging.py 精神）：**LLM 只依固定 schema 擷取數值與論點證據，
不判斷評等方向、不換算幣別；Python 負責正規化、驗證、判定擷取狀態、寫入。**
本模組零 DB、零 subprocess，完全可單元測試；批次 I/O 在 scripts/extract_signals.py。

輸出 → research.report_signal（一列＝一份研報 × 一個標的）。讀取雷達時不再呼叫 LLM，
所有差異由 app/services/radar/ 決定性計算。thesis stance 受控詞彙（STANCE_CONSTRUCTIVENESS）
是本模組與 radar/scale.py 的**共用契約**（一處為準，radar 端 import 此常數）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.services.llm_models import TASK_SIGNAL, resolve_model
from app.services.zh_hant import lookup_key, to_traditional

# 擷取 schema / prompt 版本；schema 或 prompt 一改就 bump（承載可追溯性、供重跑比較）
EXTRACTION_VERSION = "sig-2026-07-15.v1"

# 數值/證據擷取重準確度 → 預設 Sonnet（現代世代 sonnet-5；批次可用 --model 覆寫）。
# 來源是 SIGNAL_MODEL 旋鈕，未設時查 LLM_PROVIDER 的預設表（app/services/llm_models.py）。
SIGNAL_MODEL_DEFAULT = resolve_model(TASK_SIGNAL)

# 文字截斷上限（防模型暴走輸出整段）
RATING_RAW_MAX = 100
EVIDENCE_MAX = 300
SUMMARY_MAX = 200
RAW_TEXT_MAX = 4000  # rejected 時保留的原始回應長度

# ── 四維論點 stance 受控詞彙（維度特定）＋建設性序位（+1 樂觀 / 0 / −1）──
# 這是「擷取端 emit」與「服務端 radar/scale.py 解讀」的共用契約，改這裡兩邊同步。
# 風險維度以「升高 = 建設性下降」表達，避免 positive/negative 對風險的符號歧義。
THESIS_DIMENSIONS = ("outlook", "catalyst", "risk", "valuation")
STANCE_CONSTRUCTIVENESS: dict[str, dict[str, int]] = {
    "outlook": {"positive": 1, "neutral": 0, "negative": -1},
    "catalyst": {"positive": 1, "neutral": 0, "negative": -1},
    "valuation": {"attractive": 1, "fair": 0, "stretched": -1},
    "risk": {"easing": 1, "stable": 0, "rising": -1},  # 風險升高 → 建設性 −1
}

# 五級評等尺度（正規化目標）
RATING_LEVELS = ("buy", "overweight", "neutral", "underweight", "sell", "unknown")

# 精確原文 → 五級（strip+lower 後查）；未命中再走 RATING_KEYWORDS 子字串
RATING_MAP: dict[str, str] = {
    # buy
    "買進": "buy", "買入": "buy", "強力買進": "buy", "強烈買進": "buy",
    "逢低買進": "buy", "加碼": "buy", "buy": "buy", "strong buy": "buy", "add": "buy",
    # overweight
    "增持": "overweight", "加持": "overweight", "優於大盤": "overweight",
    "優於大市": "overweight", "優於同業": "overweight", "表現優於大盤": "overweight",
    "看好": "overweight", "區間偏多": "overweight", "overweight": "overweight",
    "accumulate": "overweight", "outperform": "overweight",
    # KGI「增加持股」/ 逢低承接類（實測補入，語意＝逢低加碼但非強力買進 → overweight）
    "增加持股": "overweight", "持股增加": "overweight", "逢低承接": "overweight",
    "逢低布局": "overweight",
    # neutral
    "中立": "neutral", "中性": "neutral", "持有": "neutral", "區間操作": "neutral",
    "區間整理": "neutral", "區間": "neutral", "符合大盤": "neutral",
    "與大盤同步": "neutral", "觀望": "neutral", "neutral": "neutral", "hold": "neutral",
    "market perform": "neutral", "in-line": "neutral", "equal-weight": "neutral",
    "equalweight": "neutral",
    # underweight
    "減碼": "underweight", "減持": "underweight", "劣於大盤": "underweight",
    "表現劣於大盤": "underweight", "區間偏空": "underweight",
    "underweight": "underweight", "reduce": "underweight", "underperform": "underweight",
    # sell
    "賣出": "sell", "賣": "sell", "強力賣出": "sell", "sell": "sell", "strong sell": "sell",
    # unknown（明確無評等）
    "未評等": "unknown", "無評等": "unknown", "暫不評等": "unknown", "待評": "unknown",
    "n/a": "unknown", "na": "unknown",
}

# 子字串關鍵字（處理「調升評等至買進」整句）；長鍵/特定詞排前，避免子字串誤命中。
# 「賣出」先於「賣」、「優於大盤」先於「買」。
RATING_KEYWORDS: list[tuple[str, str]] = [
    ("優於大盤", "overweight"), ("優於大市", "overweight"), ("劣於大盤", "underweight"),
    ("符合大盤", "neutral"), ("強力買進", "buy"), ("強力賣出", "sell"),
    ("區間偏多", "overweight"), ("區間偏空", "underweight"), ("區間操作", "neutral"),
    ("增加持股", "overweight"), ("逢低承接", "overweight"), ("逢低布局", "overweight"),
    ("買進", "buy"), ("買入", "buy"), ("增持", "overweight"), ("加碼", "buy"),
    ("減碼", "underweight"), ("減持", "underweight"), ("賣出", "sell"),
    ("中立", "neutral"), ("持有", "neutral"), ("觀望", "neutral"),
    ("outperform", "overweight"), ("underperform", "underweight"),
    ("strong buy", "buy"), ("strong sell", "sell"), ("buy", "buy"),
    ("overweight", "overweight"), ("underweight", "underweight"), ("sell", "sell"),
    ("neutral", "neutral"), ("hold", "neutral"),
]

# 幣別正規化：常見寫法 → ISO 代碼。**永不換算**，只是同義正規化；未知代碼原樣大寫回傳。
_CURRENCY_MAP: dict[str, str] = {
    "nt$": "TWD", "ntd": "TWD", "台幣": "TWD", "新台幣": "TWD", "新臺幣": "TWD",
    "元": "TWD", "twd": "TWD", "us$": "USD", "usd": "USD", "美元": "USD", "美金": "USD",
    "港元": "HKD", "港幣": "HKD", "hk$": "HKD", "hkd": "HKD",
    "人民幣": "CNY", "rmb": "CNY", "cny": "CNY", "¥": "CNY",
    "日圓": "JPY", "日元": "JPY", "jpy": "JPY", "歐元": "EUR", "eur": "EUR",
}


def normalize_rating(raw: object) -> str:
    """券商評等原文 → 五級代碼；未命中一律 'unknown'（不計入分布）。

    查表前先經 `zh_hant.lookup_key` 轉繁：`RATING_MAP`／`RATING_KEYWORDS` 只收繁體詞，
    原文是簡體（「买入」「减持」）或模型照抄成簡體時，不轉就一律落 unknown。轉換只用在
    查表，呼叫端存進 `rating_raw` 的仍是原值（逐字照抄研報是 prompt 規則 4 的要求）。
    """
    if not isinstance(raw, str):
        return "unknown"
    v = lookup_key(raw.strip()).lower()
    if not v:
        return "unknown"
    if v in RATING_MAP:
        return RATING_MAP[v]
    for kw, level in RATING_KEYWORDS:
        if kw in v:
            return level
    return "unknown"


def normalize_currency(raw: object) -> Optional[str]:
    """幣別寫法正規化為 ISO 代碼（不換算）；空值 → None；未知 → 原樣大寫。

    查表鍵先轉繁（「人民币」「港币」，同 `normalize_rating`）；查不到時回的是**原值**大寫，不是轉過的鍵。
    """
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    if not v:
        return None
    return _CURRENCY_MAP.get(lookup_key(v).lower(), v.upper())


def _truncate(value: object, limit: int) -> Optional[str]:
    """轉字串並截斷；空/非字串 → None。"""
    if not isinstance(value, str):
        if value is None:
            return None
        value = str(value)
    v = " ".join(value.split()).strip()
    if not v:
        return None
    return v[:limit]


def _coerce_float(value: object) -> Optional[float]:
    """容錯轉 float（去千分位逗號與貨幣符號）；失敗 → None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        for sym in ("NT$", "US$", "HK$", "$", "¥", "＄"):
            s = s.replace(sym, "")
        s = s.strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _coerce_year(value: object) -> Optional[int]:
    """FY 容錯轉整數年（接受 2026 / "2026" / "FY2026"）；失敗 → None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) >= 4:
            try:
                return int(digits[:4])
            except ValueError:
                return None
    return None


def eps_comparable(a: dict, b: dict) -> bool:
    """兩筆 EPS 是否可比：fiscal_year / period / currency / unit 全同才 True。"""
    keys = ("fiscal_year", "period", "currency", "unit")
    return all(a.get(k) == b.get(k) for k in keys)


# ── LLM 必須回傳的固定 JSON schema（供 prompt 與解析對齊）──
SIGNAL_INSTRUCTION = """你是金融研報結構化擷取助手。閱讀以下券商研報內文，針對指定的標的清單，
擷取每個標的的「評等、目標價、EPS 預估、四維論點」。

嚴格規則（違反會被系統丟棄）：
1. 只輸出「單一 JSON 物件」，不要任何說明文字、不要 markdown、不要程式碼圍欄。
2. 只能擷取研報「明確載明」的內容；研報未提到的欄位一律填 null（或空陣列），
   嚴禁推測、嚴禁編造、嚴禁用常識補值。
3. 每個數值與每個論點維度都要附 evidence（研報原句片段，一兩句即可）；
   附不出原文片段的，該欄位就填 null / 不列入。
4. 評等只「照抄研報原文」（如「買進」「區間操作」「Outperform」），
   不要替系統判斷買賣方向，方向由系統正規化。
5. 目標價與 EPS 保留研報「原本的幣別與單位」，**不要換算**成其他幣別。
6. 只針對「指定標的清單」中的代碼輸出；清單以外的代碼不要輸出。
   某標的研報中完全沒有可擷取內容時，該標的可整個省略。

JSON 格式：
{
  "signals": [
    {
      "instrument_code": "<4 碼代碼，須在指定清單內>",
      "rating": {"raw": "<研報原文評等或 null>", "evidence": "<原句或 null>"},
      "target_price": {"value": <數字或 null>, "currency": "<TWD/USD/HKD/CNY…>",
                        "horizon": "<如 12M/年底 或 null>", "evidence": "<原句或 null>"},
      "eps_estimates": [
        {"fiscal_year": <西元年整數>, "period": "<FY/1H/Q1…>", "currency": "<幣別>",
          "value": <數字>, "unit": "per_share", "evidence": "<原句>"}
      ],
      "thesis": {
        "outlook":   {"stance": "positive|neutral|negative", "summary": "<一句>", "evidence": "<原句>"},
        "catalyst":  {"stance": "positive|neutral|negative", "summary": "<一句>", "evidence": "<原句>"},
        "risk":      {"stance": "easing|stable|rising",       "summary": "<一句>", "evidence": "<原句>"},
        "valuation": {"stance": "attractive|fair|stretched",  "summary": "<一句>", "evidence": "<原句>"}
      }
    }
  ]
}

四維 stance 用詞固定（各維度不同）：
- outlook（展望）、catalyst（催化劑）：positive / neutral / negative
- risk（風險）：easing（趨緩）/ stable（持平）/ rising（升高）
- valuation（估值）：attractive（偏低）/ fair（合理）/ stretched（偏高）
任一維度找不到明確論點或附不出 evidence，就整個維度省略（不要硬填）。
"""


def build_signal_prompt(
    file_name: str,
    report_date: Optional[str],
    broker: Optional[str],
    requested_codes: list[str],
    body_excerpt: str,
) -> str:
    """組裝擷取 prompt（仿 generate_summaries.build_prompt）。"""
    codes = "、".join(requested_codes)
    meta = [f"檔名：{file_name}"]
    if report_date:
        meta.append(f"報告日：{report_date}")
    if broker:
        meta.append(f"券商：{broker}")
    meta.append(f"指定標的清單（只擷取這些代碼）：{codes}")
    header = "\n".join(meta)
    return (
        f"{SIGNAL_INSTRUCTION}\n\n"
        f"{header}\n\n"
        f"研報內文：\n{body_excerpt}\n\n"
        f"請依上述 schema 只輸出單一 JSON 物件。"
    )


@dataclass
class ParsedReportSignals:
    """parse_signal 的結果：容錯、不 raise。ok=False 代表整份 payload 無法解析。"""

    ok: bool
    signals: dict[str, dict] = field(default_factory=dict)  # instrument_code -> 原始 signal 物件
    raw_text: str = ""
    error: Optional[str] = None


@dataclass
class SignalRow:
    """對應 research.report_signal 一列（id 由批次腳本產生）。"""

    report_id: str
    market: str
    instrument_code: str
    broker: Optional[str]
    report_date: Optional[date]
    rating_raw: Optional[str]
    rating_normalized: str
    target_price: Optional[float]
    target_currency: Optional[str]
    target_horizon: Optional[str]
    target_price_evidence: Optional[str]
    eps_estimates: list[dict]
    thesis_dimensions: dict
    extraction_version: str
    extraction_status: str  # valid | partial | rejected
    raw_payload: Optional[dict]
    error_detail: Optional[str]


@dataclass
class ReportContext:
    """一份研報的固定 metadata（非 LLM 擷取，來自 research_report）。"""

    report_id: str
    market: str
    broker: Optional[str]
    report_date: Optional[date]
    requested_codes: list[str]


def parse_signal(raw: str, requested_codes: list[str]) -> ParsedReportSignals:
    """容錯解析 LLM 回應：去圍欄、抓首個 '{' 到末個 '}'。

    解析不出合法 JSON / 缺 signals 陣列 → ok=False（**不 raise**），保留原文供 rejected。
    signals 依 instrument_code 索引，只保留在 requested_codes 內的代碼。
    """
    raw_text = (raw or "")[:RAW_TEXT_MAX]
    if not raw or not raw.strip():
        return ParsedReportSignals(ok=False, raw_text=raw_text, error="空回應")
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ParsedReportSignals(ok=False, raw_text=raw_text, error="找不到 JSON 物件")
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError as exc:
        return ParsedReportSignals(ok=False, raw_text=raw_text, error=f"JSON 解析失敗：{exc}")
    if not isinstance(obj, dict):
        return ParsedReportSignals(ok=False, raw_text=raw_text, error="頂層非物件")
    arr = obj.get("signals")
    if not isinstance(arr, list):
        return ParsedReportSignals(ok=False, raw_text=raw_text, error="缺 signals 陣列")

    wanted = set(requested_codes)
    signals: dict[str, dict] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        code = str(item.get("instrument_code", "")).strip()
        if code in wanted and code not in signals:
            signals[code] = item
    return ParsedReportSignals(ok=True, signals=signals, raw_text=raw_text)


def _normalize_target(obj: object) -> tuple[Optional[dict], list[str]]:
    """正規化 target_price 子物件 → (dict 或 None, dropped 原因清單)。"""
    notes: list[str] = []
    if not isinstance(obj, dict):
        return None, notes
    price = _coerce_float(obj.get("value"))
    if price is None:
        # 有 target 物件但無有效數字 → 視為缺目標價（不算 drop，除非物件有其他內容）
        if any(obj.get(k) for k in ("currency", "horizon", "evidence")):
            notes.append("target_price 無有效數字")
        return None, notes
    currency = normalize_currency(obj.get("currency"))
    evidence = _truncate(obj.get("evidence"), EVIDENCE_MAX)
    if currency is None:
        notes.append("target_price 缺幣別")  # 下游不會併入跨券商中位數
    if evidence is None:
        notes.append("target_price 缺 evidence")
    return {
        "value": price,
        "currency": currency,
        "horizon": _truncate(obj.get("horizon"), 40),
        "evidence": evidence,
    }, notes


def _normalize_eps(arr: object) -> tuple[list[dict], list[str]]:
    """正規化 eps_estimates 陣列 → (合格清單, dropped 原因清單)。

    每筆須 fiscal_year/period/currency/value/unit 齊全才保留；缺任一 → 丟該筆。
    """
    out: list[dict] = []
    notes: list[str] = []
    if not isinstance(arr, list):
        return out, notes
    dropped = 0
    for item in arr:
        if not isinstance(item, dict):
            dropped += 1
            continue
        fy = _coerce_year(item.get("fiscal_year"))
        period = _truncate(item.get("period"), 16)
        currency = normalize_currency(item.get("currency"))
        value = _coerce_float(item.get("value"))
        unit = _truncate(item.get("unit"), 32) or "per_share"
        if fy is None or period is None or currency is None or value is None:
            dropped += 1
            continue
        out.append(
            {
                "fiscal_year": fy,
                "period": period,
                "currency": currency,
                "unit": unit,
                "value": value,
                "evidence": _truncate(item.get("evidence"), EVIDENCE_MAX),
            }
        )
    if dropped:
        notes.append(f"eps 丟棄 {dropped} 筆（缺欄）")
    return out, notes


def _normalize_thesis(obj: object) -> tuple[dict, list[str]]:
    """正規化 thesis 四維 → (dict, dropped 原因清單)。

    每維 stance 須在該維度受控詞彙內、且 evidence 非空才保留；否則刪該維。
    """
    out: dict = {}
    notes: list[str] = []
    if not isinstance(obj, dict):
        return out, notes
    for dim in THESIS_DIMENSIONS:
        cell = obj.get(dim)
        if not isinstance(cell, dict):
            continue
        stance = cell.get("stance")
        stance = stance.strip().lower() if isinstance(stance, str) else None
        evidence = _truncate(cell.get("evidence"), EVIDENCE_MAX)
        allowed = STANCE_CONSTRUCTIVENESS[dim]
        if stance not in allowed:
            if stance or cell.get("summary") or evidence:
                notes.append(f"thesis:{dim} stance 不合法")
            continue
        if evidence is None:
            notes.append(f"thesis:{dim} 缺 evidence")  # 未取得足夠證據 → 不採（設計規格）
            continue
        # summary 是 LLM 自己的一句話轉述 → 轉繁體（prompt 要繁體，但那是機率性保證）。
        # evidence 是研報原句，刻意不轉：原文若是簡體，證據就該是簡體才對得回去。
        # 轉換在截長之前（同 generate_summaries / extract_takeaways）：讓存下來的
        # 字串就是 SUMMARY_MAX 所描述的那一個。
        raw_summary = cell.get("summary")
        if isinstance(raw_summary, str):
            raw_summary = to_traditional(raw_summary)
        out[dim] = {
            "stance": stance,
            "summary": _truncate(raw_summary, SUMMARY_MAX),
            "evidence": evidence,
        }
    return out, notes


def _with_model(payload: dict, model: Optional[str]) -> dict:
    """raw_payload 加上產出模型（`model`）；未知（None）就不加鍵。不改動傳入的 dict。

    `model` 是**實際產出**這份回應的模型：HTTP 路徑取回應的 `model` 欄，CLI 路徑退回請求的
    model（由批次決定，見 scripts/extract_signals.py）。同名鍵以 Python 記的為準。
    """
    out = dict(payload)
    if model:
        out["model"] = model
    return out


def _build_one_row(ctx: ReportContext, code: str, signal: dict, model: Optional[str] = None) -> SignalRow:
    """把單一標的的原始 signal 物件正規化成 SignalRow（判 valid/partial）。"""
    rating_obj = signal.get("rating") if isinstance(signal.get("rating"), dict) else {}
    rating_raw = _truncate(rating_obj.get("raw"), RATING_RAW_MAX)
    rating_normalized = normalize_rating(rating_raw)

    target, t_notes = _normalize_target(signal.get("target_price"))
    eps, e_notes = _normalize_eps(signal.get("eps_estimates"))
    thesis, th_notes = _normalize_thesis(signal.get("thesis"))
    notes = t_notes + e_notes + th_notes

    has_usable = (
        rating_normalized != "unknown"
        or target is not None
        or len(eps) > 0
        or len(thesis) > 0
    )
    # 有可用資訊且沒有任何子結構被丟棄 → valid；否則 partial（含全空）。
    status = "valid" if (has_usable and not notes) else "partial"
    if not has_usable and not notes:
        notes.append("無可擷取內容")

    return SignalRow(
        report_id=ctx.report_id,
        market=ctx.market,
        instrument_code=code,
        broker=ctx.broker,
        report_date=ctx.report_date,
        rating_raw=rating_raw,
        rating_normalized=rating_normalized,
        target_price=target["value"] if target else None,
        target_currency=target["currency"] if target else None,
        target_horizon=target["horizon"] if target else None,
        target_price_evidence=target["evidence"] if target else None,
        eps_estimates=eps,
        thesis_dimensions=thesis,
        extraction_version=EXTRACTION_VERSION,
        extraction_status=status,
        raw_payload=_with_model(signal, model),  # 保留原始 per-instrument 物件供追溯
        error_detail="；".join(notes) if notes else None,
    )


def _rejected_row(
    ctx: ReportContext, code: str, parsed: ParsedReportSignals, model: Optional[str] = None
) -> SignalRow:
    """整份 payload 無法解析 → 該標的 rejected 列（保留原文，不阻塞其他報告）。"""
    return SignalRow(
        report_id=ctx.report_id,
        market=ctx.market,
        instrument_code=code,
        broker=ctx.broker,
        report_date=ctx.report_date,
        rating_raw=None,
        rating_normalized="unknown",
        target_price=None,
        target_currency=None,
        target_horizon=None,
        target_price_evidence=None,
        eps_estimates=[],
        thesis_dimensions={},
        extraction_version=EXTRACTION_VERSION,
        extraction_status="rejected",
        raw_payload=_with_model({"raw_text": parsed.raw_text}, model),
        error_detail=parsed.error or "無法解析",
    )


def build_rows(
    ctx: ReportContext, parsed: ParsedReportSignals, model: Optional[str] = None
) -> list[SignalRow]:
    """對 ctx.requested_codes 逐一產列。payload 解析失敗 → 全部 rejected；
    否則逐標的正規化，requested 但 LLM 未回傳者 → partial 空列。

    `model`：產出這份回應的模型，記進每列 `raw_payload.model`；沒有回應（全程逾時）時為 None。
    """
    rows: list[SignalRow] = []
    for code in ctx.requested_codes:
        if not parsed.ok:
            rows.append(_rejected_row(ctx, code, parsed, model))
            continue
        # requested 但 LLM 未回傳 → 空物件走正規化，會判為「無可擷取內容」partial（非 rejected）
        signal = parsed.signals.get(code) or {}
        rows.append(_build_one_row(ctx, code, signal, model))
    return rows
