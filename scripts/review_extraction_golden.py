"""golden set 覆核輔助：把每份 case 的頁面渲染成圖，在圖上框出每條 `order` 片段並標序號。

對應 docs/EXTRACTION.md §8 的「順序層必須純人工」——人工的部分是**看**，
不是翻 PDF 找句子。這支腳本把找的工作做掉：覆核者看序號在版面上是不是依閱讀順序
遞增、框住的字是不是那一句，再回 `eval/extraction_dataset.json` 改。

**唯讀**：不寫 dataset、不碰 DB、零 LLM。輸出落 `data/extraction/review/`（gitignored）。

用法：
    uv run python scripts/review_extraction_golden.py                 # 全部 case
    uv run python scripts/review_extraction_golden.py --case kgi-2313-daily-story-20250212
    uv run python scripts/review_extraction_golden.py --status draft  # 只出還沒覆核的
    然後開 data/extraction/review/index.html

每條片段旁邊有兩個旗標：
- `pypdf`：現況抽取文字裡找不找得到（找不到＝打字與原文有出入，先修這個）。
- `頁面`：在哪一頁的版面上定位到（定位不到不代表錯——跨欄或跨頁的片段版面搜尋會失敗，
  但只要 `pypdf` 有命中，評測就照算）。

定位方式：每頁用 pdfplumber 取字（含 bbox），依三種字序（位置序／content stream 序／
分欄序）各拼一次正規化字串，第一個找到片段的字序決定框的位置。框是逐行畫的，
跨行片段會有多個框、共用同一個序號。
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.textnorm import clean_extracted, norm_for_match  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "eval" / "extraction_dataset.json"
DEFAULT_OUT = ROOT / "data" / "extraction" / "review"

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logger = logging.getLogger("review_golden")

# 序號標籤與框線的顏色，依 stream 區分——同一 stream 內的順序是無歧義的，
# 覆核時最該盯的就是「同色序號有沒有遞增」。
STREAM_COLORS = {
    "head": (200, 30, 30),
    "side": (30, 110, 200),
    "main": (20, 140, 60),
    "col2": (150, 60, 170),
}
DEFAULT_COLOR = (200, 120, 20)
# 前幾頁定位不到時往後補掃的頁數上限；30 頁的月報全掃一遍約多花一兩秒。
FALLBACK_PAGES = 30


@dataclass
class Hit:
    page_no: int
    boxes: list[tuple[float, float, float, float]]  # (x0, top, x1, bottom)，每行一個
    ordering: str


# ── 版面定位 ────────────────────────────────────────────────────────────────


def _orderings(words: list[dict], page_width: float) -> dict[str, list[dict]]:
    """三種字序。分欄序把頁面切成左右兩半各自由上而下——粗糙，但側欄＋主文這種
    最常見的版型用它就能把跨行片段接起來。"""
    by_pos = sorted(words, key=lambda w: (round(w["top"] / 3), w["x0"]))
    mid = page_width / 2
    left = [w for w in by_pos if w["x0"] < mid]
    right = [w for w in by_pos if w["x0"] >= mid]
    return {"position": by_pos, "text_flow": words, "columns": left + right}


def _line_boxes(matched: list[dict]) -> list[tuple[float, float, float, float]]:
    """把命中的字依行分組，每行一個框。"""
    if not matched:
        return []
    rows: list[list[dict]] = []
    for w in matched:
        if rows and abs(rows[-1][0]["top"] - w["top"]) < 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [
        (min(w["x0"] for w in r), min(w["top"] for w in r), max(w["x1"] for w in r), max(w["bottom"] for w in r))
        for r in rows
    ]


def locate_on_page(words_by_ordering: dict[str, list[dict]], needle: str) -> tuple[list[tuple], str] | None:
    """在一頁裡找片段。回傳 (逐行框, 用了哪種字序)；找不到回 None。

    正規化後逐字拼接並記錄每個字元來自哪個 word，這樣 needle 在拼接串裡的區間就能
    映射回 word 區間。純函式，測試直接餵假 words。"""
    nq = norm_for_match(needle)
    if not nq:
        return None
    for name, words in words_by_ordering.items():
        owner: list[int] = []
        parts: list[str] = []
        for i, w in enumerate(words):
            nw = norm_for_match(w["text"])
            parts.append(nw)
            owner.extend([i] * len(nw))
        pos = "".join(parts).find(nq)
        if pos < 0:
            continue
        idxs = sorted(set(owner[pos : pos + len(nq)]))
        matched = [words[i] for i in idxs]
        return _line_boxes(matched), name
    return None


# ── 渲染 ────────────────────────────────────────────────────────────────────


def _color(stream: str) -> tuple[int, int, int]:
    return STREAM_COLORS.get(stream, DEFAULT_COLOR)


def render_case(case: dict, pdf_path: Path, out_dir: Path, scale: float, max_pages: int) -> dict:
    """回傳 {items: [...], pages: [png 相對路徑]}。"""
    import pdfplumber
    import pypdfium2 as pdfium
    from PIL import ImageDraw, ImageFont

    items = case.get("order", [])
    hits: dict[int, Hit] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        n_pages = len(pdf.pages)
        # 先掃前 max_pages 頁；stream 寫成 pN 的片段另外把第 N 頁補進掃描範圍。
        wanted = set(range(1, min(max_pages, n_pages) + 1))
        for it in items:
            s = it.get("stream") or ""
            if s.startswith("p") and s[1:].isdigit():
                wanted.add(int(s[1:]))
        wanted = {p for p in wanted if 1 <= p <= n_pages}
        page_words: dict[int, dict[str, list[dict]]] = {}
        for pno in sorted(wanted):
            page = pdf.pages[pno - 1]
            try:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=True) or []
            except Exception as exc:  # 單頁壞掉不擋整份
                logger.warning("%s p%d: %s", case["id"], pno, exc)
                words = []
            page_words[pno] = _orderings(words, float(page.width))

        def _scan(pno: int) -> None:
            if pno in page_words:
                return
            page = pdf.pages[pno - 1]
            try:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=True) or []
            except Exception as exc:  # 單頁壞掉不擋整份
                logger.warning("%s p%d: %s", case["id"], pno, exc)
                words = []
            page_words[pno] = _orderings(words, float(page.width))

        for idx, it in enumerate(items):
            for pno in sorted(page_words):
                found = locate_on_page(page_words[pno], it["text"])
                if found:
                    hits[idx] = Hit(pno, found[0], found[1])
                    break
        # 前幾頁沒定位到的片段，往後逐頁補掃（上限 FALLBACK_PAGES）：元大早報之類
        # 的多標的檔，標成 p4 的片段實際散在 p4–p7。
        for idx, it in enumerate(items):
            if idx in hits:
                continue
            for pno in range(1, min(n_pages, FALLBACK_PAGES) + 1):
                _scan(pno)
                found = locate_on_page(page_words[pno], it["text"])
                if found:
                    hits[idx] = Hit(pno, found[0], found[1])
                    break

    # 只渲染有片段落點的頁，外加第 1 頁（封面永遠要看：評等與目標價在那裡）。
    pages_to_render = sorted({1} | {h.page_no for h in hits.values()})
    doc = pdfium.PdfDocument(str(pdf_path))
    png_paths: list[str] = []
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(11 * scale))
    except OSError:
        font = ImageFont.load_default()
    for pno in pages_to_render:
        if pno > len(doc):
            continue
        img = doc[pno - 1].render(scale=scale).to_pil().convert("RGB")
        draw = ImageDraw.Draw(img)
        for idx, h in hits.items():
            if h.page_no != pno:
                continue
            col = _color(items[idx].get("stream") or "")
            for j, (x0, top, x1, bottom) in enumerate(h.boxes):
                box = [x0 * scale - 2, top * scale - 2, x1 * scale + 2, bottom * scale + 2]
                draw.rectangle(box, outline=col, width=2)
                if j == 0:
                    label = str(idx + 1)
                    tw = draw.textlength(label, font=font)
                    lx, ly = max(0, x0 * scale - tw - 8), max(0, top * scale - 2)
                    draw.rectangle([lx, ly, lx + tw + 6, ly + 13 * scale], fill=col)
                    draw.text((lx + 3, ly), label, fill=(255, 255, 255), font=font)
        rel = f"{case['id']}_p{pno}.png"
        img.save(out_dir / rel)
        png_paths.append(rel)

    return {
        "hits": {idx: {"page": h.page_no, "ordering": h.ordering} for idx, h in hits.items()},
        "pages": png_paths,
        "n_pages": n_pages,
    }


# ── pypdf 命中 ──────────────────────────────────────────────────────────────


def pypdf_counts(pdf_path: Path, items: list[dict]) -> list[int]:
    """每條片段在 pypdf 抽取文字裡出現幾次。0＝找不到（打字有出入）；≥2＝位置有歧義——
    評測照首次出現算，但那可能不是標註者想的那一處（例如側欄摘要句是主文首句的前綴），
    這種片段該換掉或加長到唯一。"""
    from app.services.extract import extract_text

    try:
        text = norm_for_match(clean_extracted(extract_text(pdf_path).text))
    except Exception as exc:
        logger.warning("pypdf %s: %s", pdf_path.name, exc)
        return [0] * len(items)
    return [text.count(norm_for_match(it["text"])) if norm_for_match(it["text"]) else 0 for it in items]


# ── HTML ────────────────────────────────────────────────────────────────────

_CSS = """
body{font-family:system-ui,'Noto Sans CJK TC',sans-serif;margin:0;background:#f4f4f2;color:#222}
.case{display:grid;grid-template-columns:420px 1fr;gap:16px;padding:16px;border-bottom:4px solid #ddd}
.meta{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;background:#fff;padding:12px;border-radius:6px;font-size:13px}
.meta h2{margin:0 0 6px;font-size:16px}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;color:#fff;margin-right:4px}
ol{padding-left:22px}ol li{margin:3px 0}
.miss{background:#ffe3e3}.dup{background:#fff0c2}.nohit{color:#999}
table{border-collapse:collapse;width:100%;margin:6px 0}
td,th{border:1px solid #ddd;padding:3px 5px;font-size:12px;text-align:left}
.pages img{max-width:100%;border:1px solid #ccc;margin-bottom:12px;background:#fff}
.notes{white-space:pre-wrap;background:#fffbe6;padding:6px;border-radius:4px}
.status-draft{background:#c77}.status-reviewed{background:#4a4}.status-prefilled{background:#888}
nav{padding:10px 16px;background:#fff;border-bottom:1px solid #ddd;font-size:13px}
nav a{margin-right:10px}
"""


def _tag(text: str, color: tuple[int, int, int]) -> str:
    return f'<span class="tag" style="background:rgb{color}">{html.escape(text)}</span>'


def case_html(case: dict, rendered: dict, counts: list[int]) -> str:
    items = case.get("order", [])
    f = case.get("fields", {})
    li = []
    for i, it in enumerate(items):
        stream = it.get("stream") or "main"
        hit = rendered["hits"].get(i)
        n = counts[i] if i < len(counts) else 0
        cls = "miss" if n == 0 else ("dup" if n > 1 else "")
        flags = ("pypdf 找不到" if n == 0 else ("pypdf ok" if n == 1 else f"pypdf 出現 {n} 處，位置有歧義")) + "｜"
        flags += f"頁面 p{hit['page']}（{hit['ordering']}）" if hit else '<span class="nohit">頁面未定位</span>'
        li.append(
            f'<li class="{cls}">{_tag(stream, _color(stream))}{html.escape(it["text"])}'
            f'<br><small>{flags}</small></li>'
        )
    inst_rows = "".join(
        f"<tr><td>{html.escape(str(x.get('code')))}</td><td>{html.escape(str(x.get('name') or ''))}</td>"
        f"<td>{html.escape(str(x.get('rating') or '—'))}</td>"
        f"<td>{html.escape(str((x.get('target_price') or {}).get('value', '—')))} "
        f"{html.escape(str((x.get('target_price') or {}).get('currency', '')))}</td>"
        f"<td>{html.escape(json.dumps(x.get('eps'), ensure_ascii=False) if x.get('eps') else '—')}</td></tr>"
        for x in f.get("instruments", [])
    )
    imgs = "".join(f'<img src="{html.escape(p)}" alt="{html.escape(p)}">' for p in rendered["pages"])
    st = case.get("annotation_status", "draft")
    return f"""
<section class="case" id="{html.escape(case['id'])}">
  <div class="meta">
    <h2>{html.escape(case['id'])} <span class="tag status-{st}">{st}</span></h2>
    <div>{html.escape(case.get('source') or '')}｜{html.escape(case.get('kind') or '')}｜
      {'雙欄' if case.get('two_column') else '單欄'}｜{rendered['n_pages']} 頁</div>
    <div><small>{html.escape(case.get('file_name') or '')}</small></div>
    <div><b>{html.escape(str(f.get('title') or ''))}</b>（{html.escape(str(f.get('report_date') or ''))}）</div>
    <table><tr><th>代號</th><th>名稱</th><th>評等</th><th>目標價</th><th>EPS</th></tr>{inst_rows}</table>
    <div class="notes">{html.escape(case.get('annotator_notes') or '')}</div>
    <h3>順序層（{len(items)} 條；紅底＝pypdf 找不到，先修打字；黃底＝出現多處，換成唯一的片段）</h3>
    <ol>{''.join(li)}</ol>
  </div>
  <div class="pages">{imgs}</div>
</section>"""


def build(
    dataset: dict, out_dir: Path, case_ids: set[str] | None, status: str | None, scale: float, max_pages: int
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    nav: list[str] = []
    total_miss = 0
    total_dup = 0
    for case in dataset["cases"]:
        if case_ids and case["id"] not in case_ids:
            continue
        if status and case.get("annotation_status") != status:
            continue
        pdf_path = ROOT / case["file_path"]
        if not pdf_path.exists():
            logger.error("%s: 找不到 %s", case["id"], pdf_path)
            continue
        counts = pypdf_counts(pdf_path, case.get("order", []))
        misses = [i for i, n in enumerate(counts) if n == 0]
        dups = [i for i, n in enumerate(counts) if n > 1]
        total_miss += len(misses)
        total_dup += len(dups)
        rendered = render_case(case, pdf_path, out_dir, scale, max_pages)
        sections.append(case_html(case, rendered, counts))
        n_items = len(case.get("order", []))
        n_hit = len(rendered["hits"])
        nav.append(f'<a href="#{html.escape(case["id"])}">{html.escape(case["id"])}（{n_hit}/{n_items} 定位）</a>')
        print(f"{case['id']:<44} 定位 {n_hit}/{n_items}  pypdf 漏 {len(misses)} 歧義 {len(dups)}  "
              f"頁 {rendered['pages']}")
    index = out_dir / "index.html"
    index.write_text(
        f"<!doctype html><meta charset='utf-8'><title>golden set 覆核</title><style>{_CSS}</style>"
        f"<nav>{' '.join(nav)}</nav>"
        "<nav><small>序號顏色＝stream："
        + " ".join(_tag(k, v) for k, v in STREAM_COLORS.items())
        + _tag("pN／其他", DEFAULT_COLOR)
        + "。覆核重點：同色序號在版面上是否依閱讀順序遞增；框住的字是否就是那一句；"
        "封面的評等／目標價是否與左表一致。"
        "改完到 eval/extraction_dataset.json 把 annotation_status 改成 reviewed。</small></nav>"
        + "".join(sections),
        encoding="utf-8",
    )
    print(f"→ {index}（pypdf 找不到 {total_miss} 條、出現多處 {total_dup} 條）")
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--case", action="append", help="只出這些 case id（可重複）")
    ap.add_argument("--status", choices=("prefilled", "draft", "reviewed"), help="只出這個標註狀態的 case")
    ap.add_argument("--scale", type=float, default=1.6, help="渲染倍率（1.0＝72dpi）")
    ap.add_argument("--max-pages", type=int, default=3, help="預設掃描前幾頁定位片段（pN stream 會另外補頁）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    build(dataset, args.out_dir, set(args.case) if args.case else None, args.status, args.scale, args.max_pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
