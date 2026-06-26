# 深度研報適時加入圖表

- 日期：2026-06-26
- 範圍：深度研報生成與 PDF 渲染路徑（`app/services/report.py` 的 `REPORT_SYSTEM_PROMPT`、`app/services/pdf.py`、新增 `app/services/chart.py`，前端 `web/static/app/ask.js` live 預覽佔位）。**不動** Q&A 路徑、schema、端點、檢索／瀏覽／總覽、`report_gate`。
- 前置：疊在 `feat/report-web-supplement`（PR #36）之上，擴充其改寫過的 `REPORT_SYSTEM_PROMPT`。

## 背景與動機

深度研報（PR #33／#36）目前輸出純文字 markdown（執行摘要／關鍵發現／重點分析／風險與展望／引用來源／外部參考），渲染成 PDF。使用者希望**適時加入圖表**，讓跨項目比較、隨時間趨勢、組成佔比等數據更直觀。

PDF 由 **WeasyPrint** 渲染（markdown→HTML→PDF），**不執行 JavaScript**，故 Chart.js／Plotly 等前端圖表庫無法用於 PDF；圖表必須是**伺服器端產生的靜態向量圖（SVG）**，embed 進 HTML 後由 WeasyPrint 渲染。

## 設計決策（已與使用者拍板）

1. **圖表數據＝嚴格接地、不杜撰**：只畫**參考片段或網路來源中實際出現**的數據，每圖標來源編號；查無可靠數據就不作圖。與既有「不臆測、不杜撰數據」規則一致，守研報可信度。
2. **支援三類圖**：長條（跨項目比較）、折線（隨時間趨勢）、圓餅（組成佔比）。
3. **觸發＝LLM 適時自我判斷**：當來源有明確可比較數據且作圖能提升直觀理解時才插入，不強制每篇都有圖。
4. **渲染＝輕量 SVG 自繪（不引入 matplotlib）**：新增純函式 `chart.py` 把圖規格畫成 inline SVG。理由：零新依賴；SVG 文字沿用既有 fontconfig 的 Noto CJK（中文免再處理，不踩 matplotlib 另一套字型登記的雷）；向量清晰；純函式好單測。

## 現況（變更前）

- `app/services/report.py`：`REPORT_SYSTEM_PROMPT`（web-supplement 版）固定結構 `# 標題 / ## 執行摘要 / ## 關鍵發現 / ## 重點分析 / ## 風險與展望 / ## 引用來源`，含網搜補充與「## 外部參考（網路）」規則。`generate_report` 串流 markdown → `render_report_pdf` → 持久化 `report_doc.markdown`。
- `app/services/pdf.py`：`render_report_pdf(markdown_text, *, title, meta)`：`markdown.markdown(text, extensions=["tables","fenced_code","sane_lists"])` → 包品牌 HTML → WeasyPrint。`_PAGE_CSS` 定義字型族（Noto Sans CJK TC…）與 h1~3/table 樣式。
- `app/services/llm.py`：`pillow` 已安裝（WeasyPrint 相依）；**matplotlib 未安裝**、無其他繪圖庫。
- 前端 `web/static/app/ask.js`：研報生成面板串流即時預覽 markdown 文字。

## 設計

### 1. 圖表規格（LLM 在 markdown 內輸出）

LLM 於相關分析段落附近插入 fenced 區塊（語言標籤 `chart`），內容為 JSON：

```chart
{"type":"bar","title":"2026 各廠特化材料營收（億元）",
 "x":["新應材","台特化","中砂"],
 "series":[{"name":"營收","values":[120,86,54]}],
 "unit":"億元","source":"[3]"}
```

欄位語意：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `type` | `"bar"｜"line"｜"pie"` | 圖型 |
| `title` | str | 圖標題（含單位更佳） |
| `x` | list[str] | 長條/折線＝類別或時間軸；圓餅＝各扇形標籤 |
| `series` | list[{name, values}] | 數列；長條/折線可多列（grouped）、圓餅取第一列 |
| `unit` | str（選用） | 數值單位，標於軸/圖說 |
| `source` | str（選用） | 來源編號（如 `[3]`），對應「引用來源」段 |

### 2. `app/services/chart.py`（新增，純函式）

```python
def render_chart_svg(spec: dict) -> str:
    """把圖規格畫成 inline SVG 字串（固定 viewBox，如 0 0 640 380）。
    type 不支援 / x 或 series 缺漏 / 數值非數字 → 回 ""（呼叫端略過該圖）。"""
```

- 三型各自幾何：長條（等寬長條＋y 軸刻度）、折線（點連線＋y 軸刻度）、圓餅（弧線扇形＋圖例）。
- 文字（標題、軸標籤、圖例）用 `font-family` 指定 Noto CJK 同 body，CJK 由 WeasyPrint fontconfig 解析。
- 顏色採品牌金 `#AE7415` 與中性灰階；多數列時用一組可區分的調色盤。
- 純函式、無 I/O，便於單測（驗 SVG 含對應元素數）。

