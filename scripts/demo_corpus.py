"""示範券商（`profiles/demo`）的合成研報語料：以固定種子產生虛構的 PDF 研報與 metadata 側檔。

給示範環境與容器端到端測試（`scripts/e2e_compose.py`）用。**內容全部是虛構的**：機構、分析師、數字都是編的，
每一頁的頁首、頁尾與文末都寫明「虛構研報，僅供系統示範」。股票代號用真實的代號（讓標註、訊號、雷達的
路徑有東西可走），但評等、目標價、財測一律是亂數產生、與該公司的真實狀況無關。產出的 PDF 不進版控
（CI 執行期產生）。

刻意的排版特徵（測抽取層）：頁首頁尾（`@page` 邊框區）、雙欄內文、財務表格、多頁。

側檔的組合（測連接器）：

- `full`：JSON 給齊 `market`＋`stock_targets`（不呼叫標註 LLM）、`title`（標題批次跳過）、`source`、`report_date` 等。
- `partial`：JSON 只給 `report_date`、`source`、`analysts`（照常標註，再以側檔欄位覆寫）。
- `csv`：寫在落地根目錄的 `_metadata.csv`。
- `none`：沒有側檔（日期由檔名推斷，來源券商推不出來＝NULL）。
- `invalid_*`：故意寫錯（非標準市場、日期格式錯、未知欄位、JSON 語法錯）——這幾份不入庫、進失敗清單。

`expected.json` 寫出端到端測試要對帳的預期值（入庫份數、失敗清單、側檔給的標題等）；它只在輸出資料夾，
不上傳到 bucket。

用法：
  uv run python scripts/demo_corpus.py --out /tmp/demo-corpus                 # 預設 30 份、日期以今天為錨
  uv run python scripts/demo_corpus.py --out DIR --anchor-date 2026-11-10    # 指定錨點日（完全可重現）
  uv run python scripts/demo_corpus.py --out DIR --plan-only                 # 只寫 expected.json 與側檔，不渲染 PDF

渲染用 weasyprint（dev 相依）與 Noto CJK 字型（`fonts-noto-cjk`）；缺字型時中文抽不回來，端到端測試會在入庫數失敗。
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_SEED = 20261110
TAIPEI = timezone(timedelta(hours=8))
DISCLAIMER = "本報告為程式產生的虛構內容，機構、分析師與所有數字均為虛構，僅供系統示範，不構成任何投資建議。"

# 虛構的發行機構（source 代碼 → 顯示名稱）。名稱刻意帶「虛構」，不模仿任何真實機構。
BROKERS: dict[str, str] = {
    "aster": "星樺證券（虛構）",
    "birch": "樺林投顧（虛構）",
    "cobalt": "鈷藍證券（虛構）",
    "dune": "沙丘資本（虛構）",
}
ANALYSTS = ("林示範", "陳範例", "王虛構", "張測試", "李假設")

# 真實代號＋虛構內容。market 是標準市場代碼；code_tag 是內文的代號標記（標註 stub 以它認標的）。
TW_STOCKS = {"2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電"}
US_STOCKS = {"NVDA": "輝達", "AAPL": "蘋果", "MSFT": "微軟"}
MACRO_TOPICS = ("美國利率路徑", "台灣出口動能", "全球製造業循環", "美元與亞幣匯率")
RATINGS = ("買進", "增加持股", "中立", "持有")


@dataclass
class Report:
    file: str  # 相對於 bucket prefix 的路徑
    market: str
    broker: str
    report_date: date
    sidecar: str  # full／partial／csv／none／invalid_market／invalid_date／invalid_field／invalid_json
    code: str | None = None
    name: str | None = None
    topic: str | None = None
    rating: str | None = None
    target: float | None = None
    analysts: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def ingests(self) -> bool:
        return not self.sidecar.startswith("invalid_")


def _plan_specs() -> list[tuple[str, str | None, str, str]]:
    """(market, code／topic, broker, sidecar)。訊號子集要每檔 ≥3 家券商、≥5 份（extract_signals 的預設門檻）。"""
    specs: list[tuple[str, str | None, str, str]] = []
    specs += [("TW", "2330", b, s) for b, s in (
        ("aster", "full"), ("birch", "partial"), ("cobalt", "full"), ("dune", "csv"), ("aster", "partial"),
        ("birch", "full"))]
    specs += [("TW", "2317", b, s) for b, s in (
        ("aster", "full"), ("birch", "partial"), ("cobalt", "full"), ("aster", "csv"), ("cobalt", "partial"))]
    specs += [("TW", "2454", b, s) for b, s in (
        ("birch", "full"), ("cobalt", "partial"), ("dune", "full"), ("birch", "csv"), ("dune", "partial"))]
    specs += [("TW", "2308", "aster", "none"), ("TW", "2308", "dune", "invalid_market")]
    specs += [("US", "NVDA", "cobalt", "full"), ("US", "NVDA", "dune", "partial"), ("US", "NVDA", "aster", "none")]
    specs += [("US", "AAPL", "birch", "full"), ("US", "AAPL", "cobalt", "invalid_date")]
    specs += [("US", "MSFT", "dune", "full"), ("US", "MSFT", "aster", "invalid_field")]
    specs += [("MACRO", t, b, s) for t, b, s in (
        (MACRO_TOPICS[0], "aster", "full"), (MACRO_TOPICS[1], "birch", "csv"), (MACRO_TOPICS[2], "cobalt", "none"),
        (MACRO_TOPICS[3], "dune", "invalid_json"))]
    # 未啟用市場（示範 profile 只開 TW／US／MACRO）的研報照常入庫：子集管的是篩選，不是可見性。
    specs += [("HK", "0700", "birch", "full")]
    return specs


def plan(anchor: date, seed: int = DEFAULT_SEED) -> list[Report]:
    rng = random.Random(seed)
    reports: list[Report] = []
    specs = _plan_specs()
    for i, (market, key, broker, sidecar) in enumerate(specs):
        # 大多在近一年（分層回填的第二層），每 9 份有一份落在 13～18 個月前（第三層）。
        days = rng.randint(400, 540) if i % 9 == 8 else rng.randint(0, 300)
        rdate = anchor - timedelta(days=days)
        r = Report(file="", market=market, broker=broker, report_date=rdate, sidecar=sidecar,
                   analysts=rng.sample(ANALYSTS, 2))
        if market == "MACRO":
            r.topic = key
            r.title = f"{key}：示範總經觀點"
            stem = f"{rdate:%Y%m%d}_{broker}_macro_{i:02d}"
        else:
            r.code = key
            r.name = TW_STOCKS.get(key) or US_STOCKS.get(key) or "騰訊"
            r.rating = rng.choice(RATINGS)
            base = 1000 if market == "TW" else 250
            r.target = float(round(base * rng.uniform(0.3, 1.6)))
            r.title = f"{r.name}（{key}）：示範個股報告"
            stem = f"{rdate:%Y%m%d}_{broker}_{key.lower()}_{i:02d}"
        r.file = f"{rdate:%Y}/{stem}.pdf"
        reports.append(r)
    return reports


# ── 內文 ─────────────────────────────────────────────────────────────────


def _code_tag(r: Report) -> str:
    if r.market == "TW":
        return f"{r.name}（{r.code} TT）"
    if r.market == "US":
        return f"{r.name}（{r.code} US）"
    return f"{r.name}（{r.code} HK）"


def _market_label(r: Report) -> str:
    return {"TW": "台股", "US": "美股", "MACRO": "總經", "HK": "港股"}[r.market]


def body_paragraphs(r: Report, rng: random.Random) -> list[str]:
    broker = BROKERS[r.broker]
    if r.market == "MACRO":
        rate = round(rng.uniform(1.5, 5.5), 2)
        growth = round(rng.uniform(-3, 9), 1)
        return [
            f"本期{broker}總經團隊聚焦{r.topic}，以下為第{r.report_date:%m}月份的虛構觀察。",
            f"展望方面，我們假設未來兩季的政策利率落在{rate}%附近，市場對降息時點的分歧仍大。",
            f"出口與製造業的示範數據顯示年增率約{growth}%，動能較上一季溫和。",
            "風險方面，地緣政治與能源價格的波動仍是影響通膨預期的主要變數。",
            "資產配置上，我們維持股債均衡的虛構建議，並提高現金部位以因應波動。",
            f"本段落以{r.topic}為主題，數字均為程式隨機產生，請勿引用。",
        ]
    tag = _code_tag(r)
    currency = "NT$" if r.market == "TW" else ("US$" if r.market == "US" else "HK$")
    target = f"{r.target:,.0f}"
    eps = round(rng.uniform(5, 60), 2)
    margin = round(rng.uniform(20, 60), 1)
    return [
        f"我們維持{tag}「{r.rating}」評等，目標價 {currency}{target}。",
        f"展望方面，{r.name}第{rng.randint(1, 4)}季的虛構營收年增約{rng.randint(5, 40)}%，毛利率估{margin}%。",
        f"我們預估{r.name}今年每股盈餘約{eps}元，主要受惠於產品組合改善。",
        f"風險方面，{r.name}面臨的需求波動與匯率變化仍需持續追蹤。",
        f"催化劑包含新產品導入與產能擴充，{broker}將於下次更新時重新檢視。",
        f"本報告的評價方法採本益比區間法，代號{r.code}的所有數字均為虛構。",
    ]


def _table(r: Report, rng: random.Random) -> str:
    years = [r.report_date.year - 1, r.report_date.year, r.report_date.year + 1]
    rows = []
    for label in ("營收（億）", "毛利率（%）", "每股盈餘（元）"):
        vals = [f"{rng.uniform(10, 900):,.1f}" for _ in years]
        rows.append(f"<tr><th>{label}</th>" + "".join(f"<td>{v}</td>" for v in vals) + "</tr>")
    cells = "".join(f"<th>{y}{'F' if y > r.report_date.year else ''}</th>" for y in years)
    head = f"<tr><th>項目</th>{cells}</tr>"
    return f'<table class="fin"><caption>表一：虛構財務預估</caption>{head}{"".join(rows)}</table>'


def render_html(r: Report, seed: int) -> str:
    rng = random.Random(f"{seed}:{r.file}")
    broker = BROKERS[r.broker]
    paras = body_paragraphs(r, rng)
    filler = [f"補充說明第{i}段：此處為版面測試用的虛構文字，模擬研報常見的多欄排版與段落長度。" * 2
              for i in range(1, 7)]
    subject = r.topic if r.market == "MACRO" else _code_tag(r)
    kind = "總經報告" if r.market == "MACRO" else "個股報告"
    esc = html.escape
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 22mm 16mm 20mm 16mm;
  @top-left {{ content: "{esc(broker)}"; font-size: 8pt; color: #555; }}
  @top-right {{ content: "虛構研報｜僅供系統示範"; font-size: 8pt; color: #555; }}
  @bottom-left {{ content: "本報告內容均為虛構"; font-size: 7pt; color: #777; }}
  @bottom-right {{ content: "第 " counter(page) " 頁／共 " counter(pages) " 頁"; font-size: 7pt; color: #777; }} }}
body {{ font-family: "Noto Sans CJK TC", "Noto Sans CJK SC", sans-serif; font-size: 10pt; line-height: 1.55; }}
h1 {{ font-size: 16pt; margin: 0 0 4mm; }}
.meta {{ font-size: 9pt; color: #333; margin-bottom: 4mm; }}
.cols {{ column-count: 2; column-gap: 8mm; }}
p {{ margin: 0 0 3mm; }}
table.fin {{ border-collapse: collapse; width: 100%; margin: 5mm 0; font-size: 9pt; }}
table.fin th, table.fin td {{ border: 0.4pt solid #888; padding: 1.5mm 2mm; text-align: right; }}
table.fin caption {{ text-align: left; font-weight: bold; margin-bottom: 1mm; }}
.disc {{ font-size: 8pt; color: #555; margin-top: 6mm; border-top: 0.4pt solid #aaa; padding-top: 2mm; }}
</style></head><body>
<h1>{esc(r.title)}</h1>
<div class="meta">市場別：{_market_label(r)}｜類型：{kind}｜發布日：{r.report_date:%Y-%m-%d}<br>
標的：{esc(subject)}｜分析師：{esc("、".join(r.analysts))}｜{esc(broker)}</div>
<div class="cols">{"".join(f"<p>{esc(p)}</p>" for p in paras)}{"".join(f"<p>{esc(p)}</p>" for p in filler[:3])}</div>
{_table(r, rng)}
<div class="cols">{"".join(f"<p>{esc(p)}</p>" for p in filler[3:] * 3)}</div>
<p class="disc">{DISCLAIMER}</p>
</body></html>"""


