# 研報後端／內容 Claude Design 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓深度研報的「後端生成」在內容結構更豐富、並讓匯出 PDF 與新站內 HTML 檢視器共用同一套 Claude Design 視覺語言；同時新增餵 HTML 檢視器的 JSON 讀取端點。

**Architecture:** 三塊互相獨立、皆可純測：(1) `GET /api/report-doc/{id}` JSON 端點 + `fetch_report_view` 讀取 helper 餵前端檢視器；(2) `REPORT_SYSTEM_PROMPT` 由「固定 5 段」放寬為「四錨點＋可增主題章節」；(3) `pdf.py`/`chart.py` 重排成 Claude Design。核心不變式：**同一份 raw markdown（含 ```kpi/```chart 圍欄與 [n] 標記）是唯一真相來源**——HTML 端前端 parse、PDF 端 `pdf.py` 展開；持久化不預展開，`report_doc` schema 不動。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy async(`text()`)、WeasyPrint、python-markdown、stdlib inline-SVG。測試用 unittest-class 跑於 pytest。

設計來源：已核准計畫 `~/.claude/plans/glittery-crunching-avalanche.md`（本計畫為其 Phase 1＋3 的可執行拆解）。

## Global Constraints

- 繁體中文回覆；程式碼/識別字/路徑保留原文；**不加裝飾 emoji**。
- **保留所有 `inject_kpi`/`inject_charts` 的 isinstance 守門與 `chart._valid`／`_is_num` 型別守門**——對非 dict 做 `.get()` 會 500 整份研報。
- **`test_chart.py` 的 `<rect>` 計數契約不可破**：單序列 bar=3、雙序列 bar=6（4 長條＋2 圖例）；新增格線一律 `<line>`、絕不 `<rect>`。
- **「輸出首字元＝`# 標題`」規則（prompt 規則 10）不可動**——`strip_preamble`/`split_report` 依賴它。
- `_FANCY_CSS` 只能用 `.replace("$TOKEN$", value)` 注入，**不可用 `%`-formatting**（CSS 內字面 `%` 會炸 `unsupported format character`）。
- WeasyPrint **不渲染 `box-shadow`**：一律以框線（`1px solid`）表現卡片層次。
- PDF 標題字型堆疊**必須以 `"Noto Serif CJK TC"` 開頭**（部署機 `fc-match "Noto Serif TC"` 會退回 sans，該家族未安裝）。
- 目標色值：裝飾金 `#AE7415`；本文/小字金 `#8a5a0f`；金底 tint `#faf3e3`；ink `#101828`/`#344054`/`#667085`；border `#e4e7ec`；表頭帶 `#f2f4f7`；canvas `#f6f7f9`。圖表分類色盤 `#5856d6,#007aff,#34c759,#ff9500,#ff3b30,#00c7be,#af52de,#ff2d55,#a2845e`。
- 執行：`uv run pytest <path>` 跑測試；分支自 `origin/main`；stage 明確路徑（勿 `git add -A`，工作樹有他人 WIP）。

---

### Task 1: JSON 讀取端點 `GET /api/report-doc/{id}` + `fetch_report_view`

**Files:**
- Modify: `app/services/report.py`（於 `fetch_report_doc` 後、`reports_for_conversation` 前插入 `fetch_report_view`，約 line 172）
- Modify: `web/server.py`（import 加 `fetch_report_view`；於 `report_doc_pdf` 後、`/api/feedback` 前加新路由，約 line 755）
- Test: `tests/test_report_endpoint.py`（新增一個 class）

**Interfaces:**
- Produces: `fetch_report_view(report_id: str) -> dict | None`，回 `{id, title, question, markdown, sources: list, created_at: str, thinking_ms, download_url: str}`（`markdown` 為含圍欄的 raw 文字；`sources` 為 list）。
- Produces: `GET /api/report-doc/{report_id}` → 200 該 dict／404（bad uuid 或查無）。前端 `getReportDoc` 消費（另一份 plan）。

- [ ] **Step 1: 寫失敗測試（端點）**

在 `tests/test_report_endpoint.py` 檔尾 `if __name__` 前加：

