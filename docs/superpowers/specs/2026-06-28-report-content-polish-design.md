# 深度研報內容編排優化 設計

> 日期：2026-06-28　分支：feat/report-content-polish（從 origin/main 開，main 已含封面/目錄/章節化版型與圖表）

## 動機

結構外殼（封面／目錄／關鍵發現卡片／圖表／頁尾）已上線。本次優化**內文「編排」**——把藏在段落裡的關鍵數字視覺化、讓重點跳出、提升閱讀質感。使用者選定四個方向全做。

## 目標（四方向）

1. **數據亮點卡片（KPI）**：LLM 在合適處輸出結構化關鍵指標，渲染成一排指標卡（大數字＋小標籤＋同比升降色＋來源）。
2. **重點 callout 引言框**：關鍵結論句以 markdown `>` 引言 → 左金邊＋淡金底樣式框。
3. **內文排版精修**：子標題層次（h3/h4）、引用標號 `[n]` 渲染成上標金色小徽章、引用來源懸掛縮排、外部參考清單分層。
4. **表格美化**：表頭金底白字、斑馬紋、首欄強調、金色細框。

## 非目標（Out of Scope）

- 不改報告六段固定結構與內容生成邏輯（只加 KPI/callout 的「可選」呈現）。
- 不動問答（/api/ask）路徑。
- KPI 不做啟發式抽數字（脆弱）；一律靠 LLM 結構化輸出（接地、可對應）。

## 架構與資料流

只動 `app/services/pdf.py`（渲染）、`app/services/report.py`（prompt）、`web/static/app/markdown.js`（live 預覽佔位）。`render_report_pdf` 簽章與回傳不變。沿用既有 `inject_charts`、`split_report`、`_render_fancy`。

各章 body 的處理管線（在 `_render_fancy` 內，僅 fancy 版型；簡版退化路徑不套這些）：

```
body_md
  → inject_charts(body)            # 既有：```chart → <figure><svg>
  → inject_kpi(...)               # 新：```kpi → <div class="kpi-strip">
  → （若 slug=="refs"）_normalize_refs(...)   # 新：依 [n] 穩健分條
  → markdown(...)                 # tables/fenced_code/sane_lists（既有）
  → （若 slug ∉ {refs, extrefs}）cite_badges(html)   # 新：[n] → <sup class="cite">
```

新 CSS 全部加進既有 `_FANCY_CSS`（簡版 `_PAGE_CSS` 不動）。

### 1. KPI 數據亮點卡片

- **LLM 輸出格式**（fenced，比照 ```chart）：
  ```
  ```kpi
  {"items":[{"label":"2026 營收年增","value":"+30.2%","change":"YoY","dir":"up"},…],"source":"[1]"}
  ```
  ```
  - `items`：每張卡 `{label, value, change?, dir?}`。`dir ∈ {"up","down"}` 決定 change 顏色（綠／紅，支援負向/衰退）；省略則中性灰。
  - `source`：來源編號字串，顯示於右下「來源 [n]」。
- **`inject_kpi(markdown_text) -> str`**（pdf.py，比照 inject_charts）：解析 JSON → `<div class="kpi-strip"><div class="kpi">…</div>…</div>`；JSON 壞或 `items` 空 → 移除該塊＋`logger.warning`。所有文字 `html.escape`。卡片數不限制（CSS table-cell 自適應），但 prompt 引導 3–5 張。
- **版面**：`.kpi-strip` 用 `display:table; table-layout:fixed`（WeasyPrint 穩定，避免 flex 邊角）；`.kpi` 為 `table-cell`、金色上框、淡底、圓角、`page-break-inside:avoid`。
- **prompt 規則**：在執行摘要或重點分析開頭，若有 3–5 個可比較關鍵指標，可用 ```kpi 輸出；數字必須來自參考片段或網路、可對應、標 source；無可靠數據則不用。

### 2. 重點 callout 引言框

- markdown 原生 `>` 引言 → `.s-body blockquote` CSS（左 4px 金邊、淡金底 `#faf6ee`、圓角、深色字）。純呈現。
- **prompt 規則（輕量）**：關鍵結論或核心觀點可用 `>` 引言強調（精簡 1–2 句，全篇少量）。

### 3. 內文排版精修

- **子標題**：`h3`（12pt 金棕 `#9c6a16` 粗體）、`h4`（10.5pt 灰粗體）。模型既有 `###`/`####` 與「4.1」散文標題沿用。
- **引用標號徽章**：`cite_badges(html)` 以 `re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")` 把 `[1]`、`[1,2]`、`[1、2]` 換成 `<sup class="cite">…</sup>`（金色上標小字）。**在 markdown 渲染後**套用，且**只對 slug ∉ {refs, extrefs}** 的章節（引用來源/外部參考的 `[n]` 是清單標籤、URL 也含括號，需排除）。
- **引用來源懸掛縮排**：`.s-refs .s-body p { padding-left:1.9em; text-indent:-1.9em }`；並以 **`_normalize_refs(body)`** 在渲染前確保每個 `^\[\d+\]` 起新段落（`re.sub` 在非段落起始的 `[n]` 前插空行），不靠模型空行也能一條一行。
- **外部參考清單**：`.s-extrefs .s-body ul` 去項目符號、每項左金細線、連結金色。

### 4. 表格美化

- 覆蓋 `_FANCY_CSS` 既有 table 規則：`thead th` 金底白字、`tbody tr:nth-child(even)` 淡金斑馬、`tbody td:first-child` 粗體、金色細框、`td/th` padding。純 CSS。

### live 預覽（markdown.js）

- 比照既有 ```chart 佔位，新增 ```kpi → 顯示「（重點數據）」佔位，避免串流時露出 JSON。

## 錯誤處理

- `inject_kpi` 壞 JSON／空 items → 移除塊＋warning（同 inject_charts）。
- `cite_badges`、`_normalize_refs` 對空字串安全；正則僅命中數字括號，不動其他內容。
- 簡版退化路徑（無標題/章節<3）不套以上處理，行為不變。

## 測試

`tests/test_pdf.py`（沿用 `sys.path.insert`、`uv run pytest`）：
- `inject_kpi`：正常 → `kpi-strip`＋每張 `kpi-value`/`kpi-label`／`dir` class／來源；壞 JSON/空 items → 移除＋無 `kpi-strip`；無 ```kpi → 原樣。
- `cite_badges`：`[1]`/`[1,2]` → `<sup class="cite">`；無數字括號不動。
- `_normalize_refs`：連續 `[n]` 行 → 各自成段。
- 整合 `render_report_pdf`：含 KPI/表格/引言/徽章的 fancy 研報仍回 `b"%PDF"`；引用來源段的 `[n]` **不**變徽章。
- 既有 inject_charts／版型／簡版測試不回歸。
- 視覺驗證（手動）：`uv run --with pymupdf python` 出 PDF→PNG 目視四項。

`tests/test_report.py`：`REPORT_SYSTEM_PROMPT` 含 KPI（```kpi、3–5、不得杜撰）與 callout（`>` 引言）規則子串；既有 prompt 斷言不回歸。

`web/static/app/markdown.test.mjs`：```kpi → 佔位「（重點數據）」；既有 chart 佔位不回歸。

## 部署

無 schema 變更。沿用 Noto Sans CJK。合併後 `sudo systemctl restart report-mark-web.service`。
