"""M1b 研報評測指標：全部為確定性純函式（來源清單 + Markdown 結構 + 引用編號）。

不使用 LLM 判斷——「數值主張支持率」等 claim 級真偽屬 M8 grounding。
關鍵詞比對一律經 norm_for_match 正規化（NFKC、去空白、小寫），與檢索字面路徑同一套規則。
RULESET_VERSION 隨評分規則凍結：任何指標定義變更都必須遞增版本並重跑基準線。
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.textnorm import norm_for_match  # noqa: E402

RULESET_VERSION = 1

# 基準線最低有效題數：非 error 的正常題（no_data 除外）少於此數時，基準線不得用於比較
MIN_VALID_QUESTIONS = 6

# REPORT_SYSTEM_PROMPT 固定骨架的五個章節（## 級）
REQUIRED_SECTIONS = ("執行摘要", "關鍵發現", "重點分析", "風險與展望", "引用來源")

# 引用來源／外部參考屬列表式編號區，不得計入正文引用指標
_REFERENCE_SECTION_NAMES = ("引用來源", "外部參考")

_H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$", re.MULTILINE)
_H12_RE = re.compile(r"^#{1,2}(?!#)\s*(.+?)\s*$", re.MULTILINE)
_CITE_RE = re.compile(r"\[(\d+)\]")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_LINK_LINE_RE = re.compile(r"^\s*-\s*\[[^\]]+\]\(https?://[^)\s]+\)", re.MULTILINE)


def facet_coverage(facets, context: str) -> dict | None:
    """檢索層子題覆蓋率：面向命中 = 任一 keyword 正規化後出現在檢索脈絡。

    分母 = len(facets)；facets 空/None（no_data 題）→ None（不計均值）。
    """
    if not facets:
        return None
    ctx_norm = norm_for_match(context or "")
    missing: list[str] = []
    covered = 0
    for f in facets:
        kws = [norm_for_match(k) for k in (f.get("keywords") or [])]
        kws = [k for k in kws if k]
        if kws and ctx_norm and any(k in ctx_norm for k in kws):
            covered += 1
        else:
            missing.append(f.get("name", ""))
    total = len(facets)
    return {
        "covered": covered,
        "total": total,
        "rate": covered / total,
        "missing": missing,
    }


def source_diversity(sources: list[dict], brokers: list | None = None) -> dict:
    """來源多樣性：去重報告數、去重市場數、去重券商數（brokers=None＝查詢失敗→未知）。"""
    n_reports = len({s.get("report_id") for s in sources if s.get("report_id")})
    n_markets = len({s.get("market") for s in sources if s.get("market")})
    n_brokers = (
        None if brokers is None else len({b for b in brokers if b})
    )
    return {"n_reports": n_reports, "n_markets": n_markets, "n_brokers": n_brokers}


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def date_diversity(sources: list[dict]) -> dict:
    """日期多樣性：日期跨距（天）、去重年月數、無日期來源數；全部無日期 → span/months None。"""
    dates = [_parse_date(s.get("report_date")) for s in sources]
    known = [d for d in dates if d is not None]
    n_undated = len(dates) - len(known)
    if not known:
        return {"date_span_days": None, "n_months": None, "n_undated": n_undated}
    return {
        "date_span_days": (max(known) - min(known)).days,
        "n_months": len({(d.year, d.month) for d in known}),
        "n_undated": n_undated,
    }


def _strip_reference_sections(markdown: str) -> str:
    """剔除「引用來源」「外部參考」節（## 級起始，至下一個 #/## 標題或 EOF）。"""
    if not markdown:
        return ""
    headings = list(_H12_RE.finditer(markdown))
    drop: list[tuple[int, int]] = []
    for i, m in enumerate(headings):
        title = m.group(1)
        if any(name in title for name in _REFERENCE_SECTION_NAMES):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
            drop.append((m.start(), end))
    if not drop:
        return markdown
    out: list[str] = []
    pos = 0
    for start, end in drop:
        out.append(markdown[pos:start])
        pos = end
    out.append(markdown[pos:])
    return "".join(out)


def _extract_section(markdown: str, name: str) -> str | None:
    """取出標題含 name 的 ## 節內文；不存在 → None。"""
    headings = list(_H12_RE.finditer(markdown or ""))
    for i, m in enumerate(headings):
        if name in m.group(1):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
            return markdown[m.end():end]
    return None


def section_coverage(markdown: str) -> dict:
    """生成層章節覆蓋率：五個固定骨架章節（## 級標題文字包含即命中）。分母恆為 5。"""
    titles = _H2_RE.findall(markdown or "")
    missing = [s for s in REQUIRED_SECTIONS if not any(s in t for t in titles)]
    covered = len(REQUIRED_SECTIONS) - len(missing)
    return {
        "covered": covered,
        "total": len(REQUIRED_SECTIONS),
        "rate": covered / len(REQUIRED_SECTIONS),
        "missing": missing,
    }