```python
from unittest import mock  # noqa: E402


class ReportDocViewEndpointTests(unittest.TestCase):
    _DOC = {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "title": "台積電深度研報",
        "question": "台積電前景",
        "markdown": "# 台積電深度研報\n\n## 執行摘要\n\n```kpi\n{\"items\":[]}\n```\n本文 [1]",
        "sources": [{"n": 1, "report_id": "r1", "file_name": "f.pdf",
                     "market": "TW", "report_date": "2026-06-20", "is_latest": False}],
        "created_at": "2026-07-08T00:00:00+00:00",
        "thinking_ms": 1234,
        "download_url": "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf",
    }

    def test_bad_uuid_returns_404(self):
        client = _authed_client()
        r = client.get("/api/report-doc/not-a-uuid")
        self.assertEqual(r.status_code, 404)

    def test_valid_returns_doc(self):
        client = _authed_client()
        with mock.patch("web.server.fetch_report_view",
                        new=mock.AsyncMock(return_value=self._DOC)):
            r = client.get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("```kpi", body["markdown"])          # 圍欄未被預展開（單一真相）
        self.assertEqual(body["download_url"],
                         "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(body["sources"][0]["n"], 1)
        self.assertEqual(body["thinking_ms"], 1234)

    def test_missing_returns_404(self):
        client = _authed_client()
        with mock.patch("web.server.fetch_report_view",
                        new=mock.AsyncMock(return_value=None)):
            r = client.get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(r.status_code, 404)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_report_endpoint.py::ReportDocViewEndpointTests -v`
Expected: FAIL（`fetch_report_view` 尚未存在／路由 404 因未定義 → import 或 404 錯）

- [ ] **Step 3: 實作 `fetch_report_view`（report.py，插在 `fetch_report_doc` 之後）**

```python
async def fetch_report_view(report_id: str) -> dict | None:
    """餵站內 HTML 檢視器：回含 raw markdown（圍欄未展開）、sources、thinking_ms。"""
    async with SessionFactory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, title, question, markdown, sources, thinking_ms, created_at "
                    "FROM research.report_doc WHERE id = :id"
                ),
                {"id": report_id},
            )
        ).first()
    if row is None:
        return None
    sources = row[4]
    if isinstance(sources, str):  # jsonb 可能以 str 或已解析 list 回來
        try:
            sources = json.loads(sources)
        except (ValueError, TypeError):
            sources = []
    created_at = row[6]
    return {
        "id": str(row[0]),
        "title": row[1],
        "question": row[2],
        "markdown": row[3],
        "sources": sources or [],
        "thinking_ms": row[5],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else ""),
        "download_url": f"/api/report-doc/{report_id}/pdf",
    }
```

- [ ] **Step 4: 實作路由（server.py，於 `report_doc_pdf` 之後）＋補 import**

在 `web/server.py` import `fetch_report_doc` 那行加入 `fetch_report_view`（同一 `from app.services.report import ...`），並新增：

```python
@app.get("/api/report-doc/{report_id}")
async def report_doc_view(report_id: str):
    """站內 HTML 研報檢視器的資料來源：回 raw markdown＋sources＋meta（唯讀）。"""
    if not _valid_uuid(report_id):
        raise HTTPException(status_code=404, detail="report not found")
    doc = await fetch_report_view(report_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="report not found")
    return doc
```

（auth 由 deny-by-default middleware 自動覆蓋；`/pdf` 為更具體路徑會先匹配，兩者不衝突。）

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run pytest tests/test_report_endpoint.py -v`
Expected: PASS（新 class 3 項 + 既有 guard 測全綠）

- [ ] **Step 6: Commit**

```bash
git add app/services/report.py web/server.py tests/test_report_endpoint.py
git commit -m "$(cat <<'EOF'
feat(研報): 新增 /api/report-doc/{id} JSON 端點餵站內 HTML 檢視器

fetch_report_view 回 raw markdown（圍欄未展開，維持單一真相來源）＋
sources＋thinking_ms＋created_at＋download_url；auth 由既有 middleware 覆蓋。
EOF
)"
```

---

### Task 2: 內容結構 prompt 放寬（四錨點＋可增主題章節）

**Files:**
- Modify: `app/services/report.py`（`REPORT_SYSTEM_PROMPT` 規則 2，約 line 58-60；`build_report_prompt` 尾句，line 117）
- Test: `tests/test_report.py`（新增 prompt 契約測試 class）

**Interfaces:**
- Consumes：`_render_fancy` 的 `slug = _SECT_SLUG.get(name, "sec")` 已對未知 H2 安全降級（不需改 `_SECT_SLUG`）。
- Produces：無新函式；改動為常數字串與尾句。

- [ ] **Step 1: 寫失敗測試（prompt 契約）**

在 `tests/test_report.py` 新增（若無此檔則建立同 `test_chart.py` 的 header 樣板）：