# ── 側檔 ─────────────────────────────────────────────────────────────────


def sidecar_obj(r: Report, *, full: bool | None = None) -> dict:
    """側檔欄位（`app/services/connectors/sidecar.py` 的格式）：`partial` 只給部分欄位，其餘給完整欄位。"""
    if full is None:
        full = r.sidecar != "partial"
    if not full:
        return {"report_date": r.report_date.isoformat(), "source": r.broker, "analysts": r.analysts}
    obj: dict = {"title": r.title, "report_date": r.report_date.isoformat(), "source": r.broker,
                 "market": r.market, "stock_targets": [r.code] if r.code else [], "analysts": r.analysts}
    if r.code:
        obj["instrument_types"] = ["equity"]
    if r.rating:
        obj["rating"] = r.rating
    if r.target is not None:
        obj["target_price"] = r.target
        obj["target_currency"] = {"TW": "TWD", "US": "USD", "HK": "HKD"}[r.market]
    return obj


# 故意寫錯的側檔：在完整欄位上動一個地方（sidecar.validate 一律拒收）。
_BREAKAGE = {
    "invalid_market": lambda o, r: {**o, "market": "JP"},  # 不是標準市場代碼
    "invalid_date": lambda o, r: {**o, "report_date": r.report_date.strftime("%Y/%m/%d")},  # 日期格式錯
    "invalid_field": lambda o, r: {**o, "sector": "科技"},  # 未知欄位
}
_TRUNCATED_JSON = '{"title": "示範", "report_date": '.encode("utf-8")  # JSON 語法錯


