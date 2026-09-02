"""新舊抽取器並排比對（唯讀）→ 自包含 HTML 報告。

對應 docs/EXTRACTION_REDESIGN.md §9 的第 3 步：「只讀不寫並排比對，人工看
20 份」。**它是唯一能在改動生產路徑之前發現「新抽取器在某類檔案上更差」
的機會**，所以不要跳過。

用法：
    uv run python scripts/compare_extractors.py --sample-per-source 5
    uv run python scripts/compare_extractors.py --limit 40 --workers 8

## 報告裡為什麼要印 Block 分類

實測已經證明 pdfplumber 與 pypdf 的輸出**必然不同**（12 份樣本相似度
0.275–0.884），但「不同」不等於「更好」。而新側可能以兩種方式變差：

- **抽取壞了**：文字本身就錯（漏抽、順序亂）。
- **分類壞了**：文字對，但 `layout.py` 把正文判成頁首而在序列化時丟掉。

這兩種的處置完全不同（一個改排序、一個改門檻），而只看最終文字**分不出
來**。所以每一份都附 Block 型別統計與「被丟掉的 header/footer 內容」。
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extraction.layout import extract_document  # noqa: E402
from app.services.extraction.model import DEFAULT_DROP, block_text, serialize  # noqa: E402
from app.services.extraction.quality import measure  # noqa: E402
from app.services.filename import parse_filename  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "研報自動匯入"
OUT_DIR = ROOT / "data" / "extraction"

logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

_WS = re.compile(r"\s+")
# 相似度只比前這麼多字元。difflib 是 O(n²)，整份 12 萬字會跑到天荒地老，
# 而前 6000 字（約 3-4 頁）已經足以暴露雙欄交錯。
_SIM_CHARS = 6000


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def _work(args: tuple[str, int]) -> dict:
    path_str, text_chars = args
    path = Path(path_str)
    rec: dict = {"file_name": path.name, "file_path": path_str}
    try:
        meta = parse_filename(path.name)
        rec["source"] = meta.source or "unknown"
        rec["report_date"] = meta.report_date.isoformat() if meta.report_date else None
    except Exception:
        rec["source"] = "unknown"

    # 舊：現況生產路徑用的那一支，原封不動呼叫
    try:
        from app.services.extract import extract_text

        old = extract_text(path)
        rec["old_text"] = old.text
        rec["old_chars"] = old.char_count
        rec["old_scanned"] = old.scanned
        rec["file_hash"] = old.file_hash
    except Exception as exc:
        rec["error"] = f"pypdf: {exc}"[:200]
        return rec

    # 新
    try:
        import pypdfium2 as pdfium

        doc = extract_document(path)
        if doc.error:
            rec["error"] = f"new open: {doc.error}"[:200]
            return rec
        new_text = serialize(doc)
        q = measure(doc, pdfium.PdfDocument(str(path)))
        rec["new_text"] = new_text
        rec["new_chars"] = len(new_text)
        rec.update(q.as_flags())
        rec["block_types"] = dict(Counter(b.type for b in doc.blocks()))
        # 被序列化丟掉的東西——分類壞掉時，正文會出現在這裡
        rec["dropped"] = [
            block_text(b)[:160]
            for b in doc.blocks()
            if b.type in DEFAULT_DROP and block_text(b).strip()
        ][:40]
    except Exception as exc:
        rec["error"] = f"new: {type(exc).__name__}: {exc}"[:200]
        return rec

    a, b = _norm(rec["old_text"])[:_SIM_CHARS], _norm(new_text)[:_SIM_CHARS]
    rec["order_sim"] = round(difflib.SequenceMatcher(None, a, b).ratio(), 3) if a and b else None
    # **字數比一律用去空白後的字元數。** 原始長度會被兩個抽取器的空白習慣淹掉：
    # pypdf 對表格每格換一行、pdfplumber 序列化成 markdown 一列一行，2026-09-02 實測
    # 13 份「原始字數比 <0.9」的檔案去空白後 4 份逐字元相等、其餘差異 <5%，
    # 沒有任何一份真的漏字——那個 <0.9 全是空白與頁首頁尾（刻意丟掉的）造成的。
    old_n, new_n = len(_norm(rec["old_text"])), len(_norm(new_text))
    rec["old_chars_norm"] = old_n
    rec["new_chars_norm"] = new_n
    rec["char_delta"] = new_n - old_n
    rec["char_ratio"] = round(new_n / old_n, 3) if old_n else None

    rec["old_text"] = rec["old_text"][:text_chars]
    rec["new_text"] = new_text[:text_chars]
    return rec


def _pick(files: list[Path], per_source: int | None, limit: int | None) -> list[Path]:
    if per_source:
        by: dict[str, list[Path]] = defaultdict(list)
        for p in files:
            try:
                src = parse_filename(p.name).source or "unknown"
            except Exception:
                src = "unknown"
            by[src].append(p)
        files = [p for s in sorted(by) for p in sorted(by[s])[:per_source]]
    return files[:limit] if limit else files


# ── HTML ────────────────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b7280;--line:#e5e7eb;--acc:#8a6d3b;--warn:#b45309;--bad:#b91c1c;--ok:#15803d;--panel:#f9fafb}
@media(prefers-color-scheme:dark){:root{--bg:#111214;--fg:#e8e8ea;--mut:#9ca3af;--line:#2a2d33;--acc:#c9a227;--warn:#d97706;--bad:#ef4444;--ok:#22c55e;--panel:#17181b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"Noto Sans TC",system-ui,sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line)}
h1{margin:0 0 6px;font-size:19px;letter-spacing:.01em}
.sub{color:var(--mut);font-size:13px}
.kpis{display:flex;flex-wrap:wrap;gap:20px;margin-top:14px}
.kpi b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--mut);font-size:12px}
main{padding:16px 24px 60px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
input,select{background:var(--panel);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:6px 9px;font:inherit}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{cursor:pointer;user-select:none;color:var(--mut);font-weight:600;font-size:12px;position:sticky;top:0;background:var(--bg)}
tbody tr{cursor:pointer}
tbody tr:hover{background:var(--panel)}
.name{max-width:340px;overflow:hidden;text-overflow:ellipsis}
.bad{color:var(--bad);font-weight:600}.warn{color:var(--warn)}.ok{color:var(--ok)}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:10px 0 24px}
.pane{border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0}
.pane h3{margin:0;padding:8px 12px;font-size:12px;background:var(--panel);
  border-bottom:1px solid var(--line);color:var(--mut)}
.pane pre{margin:0;padding:12px;max-height:70vh;overflow:auto;white-space:pre-wrap;
  word-break:break-word;font:12px/1.6 ui-monospace,"Noto Sans Mono CJK TC",monospace}
.meta{margin:8px 0;color:var(--mut);font-size:12px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;
  padding:1px 9px;margin:2px 4px 2px 0;font-size:12px}
.path{margin:6px 0 10px;font-size:12px;color:var(--mut)}
.path code{background:var(--panel);border:1px solid var(--line);border-radius:5px;
  padding:2px 7px;word-break:break-all;user-select:all}
.path button{margin-left:8px;background:var(--panel);color:var(--fg);border:1px solid var(--line);
  border-radius:5px;padding:2px 9px;font:inherit;font-size:12px;cursor:pointer}
.drop{border:1px dashed var(--line);border-radius:8px;padding:10px 12px;margin-bottom:18px}
.drop div{color:var(--mut);font-size:12px;margin-bottom:6px}
.drop code{display:block;font-size:12px;padding:1px 0;word-break:break-word}
@media(max-width:900px){.panes{grid-template-columns:1fr}}
"""