```python
class ReportPromptStructureTests(unittest.TestCase):
    def test_prompt_keeps_four_anchors_and_cap_drops_fixed(self):
        from app.services.report import REPORT_SYSTEM_PROMPT, build_report_prompt
        for anchor in ("執行摘要", "關鍵發現", "風險與展望", "引用來源"):
            self.assertIn(anchor, REPORT_SYSTEM_PROMPT)
        self.assertIn("上限 9", REPORT_SYSTEM_PROMPT)          # 硬上限防碎片化
        self.assertNotIn("結構固定", REPORT_SYSTEM_PROMPT)      # 不再宣稱固定
        self.assertIn("第一個字元", REPORT_SYSTEM_PROMPT)       # 規則 10 首字元不變
        prompt = build_report_prompt("台積電", "片段", "台積電深度研報")
        self.assertNotIn("固定結構", prompt)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_report.py::ReportPromptStructureTests -v`
Expected: FAIL（`結構固定`/`固定結構` 仍在、`上限 9` 尚未出現）

- [ ] **Step 3: 改 `REPORT_SYSTEM_PROMPT` 規則 2（report.py:58-60）**

把現行三行（`"2. 一律繁體中文，輸出 Markdown，結構固定：\n" ... "   ## 風險與展望\n   ## 引用來源\n"`）替換為：

```python
    "2. 一律繁體中文，輸出 Markdown。結構採下列『建議骨架』，可依主題深度增刪主題章節，"
    "但必須保留四個錨點章節：執行摘要、關鍵發現、風險與展望、引用來源。\n"
    "   # （研報標題）\n   ## 執行摘要\n   ## 關鍵發現\n"
    "   ## （可依深度自行新增 1–5 個主題章節，例如：財務表現／估值分析／競爭格局／產業趨勢／重點分析）\n"
    "   ## 風險與展望\n   ## 引用來源\n"
    "   全篇 ## 章節總數上限 9 個；主題章節可用 ### 子標題分層深入；"
    "跨標的、跨期間或組成對比時，優先以 Markdown 表格呈現。\n"
```

- [ ] **Step 4: 改 `build_report_prompt` 尾句（report.py:117）**

把 `parts.append("請依系統指示的固定結構，輸出完整的 Markdown 研報。")` 改為：

```python
    parts.append(
        "請依系統指示的建議骨架（保留四個錨點章節，並視深度自行新增主題章節與 ### 子標題、"
        "善用表格），輸出結構豐富、可交付的 Markdown 研報。"
    )
```

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run pytest tests/test_report.py::ReportPromptStructureTests -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/report.py tests/test_report.py
git commit -m "$(cat <<'EOF'
feat(研報): 內容結構由固定 5 段放寬為四錨點＋可增主題章節

保留執行摘要/關鍵發現/風險與展望/引用來源四錨點，中間可依深度自行新增
1–5 個主題章節、鼓勵 ### 子標題與表格；硬上限 9 段防碎片化，首字元＝#
標題規則不動。PDF 端 _SECT_SLUG 對未知段已安全降級（見 Task 4 回歸測試）。
EOF
)"
```

---

### Task 3: `pdf.py` `_FANCY_CSS`／`_PAGE_CSS` 重排成 Claude Design（含去重）

**Files:**
- Modify: `app/services/pdf.py`（色彩常數 27-30；`_PAGE_CSS` 44-66；`_FANCY_CSS` 83-191）
- Test: 新 `tests/test_pdf_structure.py`（**不 import weasyprint**，恆跑）

**Interfaces:**
- Consumes：`_render_fancy(title, sections, meta)`（既有，未改簽名）。
- Produces：`_FANCY_CSS`/`_PAGE_CSS` 字串內容改變；`_render_fancy` 對未知章節輸出 `class="s-sec"`（既有行為，測試鎖定）。

- [ ] **Step 1: 寫失敗測試（結構斷言，不需 weasyprint）**

建立 `tests/test_pdf_structure.py`：

```python
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.pdf import _FANCY_CSS, _render_fancy  # noqa: E402


class FancyCssStructureTests(unittest.TestCase):
    def test_serif_heading_stack_and_gold_text(self):
        self.assertIn("Noto Serif CJK TC", _FANCY_CSS)     # 標題襯線、且列於首位
        self.assertIn("#8a5a0f", _FANCY_CSS)               # 本文/小字金
        self.assertNotIn("box-shadow", _FANCY_CSS)         # WeasyPrint 不渲染、以框線代之

    def test_no_duplicate_h3_or_table_blocks(self):
        # 去重：h3 選擇器與 table 選擇器各只定義一次（避免靠 source order 覆蓋）
        self.assertEqual(len(re.findall(r"(?m)^\s*h3\s*\{", _FANCY_CSS)), 1)
        self.assertEqual(len(re.findall(r"(?m)^\s*table\s*\{", _FANCY_CSS)), 1)


