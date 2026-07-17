# M9a — Typst 渲染引擎 + 模板契約 + ib-classic 模板（設計規格）

日期：2026-07-17
里程碑：M9a（`docs/IMPLEMENTATION_PLAN.md`）
前置：M9a spike 已完成並拍板（`docs/typst_spike_m9a.md`，PR #73 merged）
分支：`feat/m9a-typst-render`（base `origin/main` @ `16493ae`）

## 1. 目標與範圍

把研報 PDF 的渲染層從 WeasyPrint 換成 Typst，並定義一套**模板契約**，讓後續 M9b
能在不動內容生成的前提下新增模板、換皮重出。

**核心原則：內容與版型解耦。** `markdown` 是唯一真相，```kpi/```chart 是結構化區塊。
本里程碑**不動任何內容生成邏輯**（`report.py` / `report_writer.py` 的大綱、逐節、
檢索、證據帳本一律不改），只替換 markdown → PDF 的那一段。

### 做

- `app/services/typst_render.py`：markdown → 型別化中介模型 → Typst → PDF bytes。
- 模板契約（§4）：固定區塊的資料模型與 Typst 函式簽章。
- `app/templates/ib-classic.typ`：首款模板（國際投行密集雙欄）。
- `REPORT_RENDERER` 雙軌 flag 與 WeasyPrint fail-open 回退。
- 研報 PDF 免責聲明（經模板契約，見 D1）。

### 不做（留 M9b 或更後）

- 模板 registry、`template_id` 貫穿、`report_rendition` 表、換皮重出端點、前端模板選擇器。
- 評等框區塊（見 D2）。
- cetz/lilaq 原生繪圖（首版重用 `chart.py` SVG，見 D5）。
- 內容生成邏輯的任何變更。

## 2. 現況事實（實測，非推測）

| 項目 | 事實 | 出處 |
|------|------|------|
| 繁中字型 | 生產主機已裝 Noto Serif/Sans CJK TC（Regular+Bold），無需再裝 | spike |
| CJK 斜體 | 無斜體字重，Typst 不合成 → 模板一律不用 italic，強調用粗體/色彩 | spike |
| converter | pandoc `gfm-tex_math_dollars`（經 `pypandoc-binary`），約 12ms/篇 | spike |
| 編譯 | typst-py `typst.compile()` 回 PDF bytes，0.20s（含字型載入）；WeasyPrint 為秒級 | spike |
| 安全 | 敵意 fixture 全數跳脫為字面文字（`#eval`/`#read`/`#import`/`#set`/`$`/`@`） | spike |
| 現有 KPI 契約 | `{source?, items: [{value, label, change?, dir?: up\|down, source?}]}`，上限 5 筆 | `pdf.py:252-299` |
| 現有 chart 契約 | `{type: bar\|line\|pie, x: [...], series: [...]}`，`chart.py:_valid()` 驗證 | `chart.py:25-33` |
| 研報 PDF 免責 | **目前完全沒有**（`pdf.py`/`report.py` 對「免責/投資建議」零命中） | grep |
| M7 骨架 | 固定五章：執行摘要／關鍵發現／重點分析／風險與展望／引用來源 | M7 spec |
| `/api/report` body | 只有 `question`/`conversation_id`/`qa_id`——**無 market/code** | `web/server.py:793` |
| `report_signal` 覆蓋 | **104/14,567 篇（0.71%）**、42 個標的、97 筆有評等 | 本機＝生產 DB 實查 |

## 3. 架構

```
markdown（真相）
   │
   ├─ 依 ```kpi/```chart 圍欄位置切段（沿用 pdf.py 的 _KPI_RE/_CHART_RE）
   │
   ├─ 散文段 ──→ pandoc(gfm-tex_math_dollars → typst) ──→ ProseBlock(typst_fragment)
   ├─ kpi 圍欄 ─→ 嚴格驗證 ──────────────────────────→ KpiBlock(spec)
   └─ chart 圍欄 → chart.py 既有 _valid() ─────────────→ ChartBlock(spec)
                                                             │
   metadata（title / date / question / sources）             │
                                                             ▼
                                        DocumentModel ──→ ib-classic.typ ──→ typst.compile() ──→ PDF bytes
```

**不使用占位符**：依圍欄位置切段、逐段送 pandoc，避免占位符被 pandoc 改寫的整類問題
（spike 建議）。

**LLM 原文永不直接拼接進 Typst 原始碼**——只有兩種東西能通過：pandoc 跳脫後的片段、
以及 JSON 驗證後的型別化資料。這是安全邊界，不是風格偏好。

## 4. 模板契約

每個模板接收同一組固定區塊，各自排版。契約以傳入的 data model + Typst 函式簽章固定。

| 區塊 | 來源 | 必有 |
|------|------|------|
| 標題 | metadata（`outline.title` 或 `suggested_title`） | 是 |
| 執行摘要 | markdown `## 執行摘要` | 是（M7 骨架節） |
| 關鍵發現 | markdown `## 關鍵發現` | 是（M7 骨架節） |
| 重點分析 | markdown `## 重點分析`（含 `###` 動態子節） | 是（M7 骨架節） |
| 風險與展望 | markdown `## 風險與展望` | 是（M7 骨架節） |
| 引用來源 | markdown `## 引用來源` | 是（M7 骨架節） |
| 外部參考（網路） | markdown `## 外部參考（網路）` | 否（僅網搜有命中時） |
| **免責** | **模板固定文字（非 LLM 產出）** | **是** |
| ```kpi / ```chart | 圍欄 JSON | 否 |