_JS = """
const D = JSON.parse(document.getElementById('data').textContent);
let sortKey='order_sim', asc=true, q='', src='';
const esc = s => (s??'').toString().replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const num = v => v==null? '' : (typeof v==='number'? v : v);
function cls(r){ if(r.error) return 'bad'; if(r.order_sim!=null&&r.order_sim<0.5) return 'warn'; return ''; }
function rows(){
  let rs = D.filter(r => (!src||r.source===src) && (!q||r.file_name.toLowerCase().includes(q)));
  rs.sort((a,b)=>{const x=a[sortKey],y=b[sortKey];
    if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
    return (x>y?1:x<y?-1:0)*(asc?1:-1);});
  return rs;
}
function render(){
  const rs=rows();
  document.getElementById('count').textContent = rs.length+' / '+D.length+' 份';
  document.getElementById('tb').innerHTML = rs.map((r,i)=>`<tr data-i="${D.indexOf(r)}">
    <td class="name" title="${esc(r.file_name)}">${esc(r.file_name)}</td>
    <td>${esc(r.source)}</td>
    <td>${num(r.page_count)}</td>
    <td>${num(r.max_columns)}</td>
    <td class="${cls(r)}">${r.error? 'ERR' : num(r.order_sim)}</td>
    <td>${num(r.old_chars)}</td>
    <td>${num(r.new_chars)}</td>
    <td>${num(r.char_ratio)}</td>
    <td>${num(r.tables)}</td>
    <td>${num(r.layout_coverage)}</td>
    <td>${num(r.quality_score)}</td>
    <td>${r.failed||''}</td></tr>`).join('');
}
function detail(r){
  const bt = Object.entries(r.block_types||{}).map(([k,v])=>`<span class="chip">${esc(k)} ${v}</span>`).join('');
  const dropped = (r.dropped||[]).length
    ? `<div class="drop"><div>序列化時丟掉的 header/footer（${r.dropped.length} 筆）——`
      + `<b>正文出現在這裡就代表分類壞了，不是抽取壞了</b></div>`
      + r.dropped.map(t=>`<code>${esc(t)}</code>`).join('') + `</div>` : '';
  const err = r.error ? ' · <span class="bad">'+esc(r.error)+'</span>' : '';
  return `<div class="meta"><b>${esc(r.file_name)}</b> · ${esc(r.source)}`
  + ` · ${r.page_count??'?'} 頁 · 相似度 ${r.order_sim??'—'}`
  + ` · coverage ${r.layout_coverage??'—'}${err}</div>
  <div class="path">原始 PDF：<code id="p">${esc(r.file_path)}</code>
  <button data-copy="${esc(r.file_path)}">複製路徑</button></div>
  <div class="meta">${bt}</div>${dropped}
  <div class="panes">
    <div class="pane"><h3>舊：pypdf（現況生產路徑）</h3><pre>${esc(r.old_text)}</pre></div>
    <div class="pane"><h3>新：pdfplumber 版面分析</h3><pre>${esc(r.new_text)}</pre></div>
  </div>`;
}
document.addEventListener('click', e=>{
  const th=e.target.closest('th[data-k]');
  if(th){ const k=th.dataset.k; asc = (k===sortKey)? !asc : true; sortKey=k; render(); return; }
  const tr=e.target.closest('tbody tr');
  if(tr){ const r=D[+tr.dataset.i];
    const box=document.getElementById('detail');
    box.innerHTML=detail(r);
    box.scrollIntoView({behavior:'smooth',block:'start'}); }
});
document.addEventListener('click', e=>{
  const b=e.target.closest('button[data-copy]');
  if(!b) return;
  const t=b.dataset.copy;
  if(navigator.clipboard&&window.isSecureContext){ navigator.clipboard.writeText(t); }
  else { const ta=document.createElement('textarea'); ta.value=t;
    ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta);
    ta.select(); try{document.execCommand('copy');}catch(_){} ta.remove(); }
  b.textContent='已複製'; setTimeout(()=>b.textContent='複製路徑', 1200);
});
document.getElementById('q').addEventListener('input', e=>{q=e.target.value.toLowerCase(); render();});
document.getElementById('src').addEventListener('change', e=>{src=e.target.value; render();});
render();
"""