class SectionAgnosticRenderTests(unittest.TestCase):
    def test_unknown_section_renders_generically_with_cites(self):
        html = _render_fancy(
            "測試標題",
            [("執行摘要", "摘要本文 [1]"),
             ("估值分析", "本益比偏低 [2]"),      # 未知主題章節
             ("風險與展望", "風險 [3]")],
            {"date": "2026-07-08"},
        )
        self.assertIn('class="s-sec"', html)            # 未知段落 slug=sec
        self.assertIn("估值分析", html)                  # 出現於 TOC 與 H2
        self.assertEqual(html.count("估值分析"), 2)      # TOC li + section h2
        self.assertIn('<sup class="cite">2</sup>', html)  # 未知段仍有引用徽章
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_pdf_structure.py -v`
Expected: `FancyCssStructureTests` FAIL（現含 `box-shadow`、無 `#8a5a0f`、h3 定義 2 次）；`SectionAgnosticRenderTests` 可能已 PASS（既有行為）——保留為回歸鎖定。

- [ ] **Step 3: 換色彩常數（pdf.py:27-30）**

把 `BRAND_NAME/BRAND_GOLD/_GOLD_SOFT/_GOLD_LINE` 區塊替換為：

```python
BRAND_NAME = "廷豐智能研報"
BRAND_GOLD = "#AE7415"   # 裝飾/大字金：封面標題、KPI 頂線、編號徽章、圖表 accent
GOLD_TEXT = "#8a5a0f"    # 本文/小字金：引用徽章、封面 kicker/foot、連結
GOLD_TINT = "#faf3e3"    # 淡金底：執行摘要框、引言 callout
_SERIF = '"Noto Serif CJK TC","Noto Serif TC","Noto Sans CJK TC",serif'
_INK = "#101828"
_INK2 = "#344054"
_INK3 = "#667085"
_BORDER = "#e4e7ec"
_BAND = "#f2f4f7"
_CANVAS = "#f6f7f9"
```

- [ ] **Step 4: 換 `_FANCY_CSS`（pdf.py:83-191，整段替換）**

