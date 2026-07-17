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

# v2（M7）：
# 1. 新增 evidence_link_coverage（逐節生成的證據連結覆蓋率）。單次生成題無
#    claim_evidence → 該指標 None，不入均值。
# 2. section_coverage 命中改要求「章節有實質內文」（原只認標題，空章節照樣 1.0）。
# 3. citation_metrics 新增 n_available：source_citation_rate 的分母改為「餵給模型的
#    證據總數」（未給則退回 n_sources ＝ v1 語義）。
# 2/3 會動到既有指標的數值 → v1 基準線不可直接比較，須以 v2 重跑。
RULESET_VERSION = 2

# 基準線最低有效題數：非 error 的正常題（no_data 除外）少於此數時，基準線不得用於比較
MIN_VALID_QUESTIONS = 6

# REPORT_SYSTEM_PROMPT 固定骨架的五個章節（## 級）
REQUIRED_SECTIONS = ("執行摘要", "關鍵發現", "重點分析", "風險與展望", "引用來源")

# 引用來源／外部參考屬列表式編號區，不得計入正文引用指標
_REFERENCE_SECTION_NAMES = ("引用來源", "外部參考")

_H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$", re.MULTILINE)
_H12_RE = re.compile(r"^#{1,2}(?!#)\s*(.+?)\s*$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.MULTILINE)
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


def _has_body(text: str) -> bool:
    """章節區間剔除所有標題行後，仍有非空白文字。"""
    return bool(_HEADING_LINE_RE.sub("", text or "").strip())


def section_coverage(markdown: str) -> dict:
    """生成層章節覆蓋率：五個固定骨架章節（## 級標題文字包含即命中）。分母恆為 5。

    v2：命中另要求該章「有實質內文」——章節區間（本 ## 標題到下一個 #/## 標題）
    剔除標題行後仍有非空白文字。只認標題會讓「## 執行摘要」下空無一字的殘報照樣
    拿 rate=1.0（逐節生成的節草稿耗盡即此形態），指標對最該抓的失效模式全盲。
    `## 重點分析` 下只掛 ### 子節時，子節內文落在同一區間內 → 仍算有內文。
    """
    md = markdown or ""
    headings = list(_H12_RE.finditer(md))
    filled: list[str] = []
    for i, m in enumerate(headings):
        if not m.group(0).lstrip().startswith("##"):
            continue  # 一級 # 標題不是章節
        end = headings[i + 1].start() if i + 1 < len(headings) else len(md)
        if _has_body(md[m.end():end]):
            filled.append(m.group(1))
    missing = [s for s in REQUIRED_SECTIONS if not any(s in t for t in filled)]
    covered = len(REQUIRED_SECTIONS) - len(missing)
    return {
        "covered": covered,
        "total": len(REQUIRED_SECTIONS),
        "rate": covered / len(REQUIRED_SECTIONS),
        "missing": missing,
    }


def evidence_link_coverage(claim_evidence) -> dict | None:
    """逐節生成的證據連結覆蓋率：有掛到 ≥1 個檢索證據的節數 / 總節數。

    claim_evidence 為 {section_position(str): [evidence_id, ...]}，僅逐節（sectioned）
    路徑產出：每個值是「該節針對性檢索到、可供引用的證據 id 集」。這量測「逐節管線
    是否為多數章節取到可據以撰寫的證據」（空集＝該節純框架/無檢索接地，屬弱點）。

    語義上限於「證據可用性」的結構訊號——引用是否真正支持主張屬 M8 grounding，不在此。
    單次生成路徑無 claim_evidence → None（不計均值，跨版本基準線不受影響）。
    """
    if not isinstance(claim_evidence, dict) or not claim_evidence:
        return None
    total = len(claim_evidence)
    linked = sum(
        1 for ids in claim_evidence.values() if isinstance(ids, list) and ids
    )
    return {"linked": linked, "total": total, "rate": linked / total}


def citation_metrics(
    markdown: str, n_sources: int, n_available: int | None = None
) -> dict:
    """正文引用指標。正文 = 全文剔除引用來源/外部參考節與 ``` 圍欄（圍欄內
    的 JSON 數值陣列如 [5] 會誤判為引用；KPI/chart 的 source 對應屬 M8）。

    n_sources ＝該份研報「引用來源」表的長度，即正文 [n] 的合法上界。
    citation_validity 分母 = 正文 [n] 總數（0 → None）。

    n_available（v2 新增）＝「餵給模型的證據總數」，source_citation_rate 的分母
    （未給 → 退回 n_sources，即 v1 語義，單次路徑不受影響）。逐節路徑的 n_sources
    是 render_citations 產出的『已被引用』表，拿它當分母會恆為 1.0 而失去訊號；
    真正的訊號是「可用證據有多少比例真的被引用」，故由呼叫端另傳共用帳本大小。
    分母 0 → None。

    先去圍欄再剔節：圍欄內若含行首「## 引用來源」字樣，反序會從圍欄中段
    誤砍到下一個標題、吃掉真正的正文引用。
    """
    body = _strip_reference_sections(_FENCE_RE.sub("", markdown or ""))
    nums = [int(m) for m in _CITE_RE.findall(body)]
    n_citations = len(nums)
    valid = [n for n in nums if 1 <= n <= n_sources]
    denom = n_available if n_available is not None else n_sources
    return {
        "n_citations": n_citations,
        "citation_validity": (len(valid) / n_citations) if n_citations else None,
        "source_citation_rate": (len(set(valid)) / denom if denom else None),
    }


def external_labeling(markdown: str) -> dict:
    """外部來源標示一致性：正文用了（網路）標註 ⇔ 存在含連結行的「外部參考（網路）」節。

    分母 = 有用到網路的題（兩訊號皆無 → applicable False、score None，不入分母）。
    """
    # 與 citation_metrics 相同，必須先移除圍欄再解析標題；chart/KPI JSON
    # 可包含看似 Markdown 標題的文字，不能讓它誤切正文或真正的外部參考節。
    clean_markdown = _FENCE_RE.sub("", markdown or "")
    body = _strip_reference_sections(clean_markdown)
    used_web = "（網路）" in body
    ext = _extract_section(clean_markdown, "外部參考")
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
    """聚合逐題結果：各指標均值僅計非 None、非 error 題；附各自 n_valid 與計數。

    error（runner 例外/逾時）與 report_error（generate_report 結構化婉拒）分開：
    前者計 n_errors 且整題排除；後者計 n_report_declined，no_data 題的婉拒屬
    安全形態（進 no_data_handled 分母），正常題的婉拒不灌入 sufficient_n 有效題數。
    """
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
    n_valid_normal = sum(
        1 for c in ok if not c.get("no_data") and not c.get("report_error")
    )
    return {
        "ruleset_version": RULESET_VERSION,
        "n": len(cases),
        "n_errors": sum(1 for c in cases if c.get("error")),
        "n_report_declined": sum(1 for c in ok if c.get("report_error")),
        "n_no_data": sum(1 for c in cases if c.get("no_data")),
        "sufficient_n": n_valid_normal >= MIN_VALID_QUESTIONS,
        "facet_coverage": _metric_mean(rate_of("facet_coverage")),
        "section_coverage": _metric_mean(rate_of("section_coverage")),
        "evidence_link_coverage": _metric_mean(rate_of("evidence_link_coverage")),
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