def _html(recs: list[dict], meta: dict) -> str:
    sims = sorted(r["order_sim"] for r in recs if r.get("order_sim") is not None)
    worst = sims[0] if sims else None
    med = sims[len(sims) // 2] if sims else None
    sources = sorted({r.get("source") or "unknown" for r in recs})
    opts = "".join(f'<option value="{html.escape(s)}">{html.escape(s)}</option>' for s in sources)
    kpi = [
        ("樣本數", len(recs)),
        ("抽取失敗", sum(1 for r in recs if r.get("error"))),
        ("順序相似度 中位", med if med is not None else "—"),
        ("最低", worst if worst is not None else "—"),
        ("相似度 &lt;0.5", sum(1 for r in recs if (r.get("order_sim") or 1) < 0.5)),
        ("多欄檔案", sum(1 for r in recs if (r.get("max_columns") or 1) > 1)),
        ("有頁級失敗", sum(1 for r in recs if r.get("pages_failed"))),
    ]
    kpis = "".join(f'<div class="kpi"><b>{v}</b><span>{k}</span></div>' for k, v in kpi)
    cols = [
        ("file_name", "檔名"), ("source", "券商"), ("page_count", "頁"),
        ("max_columns", "欄"), ("order_sim", "順序相似度"), ("old_chars", "舊字元"),
        ("new_chars", "新字元"), ("char_ratio", "新/舊（去空白）"), ("tables", "表格"),
        ("layout_coverage", "coverage"), ("quality_score", "score"), ("failed", "失敗頁"),
    ]
    ths = "".join(f'<th data-k="{k}">{html.escape(t)}</th>' for k, t in cols)
    data = json.dumps(recs, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>抽取器並排比對</title><style>{_CSS}</style></head><body>
<header><h1>抽取器並排比對：pypdf vs pdfplumber 版面分析</h1>
<div class="sub">docs/EXTRACTION_REDESIGN.md §9 第 3 步 · 只讀不寫 · {html.escape(meta['generated'])}</div>
<div class="sub">相似度是<b>去除所有空白後</b>前 {_SIM_CHARS} 字元的 difflib ratio
——它量的是<b>順序</b>不是品質。低＝兩者差很多，<b>不代表新的比較好</b>，
那要靠 golden set 回答。</div>
<div class="kpis">{kpis}</div></header>
<main><div class="bar">
<input id="q" placeholder="搜尋檔名…" size="26">
<select id="src"><option value="">全部券商</option>{opts}</select>
<span class="sub" id="count"></span>
<span class="sub">點欄標排序 · 點一列看並排全文</span></div>
<table><thead><tr>{ths}</tr></thead><tbody id="tb"></tbody></table>
<div id="detail"></div></main>
<script type="application/json" id="data">{data}</script>
<script>{_JS}</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="新舊抽取器並排比對（唯讀）")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4))
    ap.add_argument("--sample-per-source", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--text-chars", type=int, default=12000, help="每側嵌進報告的文字上限")
    ap.add_argument("--out", default=str(OUT_DIR / "compare.html"))
    args = ap.parse_args()

    files = _pick(
        sorted(p for p in SRC.rglob("*") if p.suffix.lower() == ".pdf"),
        args.sample_per_source,
        args.limit,
    )
    print(f"比對 {len(files)} 份，workers={args.workers}")

    recs: list[dict] = []
    with ProcessPoolExecutor(args.workers) as ex:
        for i, rec in enumerate(
            ex.map(_work, [(str(p), args.text_chars) for p in files], chunksize=2), 1
        ):
            recs.append(rec)
            if i % 50 == 0:
                print(f"  ...{i}/{len(files)}", flush=True)

    # 排序欄位要真的存在於紀錄裡，否則點欄標排序會把整欄當成 null 而靜默無效。
    for r in recs:
        r["tables"] = (r.get("block_types") or {}).get("table", 0)
        r["failed"] = len(r.get("pages_failed") or [])
    recs.sort(key=lambda r: (r.get("order_sim") is None, r.get("order_sim") or 0))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    out.write_text(
        _html(recs, {"generated": datetime.now().strftime("%Y-%m-%d %H:%M")}), encoding="utf-8"
    )

    jsonl = out.with_suffix(".jsonl")
    with jsonl.open("w", encoding="utf-8") as f:
        for r in recs:
            slim = {k: v for k, v in r.items() if k not in ("old_text", "new_text", "dropped")}
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")

    sims = [r["order_sim"] for r in recs if r.get("order_sim") is not None]
    print("\n=== compare_extractors summary ===")
    print(f"  樣本            : {len(recs)}")
    print(f"  抽取失敗        : {sum(1 for r in recs if r.get('error'))}")
    if sims:
        sims.sort()
        print(f"  順序相似度      : min {sims[0]} / p50 {sims[len(sims)//2]} / max {sims[-1]}")
        print(f"  相似度 <0.5     : {sum(1 for s in sims if s < 0.5)}")
    print(f"  多欄檔案        : {sum(1 for r in recs if (r.get('max_columns') or 1) > 1)}")
    print(f"  有頁級失敗      : {sum(1 for r in recs if r.get('pages_failed'))}")
    print(f"  HTML  : {out}")
    print(f"  JSONL : {jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