```python
_FANCY_CSS = (
    """
@page { size: A4; margin: 20mm 18mm 18mm 18mm;
  @bottom-center { content: "$BRANDNAME$　·　" counter(page) " / " counter(pages);
    font-size: 8.5pt; color: $INK3$; } }
@page:first { margin: 0; @bottom-center { content: none; } }
body { font-family: "Noto Sans CJK TC","Noto Sans CJK SC","Noto Sans TC",sans-serif;
  color: $INK2$; font-size: 10.5pt; line-height: 1.75; }

/* 封面 */
.cover { page-break-after: always; height: 100vh; padding: 40mm 24mm;
  box-sizing: border-box; position: relative;
  background: linear-gradient(180deg,#ffffff 0%,$CANVAS$ 100%); }
.cover-brand { font-family: $SERIF$; color: $GOLD$; font-size: 15pt; font-weight: 700; letter-spacing: 2px; }
.cover-rule { height: 3px; width: 56px; background: $GOLD$; margin: 10px 0 0; }
.cover-mid { position: absolute; top: 42%; left: 24mm; right: 24mm; }
.cover-kicker { color: $GOLDT$; font-size: 11pt; letter-spacing: 4px; margin-bottom: 10px; }
.cover-title { font-family: $SERIF$; font-size: 30pt; line-height: 1.3; color: $INK$; margin: 0; font-weight: 700; }
.cover-date { color: $INK3$; font-size: 11pt; margin-top: 18px; }
.cover-foot { position: absolute; bottom: 26mm; left: 24mm; color: $GOLDT$;
  font-size: 9pt; letter-spacing: 1px; }

/* 目錄 */
.toc { page-break-after: always; padding-top: 6mm; }
.toc-h { font-family: $SERIF$; color: $INK$; font-size: 16pt; font-weight: 700;
  border-bottom: 2px solid $BORDER$; padding-bottom: 6px; margin-bottom: 14px; }
.toc ol { list-style: none; counter-reset: toc; padding: 0; }
.toc li { counter-increment: toc; margin: 9px 0; font-size: 11.5pt; }
.toc a { color: $INK2$; text-decoration: none; }
.toc a::before { content: counter(toc,decimal-leading-zero) "　"; color: $GOLDT$; font-weight: 700; }
.toc a::after { content: leader('·') target-counter(attr(href), page); color: $INK3$; }

/* 章節 */
section[id] { margin-top: 16px; }
h2 { font-family: $SERIF$; font-size: 15pt; color: $INK$; font-weight: 700;
  margin: 18px 0 10px; padding-bottom: 5px; border-bottom: 1.5px solid $BORDER$; }
.s-body p { margin: 7px 0; text-align: justify; }
h3 { font-family: $SERIF$; font-size: 12pt; color: $INK2$; font-weight: 700; margin: 15px 0 6px; }
h4 { font-size: 10.5pt; color: $INK3$; font-weight: 700; margin: 11px 0 4px; }
a { color: $GOLDT$; text-decoration: none; }

/* 執行摘要：淡金底色框 */
.s-exec .s-body { background: $TINT$; border: 1px solid $BORDER$;
  border-left: 4px solid $GOLD$; border-radius: 12px; padding: 12px 16px; }

/* 關鍵發現：卡片＋編號徽章（以框線表現層次，WeasyPrint 不渲染 box-shadow） */
.s-findings ol { list-style: none; counter-reset: f; padding: 0; }
.s-findings li { counter-increment: f; position: relative; background: #fff;
  border: 1px solid $BORDER$; border-radius: 12px; padding: 11px 14px 11px 46px;
  margin: 9px 0; page-break-inside: avoid; }
.s-findings li::before { content: counter(f); position: absolute; left: 12px; top: 11px;
  width: 24px; height: 24px; background: $GOLD$; color: #fff; border-radius: 50%;
  font-size: 11pt; font-weight: 700; text-align: center; line-height: 24px; }

/* 圖表 */
figure.chart { margin: 14px 0; text-align: center; page-break-inside: avoid; }
figure.chart svg { max-width: 100%; height: auto; }
figcaption { font-size: 9pt; color: $INK3$; margin-top: 4px; }

/* 引用標號：上標金色小徽章 */
sup.cite { color: $GOLDT$; font-size: 0.68em; font-weight: 700;
  vertical-align: super; padding: 0 0.5px; letter-spacing: 0.5px; }

/* 重點引言 callout */
.s-body blockquote { margin: 11px 0; padding: 10px 15px; background: $TINT$;
  border: 1px solid $BORDER$; border-left: 4px solid $GOLD$;
  border-radius: 0 12px 12px 0; color: $INK2$; }
.s-body blockquote p { margin: 3px 0; font-size: 10.5pt; }

/* 數據亮點卡片（WeasyPrint flex 弱 → 續用 table-cell，僅換 Claude 配色） */
.kpi-strip { display: table; width: 100%; border-spacing: 8px 0; margin: 14px 0;
  table-layout: fixed; }
.kpi { display: table-cell; background: #fff; border: 1px solid $BORDER$;
  border-top: 3px solid $GOLD$; border-radius: 12px; padding: 11px 8px;
  text-align: center; page-break-inside: avoid; vertical-align: top; }
.kpi-value { font-size: 18pt; font-weight: 700; color: $INK$; line-height: 1.15;
  font-variant-numeric: tabular-nums; }
.kpi-label { font-size: 8.5pt; color: $INK3$; margin-top: 4px; line-height: 1.3; }
.kpi-change { font-size: 8.5pt; margin-top: 3px; font-weight: 700; }
.kpi-change.up { color: #248a3d; }
.kpi-change.down { color: #b42318; }
.kpi-src { font-size: 8pt; color: $INK3$; text-align: right; margin: 2px 4px 0; }

/* 表格：冷灰表頭帶＋髮絲列（單一定義） */
table { border-collapse: collapse; width: 100%; margin: 11px 0; }
thead th { background: $BAND$; color: $INK2$; font-weight: 700; font-size: 9.5pt; }
td, th { border: 1px solid $BORDER$; padding: 6px 10px; font-size: 9.5pt; }
tbody td:first-child { font-weight: 700; color: $INK$; }

/* 引用來源：懸掛縮排 */
.s-refs .s-body p { padding-left: 1.9em; text-indent: -1.9em; border-left: none;
  font-size: 9.5pt; color: $INK3$; margin: 5px 0; }

/* 外部參考：清單分層 */
.s-extrefs .s-body ul { list-style: none; padding: 0; }
.s-extrefs .s-body li { padding: 5px 0 5px 14px; border-left: 2px solid $BORDER$;
  margin: 6px 0; }
.s-extrefs .s-body a { font-size: 10pt; }
"""
    .replace("$BRANDNAME$", BRAND_NAME)
    .replace("$SERIF$", _SERIF)
    .replace("$GOLDT$", GOLD_TEXT)
    .replace("$GOLD$", BRAND_GOLD)
    .replace("$TINT$", GOLD_TINT)
    .replace("$INK2$", _INK2)
    .replace("$INK3$", _INK3)
    .replace("$INK$", _INK)
    .replace("$BORDER$", _BORDER)
    .replace("$BAND$", _BAND)
    .replace("$CANVAS$", _CANVAS)
)
```