def citation_metrics(markdown: str, n_sources: int) -> dict:
    """正文引用指標。正文 = 全文剔除引用來源/外部參考節與 ``` 圍欄（圍欄內
    的 JSON 數值陣列如 [5] 會誤判為引用；KPI/chart 的 source 對應屬 M8）。

    citation_validity 分母 = 正文 [n] 總數（0 → None）；
    source_citation_rate 分母 = len(sources)（0 → None）。
    """
    body = _FENCE_RE.sub("", _strip_reference_sections(markdown or ""))
    nums = [int(m) for m in _CITE_RE.findall(body)]
    n_citations = len(nums)
    valid = [n for n in nums if 1 <= n <= n_sources]
    return {
        "n_citations": n_citations,
        "citation_validity": (len(valid) / n_citations) if n_citations else None,
        "source_citation_rate": (
            len(set(valid)) / n_sources if n_sources else None
        ),
    }


def external_labeling(markdown: str) -> dict:
    """外部來源標示一致性：正文用了（網路）標註 ⇔ 存在含連結行的「外部參考（網路）」節。

    分母 = 有用到網路的題（兩訊號皆無 → applicable False、score None，不入分母）。
    """
    body = _strip_reference_sections(markdown or "")
    used_web = "（網路）" in body
    ext = _extract_section(markdown or "", "外部參考")
    has_ext_section = bool(ext and _LINK_LINE_RE.search(ext))
    applicable = used_web or has_ext_section
    if not applicable:
        return {"applicable": False, "used_web": False,
                "has_ext_section": False, "score": None}
    return {
        "applicable": True,
        "used_web": used_web,
        "has_ext_section": has_ext_section,
        "score": 1.0 if (used_web and has_ext_section) else 0.0,
    }


def no_data_handled(*, error: str | None, n_sources: int, markdown: str | None) -> bool:
    """無資料題的安全行為（結構化可判部分）：

    - yield error（網搜關時的「找不到足夠資料」）→ 安全。
    - 有產出時：正文不得有無效 [n] 引用（憑空捏造編號）、網搜標示必須一致。
    內容層真偽（是否把弱相關研報當依據）屬 M8 與人工抽樣。
    """
    if error:
        return True
    if not markdown:
        return False
    cm = citation_metrics(markdown, n_sources)
    if cm["citation_validity"] is not None and cm["citation_validity"] < 1.0:
        return False
    el = external_labeling(markdown)
    if el["applicable"] and el["score"] == 0.0:
        return False
    return True


def _metric_mean(values: list) -> dict:
    vals = [v for v in values if v is not None]
    return {
        "mean": (sum(vals) / len(vals)) if vals else None,
        "n_valid": len(vals),
    }


def aggregate_cases(cases: list[dict]) -> dict:
    """聚合逐題結果：各指標均值僅計非 None、非 error 題；附各自 n_valid 與計數。"""
    ok = [c for c in cases if not c.get("error")]

    def rate_of(key: str) -> list:
        out = []
        for c in ok:
            v = c.get(key)
            out.append(v.get("rate") if isinstance(v, dict) else v)
        return out

    def diversity_of(key: str, sub: str) -> list:
        return [
            (c.get(key) or {}).get(sub) if isinstance(c.get(key), dict) else None
            for c in ok
        ]

    labeling = [
        (c.get("external_labeling") or {}).get("score")
        if isinstance(c.get("external_labeling"), dict) else None
        for c in ok
    ]
    handled = [
        (1.0 if c.get("no_data_handled") else 0.0)
        if c.get("no_data") and c.get("no_data_handled") is not None else None
        for c in ok
    ]
    n_valid_normal = sum(1 for c in ok if not c.get("no_data"))
    return {
        "ruleset_version": RULESET_VERSION,
        "n": len(cases),
        "n_errors": sum(1 for c in cases if c.get("error")),
        "n_no_data": sum(1 for c in cases if c.get("no_data")),
        "sufficient_n": n_valid_normal >= MIN_VALID_QUESTIONS,
        "facet_coverage": _metric_mean(rate_of("facet_coverage")),
        "section_coverage": _metric_mean(rate_of("section_coverage")),
        "citation_validity": _metric_mean(rate_of("citation_validity")),
        "source_citation_rate": _metric_mean(rate_of("source_citation_rate")),
        "external_labeling": _metric_mean(labeling),
        "no_data_handled": _metric_mean(handled),
        "n_reports": _metric_mean(diversity_of("source_diversity", "n_reports")),
        "n_brokers": _metric_mean(diversity_of("source_diversity", "n_brokers")),
        "n_markets": _metric_mean(diversity_of("source_diversity", "n_markets")),
        "date_span_days": _metric_mean(diversity_of("date_diversity", "date_span_days")),
        "n_months": _metric_mean(diversity_of("date_diversity", "n_months")),
    }