def sidecar_bytes(r: Report) -> bytes | None:
    """JSON 側檔的內容（`csv`、`none` 回 None）。"""
    if r.sidecar in ("csv", "none"):
        return None
    if r.sidecar == "invalid_json":
        return _TRUNCATED_JSON
    obj = sidecar_obj(r, full=r.sidecar != "partial")
    if r.sidecar in _BREAKAGE:
        obj = _BREAKAGE[r.sidecar](obj, r)
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def manifest_bytes(reports: list[Report]) -> bytes:
    rows = [r for r in reports if r.sidecar == "csv"]
    cols = ["file", "title", "report_date", "source", "market", "stock_targets", "analysts"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        o = sidecar_obj(r)
        w.writerow([r.file, o["title"], o["report_date"], o["source"], o["market"],
                    ";".join(o["stock_targets"]), ";".join(o["analysts"])])
    return buf.getvalue().encode("utf-8")


def expected(reports: list[Report], anchor: date) -> dict:
    ok = [r for r in reports if r.ingests]
    return {
        "anchor_date": anchor.isoformat(),
        "total_files": len(reports),
        "ingested": len(ok),
        "sidecar_invalid": sorted(r.file for r in reports if not r.ingests),
        "metadata_titles": {r.file: r.title for r in ok if r.sidecar in ("full", "csv")},
        "metadata_sources": {r.file: r.broker for r in ok if r.sidecar != "none"},
        "markets": {r.file: r.market for r in ok},
        "stock_code": {r.file: r.code for r in ok if r.code},
        "no_sidecar": sorted(r.file for r in reports if r.sidecar == "none"),
    }


def write(out: Path, reports: list[Report], anchor: date, seed: int, *, render: bool = True) -> None:
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    if render:
        from weasyprint import HTML  # dev 相依；只在渲染時才需要

    for r in reports:
        pdf = corpus / r.file
        pdf.parent.mkdir(parents=True, exist_ok=True)
        if render:
            HTML(string=render_html(r, seed)).write_pdf(str(pdf), metadata={"creator": "report-mark demo_corpus"})
            # 檔案時間設成報告日（台北中午）：連接器以物件 metadata 的 mtime 還原，日期推不出來時是最後的退路。
            ts = datetime(r.report_date.year, r.report_date.month, r.report_date.day, 12, tzinfo=TAIPEI).timestamp()
            os.utime(pdf, (ts, ts))
        side = sidecar_bytes(r)
        if side is not None:
            pdf.with_suffix(".json").write_bytes(side)
    (corpus / "_metadata.csv").write_bytes(manifest_bytes(reports))
    (out / "expected.json").write_text(json.dumps(expected(reports, anchor), ensure_ascii=False, indent=2),
                                       encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="輸出資料夾（corpus/ 是要上傳的內容，expected.json 是預期值）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--anchor-date", default=None, help="日期錨點 YYYY-MM-DD（預設今天，台北時間）")
    ap.add_argument("--plan-only", action="store_true", help="不渲染 PDF（只寫側檔、清單與 expected.json）")
    args = ap.parse_args(argv)
    anchor = date.fromisoformat(args.anchor_date) if args.anchor_date else datetime.now(TAIPEI).date()
    reports = plan(anchor, args.seed)
    write(Path(args.out), reports, anchor, args.seed, render=not args.plan_only)
    print(f"demo_corpus：{len(reports)} 份（預期入庫 {sum(r.ingests for r in reports)}）→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