注意 replace 順序：`$INK2$`/`$INK3$` 必須在 `$INK$` 之前替換（否則 `$INK$` 不會誤傷，但為保險先長後短）；`$GOLDT$` 在 `$GOLD$` 之前。去重點：原本 `h3` 出現於 120 與 145、`table` 出現於 122-124 與 171-175，本整段替換後各只剩一條。

- [ ] **Step 5: 同步更新 `_PAGE_CSS`（pdf.py:44-66，退化簡版也套 Claude Design）**

把 `_PAGE_CSS` 內 `h1/h2/h3` 加襯線與冷灰、`th` 背景改帶色：將 `body{...color:#222}`→`color:$INK2$`；`h1/h2/h3` 前加 `font-family:$SERIF$`、色改 `$INK$`/`$INK$`/`$INK2$`；`h2` 的 `border-left`/color 由 gold 改為 `color:$INK$;border-left:4px solid $GOLD$`（保留左槓裝飾）；`th{background:#faf3e6}`→`$BAND$`；`a{color:$GOLD$}`→`$GOLDT$`。仍用既有 `% {"gold":...}` 會與新 token 混用——**改為與 `_FANCY_CSS` 相同的 `.replace()` 鏈**（移除 `% {"gold": BRAND_GOLD}`，改逐一 `.replace("$SERIF$",_SERIF).replace("$GOLD$",BRAND_GOLD)...`），避免 `%` 格式化與 CSS `%` 衝突。

- [ ] **Step 6: 跑測試確認通過**

Run: `uv run pytest tests/test_pdf_structure.py -v`
Expected: PASS（4 項全綠）

- [ ] **Step 7: Commit**

```bash
git add app/services/pdf.py tests/test_pdf_structure.py
git commit -m "$(cat <<'EOF'
feat(研報): PDF 重排成 Claude Design（襯線標題/冷灰/正確金色）＋去重

標題改 Noto Serif CJK TC 襯線、本文金改 #8a5a0f、冷灰 ink ramp、卡片 radius 12
以框線代 box-shadow、表格改冷灰表頭帶。收斂重複的 h3(×2)/table(×2) 定義。
新增 tests/test_pdf_structure.py（免 weasyprint）鎖定樣式與未知章節安全降級。
EOF
)"
```

---

### Task 4: `chart.py` 重排（語意色盤／圓角長條／格線／圖例換行）

**Files:**
- Modify: `app/services/chart.py`（`_PALETTE` 17；`_axes` 64-76；`_bar` 106-135；`_legend` 91-103；x-labels/title 色）
- Test: `tests/test_chart.py`（既有計數測試保留＋新增樣式測試）

**Interfaces:**
- Consumes：`_yrange`/`_ymap`（既有）。新增純函式 `_yticks(lo, hi, n=5) -> list[float]`。
- Produces：SVG 輸出改樣式；`<rect>` 計數契約不變（bars/legend swatch 仍 `<rect>`，格線為 `<line>`）。

- [ ] **Step 1: 寫失敗測試（新樣式）**

在 `tests/test_chart.py` 的 `if __name__` 前加：

```python
class ChartRestyleTests(unittest.TestCase):
    def test_bars_rounded_and_new_palette(self):
        spec = {"type": "bar", "title": "t", "x": ["a", "b", "c"],
                "series": [{"name": "s", "values": [10, 20, 30]}]}
        svg = render_chart_svg(spec)
        self.assertIn('rx="3"', svg)               # 圓角長條
        self.assertIn("#5856d6", svg)              # 新色盤首色
        self.assertEqual(svg.count("<rect"), 3)    # 計數契約不變

    def test_has_gridlines(self):
        spec = {"type": "bar", "title": "t", "x": ["a", "b"],
                "series": [{"name": "s", "values": [10, 40]}]}
        svg = render_chart_svg(spec)
        self.assertGreater(svg.count("<line"), 2)  # 縱軸＋多條格線＋零線

    def test_legend_wraps_long_cjk_names(self):
        longname = "非常長的中文數列名稱一二三四五六七八九十"
        spec = {"type": "bar", "title": "t", "x": ["a", "b"],
                "series": [{"name": longname + "甲", "values": [1, 2]},
                           {"name": longname + "乙", "values": [3, 4]}]}
        svg = render_chart_svg(spec)
        ys = set(re.findall(r'<text[^>]*y="(\d+)"[^>]*fill="#667085"', svg))
        self.assertGreaterEqual(len(ys), 2)        # 圖例換至 ≥2 列
        self.assertEqual(svg.count("<rect"), 6)    # 4 長條＋2 圖例 swatch
```