**區塊與 M7 五章骨架完全對齊，無任何區塊缺資料源。**

模板**不得**依賴 LLM 產生免責——免責是模板的一部分、恆定存在，這樣 LLM 漏寫或
內容截斷都不影響它。

## 5. 決策

| # | 決策 | 理由 |
|---|------|------|
| **D1** | **免責兩軌共用**：Typst 走模板契約區塊，WeasyPrint 走 `render_report_pdf` 注入 | 使用者拍板（原訂只補 Typst，經 fail-open 缺口討論後改為兩軌）。理由：回退路徑存在正是為了應付沒預料到的情況，那恰恰是最不該少免責的時候。 |
| **D2** | **評等框移出 M9a 契約** | 使用者拍板。實測 `report_signal` 只覆蓋 0.71% 的報告、`/api/report` 亦無 market/code，接上去會是一個幾乎永遠不顯示的死區塊。待雷達「產生此標的研報」入口或 signal 覆蓋率上來再議。 |
| **D3** | **`REPORT_RENDERER` 預設 `typst`，WeasyPrint 為 fail-open 回退** | 使用者拍板。spike 實測 0.2s vs 秒級、安全邊角已驗證。 |
| **D4** | 依圍欄位置切段，不用占位符 | spike 建議：避免占位符被 pandoc 改寫。 |
| **D5** | chart 首版重用 `chart.py` 的 SVG → Typst `image()` | spike 實測 SVG 內繁中與 `figure` 中文編號皆正常；零 `@preview` 套件＝無網路編譯開箱即得。cetz 延後到有明確需求。 |
| **D6** | pandoc 必帶 `-f gfm-tex_math_dollars` | spike：GFM reader 預設把成對 `$...$` 當數學模式，財經文本的美元符號會誤配對。**這是實作硬性參數，不可省。** |
| **D7** | markdown 永遠是真相，PDF 可重建 | 既有 `report_doc` 契約，不變。 |

## 6. 安全

- **編譯前**：fence JSON 嚴格驗證（沿用 `chart.py:_valid()` 與 KPI 形狀檢查）；拒絕任意
  檔案路徑與未核准 URL。
- **編譯時**：typst-py 以 `root` 限制檔案存取；模板零 `@preview` 依賴 → 無網路。
- **編譯後**：失敗 fail-open 回退 WeasyPrint（見 §7 的免責缺口）。
- **形狀防禦（CLAUDE.md 紅線）**：`pdf.py` 的 `inject_kpi`/`inject_charts` 必須 isinstance-guard
  每個形狀，否則例外炸穿 `render_report_pdf` → 無 PDF、無持久化、重建永久 500。
  **`typst_render.py` 承擔同一條紅線**：每個 spec 欄位存取前先驗形狀，畸形 JSON 一律
  略過該區塊並 log，絕不讓例外逃出渲染函式。M7 已用 11 種畸形 JSON probe 驗過既有路徑，
  新路徑需比照。

## 7. 免責的兩軌一致性

免責在**兩條渲染路徑都必須存在**（D1），因為 Typst 編譯失敗會 fail-open 回退
WeasyPrint，而那正是最不該少免責的時候。

- Typst：模板契約的固定區塊（§4），恆定存在、不依賴 LLM 產出。
- WeasyPrint：`render_report_pdf` 注入，與模板文字同源（單一常數，避免兩軌漂移）。

**驗收必須同時覆蓋兩軌**（§8），否則回退路徑會在無人察覺下產出無免責 PDF——這正是
雷達改版時免責無聲消失的形態（見 PR #87 `cc054c3`）。

## 8. 驗收

- 同一份 `markdown` 在 `REPORT_RENDERER=typst` 與 `=weasyprint` 都能出 PDF。
- `ib-classic` 繁中正常（無豆腐字、無 italic）、KPI/圖表正確對應來源。
- **免責在兩軌都恆存在**（含 LLM 未產出免責文字的情況），且文字同源。
- **Typst 編譯失敗回退 WeasyPrint 後，PDF 仍有免責**（回退路徑不是免責的漏洞）。
- 畸形 kpi/chart JSON（比照 M7 的 11 種 probe）不炸穿渲染、該區塊安全略過。
- 敵意 markdown（`docs/typst_spike/fixture_hostile.md`）編譯後所有注入向量為字面文字。
- Typst 編譯失敗 → 回退 WeasyPrint 出 PDF，不 500。
- 既有 `test_report.py` / `test_pdf.py` 契約不破。

## 9. 部署

- `uv add typst pypandoc-binary` → **零手動 binary 安裝、零 systemd PATH drop-in**
  （與 `claude` CLI 不同，渲染鏈不需要 PATH）。
- 字型：生產主機已具備。
- `REPORT_RENDERER` 預設 `typst`（D3）；出事時設 `REPORT_RENDERER=weasyprint` 即可全域回退。