### 3. `app/services/pdf.py`

新增純函式 `inject_charts(markdown_text: str) -> str`：以 regex 找出 ```chart fenced 區塊，逐一 `json.loads` → `render_chart_svg`，將整塊替換為 block-level HTML：

```html
<figure class="chart">{svg}<figcaption>{title}（來源 {source}）</figcaption></figure>
```

- `<figcaption>` 顯示 `title`；`source` 有值時才附「（來源 [3]）」，缺則只顯示標題。
- 渲染回空字串（規格壞/數據缺）→ 整塊移除（不留殘碼），記 `logger.warning`。
- `render_report_pdf` 在 `markdown.markdown(...)` **之前**先呼叫 `inject_charts`；python-markdown 對 block-level 原生 HTML 直接放行，`<figure>/<svg>` 原樣進 HTML。
- `_PAGE_CSS` 加 `figure.chart { margin:14px 0; text-align:center; } figure.chart svg { max-width:100%; } figcaption { font-size:9pt; color:#888; margin-top:4px; }`。

### 4. Prompt（`REPORT_SYSTEM_PROMPT`，`report.py`）

於既有規則後新增一條（圖表規則），要點：

- 當參考片段或網路來源中有**明確、可比較**的數據（跨項目比較／隨時間趨勢／組成佔比）且作圖能提升直觀理解時，**適時**插入圖表。
- 以 ```chart fenced 區塊輸出 JSON 規格（`type`/`title`/`x`/`series`/`unit`/`source`），`type` 限 `bar｜line｜pie`。
- **數據必須來自參考片段或網路來源、逐一可對應，不得杜撰或臆測**；每圖標 `source` 來源編號；無可靠數據則不作圖。
- 圖置於相關分析段落附近，非全部堆到末尾。

### 5. 前端 live 預覽（`web/static/app/ask.js`）

串流即時預覽中，```chart 區塊的原始 JSON 會以程式碼顯示（醜且洩漏規格）。在既有 markdown 正規化處，將 ```chart...``` 區塊以**佔位**取代顯示：`（圖表：{title}）`（純文字、不用 emoji，符使用者標示偏好）。最終 PDF 才呈現真圖。純顯示層，不改串流資料。

### 6. 持久化與重建

圖規格以 ```chart 區塊**留在 `report_doc.markdown`** 內，隨研報一起持久化；PDF 由 markdown 經 `inject_charts` **確定性重建**——無需另存圖檔、無 schema 變更。

## 錯誤處理

- 規格 JSON 解析失敗、`type` 不支援、`x`/`series` 缺漏或數值非數字 → `render_chart_svg` 回 `""`、`inject_charts` 移除該塊並 `logger.warning`，研報其餘照常。
- LLM 未輸出任何圖 → 研報與現況相同（純文字），無副作用。

## 延遲與部署

- SVG 自繪為輕量字串組裝，對生成延遲影響可忽略（渲染在 PDF 階段、非 LLM 串流）。
- **零新依賴**；CJK 沿用既有 fontconfig 的 Noto CJK（部署需求不變）。

## 測試

- `tests/test_chart.py`（新）：`render_chart_svg`
  - bar：3 類別單序列 → SVG 含 3 個 `<rect>`（長條）與標題文字。
  - line：時間序列 → 含 `<polyline>`／多 `<line>` 與資料點。
  - pie：3 扇形 → 含 3 個 `<path>`（弧）與圖例。
  - 多序列 bar/line：序列數 × 類別數對應元素。
  - 空/壞輸入（缺 `series`、`values` 非數字、`type` 不支援）→ 回 `""`。
- `tests/test_pdf.py`（新或既有）：
  - `inject_charts`：含一個 ```chart 區塊的 markdown → 輸出含 `<svg>` 與 `<figcaption>`；壞規格 → 該塊被移除、不含殘碼。
  - `render_report_pdf` 帶 ```chart 區塊 → 回 `b"%PDF"` 開頭（冒煙）。
- `tests/test_report.py`：`REPORT_SYSTEM_PROMPT` 含圖表指令字樣（如「```chart」與「不得杜撰」）。
- 前端：`ask.js` `node --check`；既有 `.test.mjs` 若涵蓋 markdown 正規化則加一例（```chart → 佔位）。

## 範圍與非目標

- **非目標**：互動式／前端 JS 圖表（PDF 不支援）；matplotlib 或其他繪圖庫；串接真實金融結構化數據（股價/財報序列）作圖（另案、範圍大）；圖表編輯 UI。
- **不動**：Q&A 路徑、檢索／瀏覽／總覽、schema、端點、`report_gate`、PDF 既有版面（僅加 figure/chart 樣式）。