在 `tests/test_chart.py` 頂部 import 區加 `import re`（若無）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_chart.py::ChartRestyleTests -v`
Expected: FAIL（無 `rx="3"`／舊色盤／固定 120px 圖例不換行）

- [ ] **Step 3: 換 `_PALETTE`（chart.py:17）**

```python
_PALETTE = ["#5856d6", "#007aff", "#34c759", "#ff9500", "#ff3b30",
            "#00c7be", "#af52de", "#ff2d55", "#a2845e"]  # 語意市場色盤（GLOBAL/US/TW/HK/CN/FX/WTX/MACRO/CRYPTO）
```

- [ ] **Step 4: 新增 `_yticks` 並改寫 `_axes`（chart.py，替換 64-76）**

```python
def _yticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """回涵蓋 [lo,hi] 的 ~n 個『整齊』刻度值。"""
    span = (hi - lo) or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        if span / (m * mag) <= n:
            step = m * mag
            break
    start = math.ceil(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _axes(lo: float, hi: float) -> str:
    zero_y = _ymap(0.0, lo, hi)
    out = [f'<line x1="{_PAD_L}" y1="{_PAD_T}" x2="{_PAD_L}" y2="{_BASE_Y}" stroke="#e4e7ec"/>']
    for val in _yticks(lo, hi):
        y = _ymap(val, lo, hi)
        out.append(f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W - _PAD_R}" y2="{y:.1f}" stroke="#eceef2"/>')
        out.append(
            f'<text x="{_PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="#667085">{val:g}</text>'
        )
    out.append(f'<line x1="{_PAD_L}" y1="{zero_y:.1f}" x2="{_W - _PAD_R}" y2="{zero_y:.1f}" stroke="#c8ccd4"/>')
    return "".join(out)
```

- [ ] **Step 5: 圓角長條（chart.py:_bar，`group_w` 與 rect）**

把 `group_w = step * 0.7` 改 `group_w = step * 0.62`；把該 rect 產生行改為附 `rx="3"`：

```python
            out.append(
                f'<rect x="{bx:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                f'rx="3" fill="{c}"/>'
            )
```

單序列數值標籤色 `fill="#555"`→`fill="#667085"`（該 `<text>` 行）。

- [ ] **Step 6: 圖例流式換行（chart.py:_legend，替換 91-103）**

```python
def _legend(series: list) -> str:
    if len(series) < 2:
        return ""
    out = []
    x, y = _PAD_L, _H - 22
    for i, s in enumerate(series):
        c = _PALETTE[i % len(_PALETTE)]
        name = str(s.get("name") or "")
        text_w = sum(11 if ord(ch) > 0x2E7F else 6 for ch in name)  # CJK ~11px、ASCII ~6px @font-size 10
        item_w = 10 + 4 + text_w + 18
        if x + item_w > _W - _PAD_R and x > _PAD_L:
            x, y = _PAD_L, y + 16
        out.append(f'<rect x="{x}" y="{y}" width="10" height="10" fill="{c}"/>')
        out.append(f'<text x="{x + 14}" y="{y + 9}" font-size="10" fill="#667085">{_esc(name)}</text>')
        x += item_w
    return "".join(out)
```

（x-labels `fill="#555"`→`"#667085"`、title `fill="#1a1a1a"`→`"#101828"` 一併微調，同檔對應行。）

- [ ] **Step 7: 跑測試確認全綠（含既有計數契約）**

Run: `uv run pytest tests/test_chart.py -v`
Expected: PASS（既有 9 項 rect/circle/path 計數＋新 3 項）

- [ ] **Step 8: Commit**

```bash
git add app/services/chart.py tests/test_chart.py
git commit -m "$(cat <<'EOF'
feat(研報): 圖表重排成 Claude Design（語意色盤/圓角長條/格線/圖例換行）

_PALETTE 換語意市場色盤、長條加 rx 圓角、y 軸加整齊刻度與淡格線（<line>）＋
強調零線、圖例改流式換行修長中文名重疊。保留 <rect> 計數契約與負值/零總和守門。
EOF
)"
```

---

### Task 5: 部署註記 + 單一真相守門測試

**Files:**
- Modify: `docs/qa_pdf_report_deployment.md`（字型段，約 15-22）
- Test: `tests/test_report.py`（`fetch_report_view` 的圍欄保留守門）

- [ ] **Step 1: 寫守門測試（fetch_report_view 不預展開圍欄）**

於 `tests/test_report.py` 新增（用 `unittest.mock` stub SessionFactory 的 execute 回傳 row，模式沿用該檔既有對 `fetch_report_doc` 的測法；若該檔尚無 DB stub 樣板，改以下述輕量測試鎖定「回傳 dict 保留圍欄」的契約）：

```python
class FetchReportViewContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_keeps_fences(self):
        import app.services.report as rpt
        from unittest import mock

        class _Row:
            def __init__(self, vals): self._v = vals
            def __getitem__(self, i): return self._v[i]

        raw_md = "# 標題\n\n```kpi\n{\"items\":[]}\n```\n\n```chart\n{}\n```\n本文 [1]"
        row = _Row(["id-1", "標題", "問題", raw_md,
                    [{"n": 1}], 999, None])

        class _Result:
            def first(self): return row

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, *a, **k): return _Result()

        with mock.patch.object(rpt, "SessionFactory", lambda: _Session()):
            doc = await rpt.fetch_report_view("id-1")
        self.assertIn("```kpi", doc["markdown"])     # 圍欄未展開
        self.assertIn("```chart", doc["markdown"])
        self.assertEqual(doc["download_url"], "/api/report-doc/id-1/pdf")
        self.assertEqual(doc["sources"], [{"n": 1}])
