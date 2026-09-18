"""抽取層評測：對 golden set 算 docs/EXTRACTION.md §8 的四項指標。

對應 §4.1 `E0` 的交付。**唯讀**：不寫 DB、不動 data/extracted、不呼叫 LLM。

用法：
    uv run python scripts/eval_extraction.py --extractor pypdf \
        --out eval/baselines/extraction-pypdf-2026-09-02.json
    uv run python scripts/eval_extraction.py --extractor pdfplumber --out eval/extraction-cand.json
    make eval-compare BASE=eval/baselines/extraction-pypdf-2026-09-02.json CAND=eval/extraction-cand.json

## 四項指標怎麼算（與 §5 對齊）

1. **閱讀順序**（主指標）。golden 每份標 8–15 條原文句子與紙本上的正確先後。
   - `order_hit_rate`：句子經 `norm_for_match` 後在抽取文字裡找得到的比例。
   - `order_pair_acc`：命中句子兩兩配對，在抽取文字裡先後與 golden 一致的配對比例。
   - `order_kendall_tau`：同一組配對的 (一致 − 不一致) ÷ 總數，[-1, 1]。
   命中用**首次出現位置**。句子在原文出現多次時（表頭、重複的小標）位置會有歧義，
   golden 標註時就該避開這種句子；腳本不猜、照首次算。
   **比對前會把 `|` 拿掉**：版面抽取器把表格序列化成 markdown（`| a | b |`），
   golden 裡取自表格列的片段（元大早報的報告清單、高盛的 Key Data）在正規化後
   會因為儲存格分隔符而對不上。分隔符是序列化的產物不是版面順序的資訊，
   閱讀順序指標不該被它扣分；pypdf 的輸出沒有 `|`，拿掉對它是 no-op。

2. **欄位 coverage**：golden 有值的（評等、目標價）裡，抽取結果也有值的比例。
3. **欄位 accuracy**：抽取結果有值的裡，值正確的比例。目標價容差 0.5%，評等比
   正規化字串。**coverage 與 accuracy 必須分開看**：只看前者獎勵亂猜，只看後者
   獎勵什麼都不填。
4. **幻覺率**：`target_price_evidence` 用 `locate_quote()` 錨回抽取文字，
   **`Anchor.method == "prefix"` 一律算錨不回**（理由見 §4.3：前綴層尾巴不驗，
   而目標價幾乎總在句尾，實測 75.5% 的竄改數值會被前綴層放行）。

欄位層的「抽取結果」在 `E0` 階段來自 **DB 裡現況 `report_signal`**（`--signals db`），
也就是「pypdf 全文 → `signal_extract` 一次 16000 字」這條現況管線的產物；`E4` 就位後
改接新的擷取器輸出。抽取器（`--extractor`）只影響閱讀順序、幻覺率的錨定基準與
文字健檢，**不影響欄位層的值**——兩者分屬 `E1` 與 `E4` 兩個里程碑，刻意分開歸因。

## 標註狀態與樣本口徑

golden set 每筆帶 `annotation_status`：
- `prefilled`：只有 DB 預填的欄位，順序層是空的 → 順序指標略過、欄位層照算。
- `draft`：順序層與人工欄位由模型看頁面圖片草標，**尚未經人覆核**。
- `reviewed`：人工覆核過。

預設三種都吃並在 summary 分開計數；`--reviewed-only` 只吃覆核過的。`n_reviewed`
進 `scripts/eval_compare.py` 的可比性判定：兩份的覆核樣本數不同就不給結論。

## 文字召回率不是主指標

`chars_nows`（去空白字元數）只當健檢：大幅下降＝新抽取器在吃字（如 pypdf layout
模式那樣少 52–87%）。原始長度會被兩個抽取器的空白習慣淹掉，所以一律去空白後算。
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.reading.anchor import locate_quote  # noqa: E402
from app.services.textnorm import clean_extracted, norm_for_match  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "eval" / "extraction_dataset.json"

logger = logging.getLogger("eval_extraction")
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

EXTRACTORS = ("pypdf", "pdfplumber")
# 目標價容差：券商同一份報告內的目標價常見 NT$430 與 430.0 之差，0.5% 吸收四捨五入。
TP_REL_TOL = 0.005
STATUSES = ("prefilled", "draft", "reviewed")


# ── 抽取 ────────────────────────────────────────────────────────────────────


def extract(path: Path, extractor: str) -> tuple[str, dict[str, Any]]:
    """回傳 (抽取文字, 診斷)。pypdf 走現況生產路徑；pdfplumber 走 spike 的版面層。"""
    if extractor == "pypdf":
        from app.services.extract import extract_text

        res = extract_text(path)
        return res.text, {"scanned": res.scanned, "error": res.error}
    if extractor == "pdfplumber":
        from app.services.extraction.layout import extract_document
        from app.services.extraction.model import serialize

        doc = extract_document(path)
        return serialize(doc), {
            "pages_failed": list(doc.pages_failed),
            "error": doc.error,
            "extraction_version": doc.extraction_version,
        }
    raise ValueError(f"unknown extractor: {extractor}")


# ── 閱讀順序 ────────────────────────────────────────────────────────────────


@dataclass
class OrderResult:
    n_sentences: int
    n_hit: int
    n_pairs: int = 0
    n_concordant: int = 0
    n_pairs_within: int = 0
    n_concordant_within: int = 0
    misses: list[str] = field(default_factory=list)

    @property
    def hit_rate(self) -> float | None:
        return self.n_hit / self.n_sentences if self.n_sentences else None

    @property
    def pair_acc(self) -> float | None:
        return self.n_concordant / self.n_pairs if self.n_pairs else None

    @property
    def pair_acc_within(self) -> float | None:
        return self.n_concordant_within / self.n_pairs_within if self.n_pairs_within else None

    @property
    def kendall_tau(self) -> float | None:
        if not self.n_pairs:
            return None
        return (2 * self.n_concordant - self.n_pairs) / self.n_pairs


def _order_norm(s: str) -> str:
    """順序比對用的正規化：norm_for_match 再拿掉表格儲存格分隔符（見檔頭）。"""
    return norm_for_match(s).replace("|", "")


def score_order(text: str, items: list[dict]) -> OrderResult:
    """`items`：golden 的 `order` 清單，每筆 {text, stream?}。

    兩組配對分開算：全部配對（跨 stream 的先後採 golden 的「左到右、上到下」慣例）
    與**同一 stream 內**的配對（先後無歧義，是主指標裡最硬的那一塊）。"""
    ntext = _order_norm(text)
    hits: list[tuple[int, str]] = []
    misses: list[str] = []
    for it in items:
        s = it["text"]
        ns = _order_norm(s)
        pos = ntext.find(ns) if ns else -1
        if pos < 0:
            misses.append(s)
        else:
            hits.append((pos, it.get("stream") or "main"))
    res = OrderResult(n_sentences=len(items), n_hit=len(hits), misses=misses)
    for (a, sa), (b, sb) in combinations(hits, 2):
        res.n_pairs += 1
        ok = a < b
        if ok:
            res.n_concordant += 1
        if sa == sb:
            res.n_pairs_within += 1
            if ok:
                res.n_concordant_within += 1
    return res


# ── 欄位層 ──────────────────────────────────────────────────────────────────


def _norm_rating(s: Any) -> str:
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s)).strip().lower().replace(" ", "")


def _tp_equal(a: Any, b: Any) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= TP_REL_TOL * max(abs(fa), abs(fb), 1e-9)


@dataclass
class FieldResult:
    golden_rating: int = 0
    got_rating: int = 0
    ok_rating: int = 0
    golden_tp: int = 0
    got_tp: int = 0
    ok_tp: int = 0
    n_evidence: int = 0
    n_unanchored: int = 0
    n_prefix: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


def score_fields(golden_instruments: list[dict], signals: list[dict], canonical_text: str) -> FieldResult:
    """`golden_instruments`：golden 的標的清單，每筆 {code, rating?, target_price?}。
    `signals`：抽取結果（現況為 DB 的 report_signal 列），每筆 {instrument_code, rating_raw,
    target_price, target_price_evidence}。"""
    fr = FieldResult()
    by_code = {str(s.get("instrument_code")): s for s in signals}
    for g in golden_instruments:
        code = str(g.get("code"))
        s = by_code.get(code)
        d: dict[str, Any] = {"code": code}
        g_rating = g.get("rating")
        g_tp = (g.get("target_price") or {}).get("value") if isinstance(g.get("target_price"), dict) else None
        if g_rating:
            fr.golden_rating += 1
            got = s.get("rating_raw") if s else None
            d["rating"] = {"golden": g_rating, "got": got}
            if got:
                fr.got_rating += 1
                if _norm_rating(got) == _norm_rating(g_rating):
                    fr.ok_rating += 1
                    d["rating"]["ok"] = True
        if g_tp is not None:
            fr.golden_tp += 1
            got = s.get("target_price") if s else None
            d["target_price"] = {"golden": g_tp, "got": got}
            if got is not None:
                fr.got_tp += 1
                if _tp_equal(got, g_tp):
                    fr.ok_tp += 1
                    d["target_price"]["ok"] = True
        fr.details.append(d)

    # 幻覺率：對抽取結果裡**所有**帶 evidence 的訊號算，不限 golden 標的——
    # 模型對 golden 沒標的標的編出來的 evidence 也是幻覺。
    for s in signals:
        ev = s.get("target_price_evidence")
        if not ev:
            continue
        fr.n_evidence += 1
        anchor = locate_quote(canonical_text, ev)
        if anchor is None:
            fr.n_unanchored += 1
        elif anchor.method == "prefix":
            fr.n_unanchored += 1
            fr.n_prefix += 1
    return fr


# ── 資料來源 ────────────────────────────────────────────────────────────────


async def _load_signals_from_db(file_hashes: list[str]) -> dict[str, list[dict]]:
    from sqlalchemy import text as sql

    from app.services.db import SessionFactory

    out: dict[str, list[dict]] = defaultdict(list)
    async with SessionFactory() as session:
        rows = await session.execute(
            sql(
                """
                SELECT r.file_hash, s.instrument_code, s.rating_raw, s.target_price,
                       s.target_price_evidence, s.extraction_status
                FROM research.report_signal s
                JOIN research.research_report r ON r.id = s.report_id
                WHERE r.file_hash = ANY(:hashes)
                """
            ),
            {"hashes": file_hashes},
        )
        for fh, code, rating, tp, ev, status in rows:
            out[fh].append(
                {
                    "instrument_code": code,
                    "rating_raw": rating,
                    "target_price": float(tp) if tp is not None else None,
                    "target_price_evidence": ev,
                    "extraction_status": status,
                }
            )
    return out


def load_signals(source: str, file_hashes: list[str]) -> dict[str, list[dict]]:
    if source == "none":
        return {}
    if source == "db":
        import asyncio

        return asyncio.run(_load_signals_from_db(file_hashes))
    raise ValueError(f"unknown signals source: {source}")


def resolve_path(case: dict) -> Path:
    p = Path(case["file_path"])
    if not p.is_absolute():
        p = ROOT / p
    return p


# ── 聚合 ────────────────────────────────────────────────────────────────────


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return statistics.fmean(vals) if vals else None


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def aggregate(cases: list[dict]) -> dict[str, Any]:
    """summary 的口徑：順序指標是 **配對加總** 不是逐檔平均——逐檔平均會讓 3 句的檔與
    15 句的檔權重相同；欄位層同樣以筆數加總。逐檔數字留在 cases 裡。"""
    with_order = [c for c in cases if c["order"]["n_sentences"]]
    n_pairs = sum(c["order"]["n_pairs"] for c in with_order)
    n_conc = sum(c["order"]["n_concordant"] for c in with_order)
    n_pairs_w = sum(c["order"]["n_pairs_within"] for c in with_order)
    n_conc_w = sum(c["order"]["n_concordant_within"] for c in with_order)
    n_sent = sum(c["order"]["n_sentences"] for c in with_order)
    n_hit = sum(c["order"]["n_hit"] for c in with_order)
    f = [c["fields"] for c in cases]
    return {
        "order_hit_rate": _ratio(n_hit, n_sent),
        "order_pair_acc": _ratio(n_conc, n_pairs),
        "order_pair_acc_within": _ratio(n_conc_w, n_pairs_w),
        "order_kendall_tau": (2 * n_conc - n_pairs) / n_pairs if n_pairs else None,
        "order_pair_acc_case_mean": _mean([c["order"]["pair_acc"] for c in with_order]),
        "n_cases_with_order": len(with_order),
        "n_order_sentences": n_sent,
        "n_order_pairs": n_pairs,
        "n_order_pairs_within": n_pairs_w,
        "rating_coverage": _ratio(sum(x["got_rating"] for x in f), sum(x["golden_rating"] for x in f)),
        "rating_accuracy": _ratio(sum(x["ok_rating"] for x in f), sum(x["got_rating"] for x in f)),
        "tp_coverage": _ratio(sum(x["got_tp"] for x in f), sum(x["golden_tp"] for x in f)),
        "tp_accuracy": _ratio(sum(x["ok_tp"] for x in f), sum(x["got_tp"] for x in f)),
        "n_golden_tp": sum(x["golden_tp"] for x in f),
        "hallucination_rate": _ratio(sum(x["n_unanchored"] for x in f), sum(x["n_evidence"] for x in f)),
        "n_evidence": sum(x["n_evidence"] for x in f),
        "n_evidence_prefix": sum(x["n_prefix"] for x in f),
        "chars_nows_total": sum(c["chars_nows"] for c in cases),
    }


def run(dataset: dict, extractor: str, signals_source: str, reviewed_only: bool) -> dict[str, Any]:
    cases_in = [c for c in dataset["cases"] if not reviewed_only or c.get("annotation_status") == "reviewed"]
    signals = load_signals(signals_source, [c["file_hash"] for c in cases_in])
    out_cases: list[dict] = []
    for c in cases_in:
        path = resolve_path(c)
        try:
            text, diag = extract(path, extractor)
        except Exception as exc:  # 單檔炸掉要留成一列，不能讓整份評測消失
            logger.error("%s: %s", c["id"], exc)
            text, diag = "", {"error": str(exc)}
        canonical = clean_extracted(text)
        order = score_order(canonical, c.get("order", []))
        fields = score_fields(
            c.get("fields", {}).get("instruments", []),
            signals.get(c["file_hash"], []),
            canonical,
        )
        out_cases.append(
            {
                "id": c["id"],
                "source": c.get("source"),
                "kind": c.get("kind"),
                "two_column": c.get("two_column"),
                "annotation_status": c.get("annotation_status"),
                "chars_nows": len(norm_for_match(canonical)),
                "diag": diag,
                "order": {
                    "n_sentences": order.n_sentences,
                    "n_hit": order.n_hit,
                    "n_pairs": order.n_pairs,
                    "n_concordant": order.n_concordant,
                    "n_pairs_within": order.n_pairs_within,
                    "n_concordant_within": order.n_concordant_within,
                    "hit_rate": order.hit_rate,
                    "pair_acc": order.pair_acc,
                    "pair_acc_within": order.pair_acc_within,
                    "kendall_tau": order.kendall_tau,
                    "misses": order.misses,
                },
                "fields": {
                    "golden_rating": fields.golden_rating,
                    "got_rating": fields.got_rating,
                    "ok_rating": fields.ok_rating,
                    "golden_tp": fields.golden_tp,
                    "got_tp": fields.got_tp,
                    "ok_tp": fields.ok_tp,
                    "n_evidence": fields.n_evidence,
                    "n_unanchored": fields.n_unanchored,
                    "n_prefix": fields.n_prefix,
                    "details": fields.details,
                },
            }
        )

    summary = aggregate(out_cases)
    summary["n_cases"] = len(out_cases)
    for st in STATUSES:
        summary[f"n_{st}"] = sum(1 for c in out_cases if c["annotation_status"] == st)
    subsets: dict[str, Any] = {
        "two_column": aggregate([c for c in out_cases if c["two_column"] is True]),
        "single_column": aggregate([c for c in out_cases if c["two_column"] is False]),
    }
    by_source: dict[str, Any] = {}
    for src in sorted({c["source"] or "unknown" for c in out_cases}):
        by_source[src] = aggregate([c for c in out_cases if (c["source"] or "unknown") == src])
    notes = [
        "順序指標以配對加總計，不是逐檔平均（order_pair_acc_case_mean 才是逐檔平均）。",
        "欄位層的抽取結果來自 --signals（現況為 DB 的 report_signal），與 --extractor 無關。",
        "幻覺率把 Anchor.method == 'prefix' 計為錨不回（§4.3）。",
    ]
    if summary["n_draft"]:
        notes.append(f"{summary['n_draft']} 筆為模型草標（draft），尚未人工覆核；數字暫定。")
    return {
        "summary": summary,
        "subsets": subsets,
        "by_source": by_source,
        "config": {
            "extractor": extractor,
            "signals_source": signals_source,
            "dataset_version": dataset.get("version"),
            "reviewed_only": reviewed_only,
            "run_date": date.today().isoformat(),
        },
        "notes": notes,
        "cases": out_cases,
    }


def fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}"


def print_report(result: dict) -> None:
    s = result["summary"]
    print(f"=== extraction eval  extractor={result['config']['extractor']}  n={s['n_cases']} "
          f"(reviewed {s['n_reviewed']} / draft {s['n_draft']} / prefilled {s['n_prefilled']}) ===")
    print(f"  閱讀順序：命中率 {fmt(s['order_hit_rate'])}  配對正確率 {fmt(s['order_pair_acc'])}  "
          f"同流配對 {fmt(s['order_pair_acc_within'])}  tau {fmt(s['order_kendall_tau'])}  "
          f"（{s['n_order_sentences']} 句／{s['n_order_pairs']} 對／同流 {s['n_order_pairs_within']} 對）")
    for name, sub in list(result["subsets"].items()) + list(result["by_source"].items()):
        print(f"    {name:<14} 配對 {fmt(sub['order_pair_acc'])}  同流 {fmt(sub['order_pair_acc_within'])}  "
              f"命中率 {fmt(sub['order_hit_rate'])}  （{sub['n_cases_with_order']} 檔）")
    print(f"  目標價：coverage {fmt(s['tp_coverage'])}  accuracy {fmt(s['tp_accuracy'])}  "
          f"（golden {s['n_golden_tp']} 筆）")
    print(f"  評等：  coverage {fmt(s['rating_coverage'])}  accuracy {fmt(s['rating_accuracy'])}")
    print(f"  幻覺率：{fmt(s['hallucination_rate'])}  "
          f"（evidence {s['n_evidence']} 筆，其中 prefix {s['n_evidence_prefix']}）")
    print(f"  文字健檢：去空白字元合計 {s['chars_nows_total']}")
    for c in result["cases"]:
        o = c["order"]
        flag = f" 漏 {len(o['misses'])} 句" if o["misses"] else ""
        print(f"  - {c['id']:<40} 配對 {fmt(o['pair_acc'])}  同流 {fmt(o['pair_acc_within'])}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extractor", choices=EXTRACTORS, default="pypdf")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--signals", choices=("db", "none"), default="db", help="欄位層的抽取結果來源")
    ap.add_argument("--reviewed-only", action="store_true", help="只吃 annotation_status == reviewed")
    ap.add_argument("--out", type=Path, help="結果 JSON 落點（對齊 eval/baselines/ 的 {summary, cases} 慣例）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = run(dataset, args.extractor, args.signals, args.reviewed_only)
    print_report(result)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