```

- [ ] **Step 2: 跑測試確認通過（Task 1 已實作 fetch_report_view）**

Run: `uv run pytest tests/test_report.py::FetchReportViewContractTests -v`
Expected: PASS

- [ ] **Step 3: 更新部署文件**

在 `docs/qa_pdf_report_deployment.md` 字型段落補一句：`fonts-noto-cjk` 同時提供 `Noto Serif CJK TC`（研報標題襯線依賴之，免額外安裝）；部署機驗證 `fc-list | grep -i "serif.*cjk"` 需非空，否則標題會退回 sans（無錯誤、僅樣式遺失）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_report.py docs/qa_pdf_report_deployment.md
git commit -m "$(cat <<'EOF'
test(研報): 鎖定 fetch_report_view 保留圍欄（單一真相）＋補襯線字型部署註記

守門測試確保讀取路徑不預展開 ```kpi/```chart（HTML 前端 parse、PDF 後端展開）；
部署文件標註 fonts-noto-cjk 已含 Noto Serif CJK TC，缺則標題退回 sans。
EOF
)"
```

---

## 全套驗證（所有 task 後）

```bash
uv run pytest tests/test_report_endpoint.py tests/test_report.py tests/test_pdf_structure.py tests/test_chart.py -v
uv run pytest      # 全套回歸（含既有 test_pdf 冒煙）
```

PDF 視覺人工驗證（確認襯線實際套用、非退回 sans）：

```bash
uv run python -c "
from app.services.pdf import render_report_pdf
md = open('/tmp/sample_report.md').read()   # 含 4 錨點＋1 主題章節＋```kpi＋```chart＋表格
open('/tmp/out.pdf','wb').write(render_report_pdf(md, title='樣本深度研報', meta={'date':'2026-07-08'}))
print('wrote /tmp/out.pdf')
"
```

肉眼檢查 `/tmp/out.pdf`：標題為襯線、金色為 #8a5a0f 系、KPI/表格/圖表配色、未知主題章節正常渲染。

## Self-Review

- **Spec 覆蓋**：Phase 1a(Task 1)、1b(Task 2)、3a(Task 3)、3b(Task 4)、3c＋單一真相(Task 5) 皆有對應 task。Phase 2（前端 HTML 檢視器）為另一份 plan，依賴本 plan 的 Task 1 端點。
- **不變式**：`<rect>` 計數（Task 4 測試明列）、isinstance 守門（未改 `inject_*`/`_valid`）、首字元 `#`（Task 2 保留規則 10）、單一真相（Task 5 守門）皆鎖定。
- **型別一致**：`fetch_report_view` 回傳鍵（id/title/question/markdown/sources/thinking_ms/created_at/download_url）於 Task 1 定義、Task 5 測試、與前端 plan 的 `reportDocSchema` 對齊。
- **無 placeholder**：各 step 附完整程式碼與明確指令。
