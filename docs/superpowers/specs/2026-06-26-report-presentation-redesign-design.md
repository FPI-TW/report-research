# 深度研報輸出優化：去流程旁白＋版面較大改版 設計

> 日期：2026-06-26　分支：`feat/report-charts`（疊於圖表功能之上，沿用 `inject_charts`）

## 動機

兩個使用者回饋：

1. **開頭流程旁白滲入正文**。模型偶爾在 `# 標題` 前輸出過程獨白，例如：
   「好的，現在我來進行多面向的網路搜尋，補充材料行業的全面資料。已取得足夠的網路資料，現在整合所有參考片段與搜尋結果，撰寫完整深度研報。」
   這類文字不該出現在交付的 PDF 裡。

2. **整體版面要更專業、直觀**（使用者選「較大改版」）。現況是品牌橫條＋平鋪的 h1/h2/h3，缺少封面、目錄、章節層次與重點視覺化。

## 目標

- 渲染端**確定性**移除標題前的所有旁白（不靠模型自律），並以 system prompt 雙保險要求首字即 `# 標題`。
- 把研報 PDF 升級為：**封面頁 → 目錄頁 → 章節化內文**，含執行摘要重點框、關鍵發現編號卡片、章節層次、全頁頁尾。
- 零新依賴（純 WeasyPrint CSS ＋既有 SVG 圖表）、無 emoji、延續金色品牌 `#AE7415`。
- 沿用既有 `inject_charts`，圖表照常內嵌。

## 非目標（Out of Scope）

- 圓餅圖例維持正規化百分比（圓餅本質表佔比）；不改 `chart.py` 顯示原始數字。
- 不動問答（`/api/ask`）路徑與 live 串流預覽（`markdown.js`）的呈現。
- 不改報告的內容結構（仍為固定六段：執行摘要／關鍵發現／重點分析／風險與展望／引用來源／可選 外部參考（網路））。

## 架構與資料流

`generate_report` 已將 markdown 交給 `render_report_pdf(markdown_text, *, title, meta)`。本次只動 `app/services/pdf.py` 的 HTML 組裝與 CSS，外加 `app/services/report.py` 系統 prompt 一條規則。簽章不變、回傳仍為 PDF bytes。

```
markdown
  └─ split_report(markdown) → (title, [(section_name, body_md), …])   # 丟標題前旁白、依 ## 切章
       ├─ 退化（無標題 或 章節數 < 3）→ _render_simple()             # 回退現有簡版（品牌橫條＋平鋪）
       └─ 正常 → _render_fancy(title, sections, meta)
            ├─ 封面 section.cover
            ├─ 目錄 nav.toc（target-counter 帶頁碼、decimal-leading-zero 編號）
            └─ 每章 section.s-<slug>（內文 body 經 inject_charts → markdown）
  └─ WeasyPrint → PDF bytes
```

### `split_report(markdown_text) -> tuple[str, list[tuple[str, str]]]`

純函式。

- 找第一個 `^#\s+(.+)$`（多行模式）作標題；**其前所有文字（含旁白）丟棄**。
- 找不到 `# ` 標題 → 回 `("", [])`（交由呼叫端走簡版）。
- 標題後依 `^##\s+` 切段；每段第一行為章節名、其餘為章節 body markdown（strip）。
- `# 標題` 與第一個 `## ` 間的文字（少見的章節前言）併入結果丟棄列，不渲染（與「丟標題前旁白」一致，避免遊離段落破壞版型）。

### 版型判定（退化回退）

`_render_fancy` 僅在 `title` 非空 **且** `len(sections) >= 3` 時採用；否則 `render_report_pdf` 回退 `_render_simple`（沿用目前的品牌橫條＋整段 markdown 平鋪，確保「找不到相關資料」這類一兩句的輸出不會被套上封面＋目錄空殼）。簡版仍套用 `inject_charts`。

### 章節 slug 對應與樣式

| 章節名 | slug | 樣式重點 |
|---|---|---|
| 執行摘要 | `exec` | 淡金底色框（左側金色粗邊），一眼抓重點 |
| 關鍵發現 | `findings` | `<ol><li>` 渲染為獨立卡片＋圓形金色編號徽章（CSS counter） |
| 重點分析 | `analysis` | 預設章節樣式（含圖表） |
| 風險與展望 | `outlook` | 預設章節樣式 |
| 引用來源 | `refs` | 小字、左側細金線條列 |
| 外部參考（網路） | `extrefs` | 同 refs，連結金色可點 |
| 其他/未知 | `sec` | 預設章節樣式 |

未知章節名落 `sec`，永遠安全降級為一般段落。關鍵發現卡片依賴該段是有序清單（`1. …`）；若模型改用其他格式，無 `<li>` 即無卡片、降為純文字，不致破版。

### 封面與目錄

- **封面**（`section.cover`，`page-break-after: always`）：淡金漸層底、品牌名＋金色短線、置中 kicker「深度研究報告」＋大標題（取自 markdown 的 `# `，缺則用 `title` 參數）＋「生成日期 <meta.date>」＋頁尾署名。`@page:first { margin: 0 }` 讓封面滿版、且不出頁尾頁碼。
- **目錄**（`nav.toc`，`page-break-after: always`）：每章 `01/02…`（`decimal-leading-zero`）、點線（`leader('·')`）、自動頁碼（`target-counter(attr(href), page)`）。錨點 `#sec-<i>` 對到各章 `section[id]`。

### 全頁頁尾

`@page { @bottom-center { content: "<品牌名>　·　" counter(page) " / " counter(pages) }}`，封面以 `@page:first` 覆寫為無頁尾。

## 去旁白：system prompt 規則

`report.py` 的 `REPORT_SYSTEM_PROMPT` 既有第 2 條規定固定結構。新增明確要求：**輸出的第一個字元即為 `# （研報標題）`，前面不要任何前言、寒暄或流程說明（如「好的，我來…」「已取得資料，現在整合…」）。** 渲染端 `split_report` 為主要防線，本條為雙保險。

## 錯誤處理

- `split_report` 對空字串／無標題／無 `## ` 章節皆安全回退簡版。
- 章節 body 內 `inject_charts` 維持既有「壞規格→移除該塊＋warning」行為。
- HTML 全程 `html.escape` 章節名與標題（沿用現況）。

## 測試

`tests/test_pdf.py`（沿用 `sys.path.insert` 表頭、`uv run pytest`）新增：

- `split_report`：丟標題前旁白；無標題回 `("", [])`；正確切 N 段、章節名與 body 對應；標題前的「好的，現在我來…」範例被丟掉。
- 版型判定：≥3 章走 fancy（HTML 含 `class="cover"`、`class="toc"`、`section id="sec-`）；退化輸入走簡版（含 `brand-bar`、不含 `cover`）。
- 章節樣式掛載：執行摘要段 → `s-exec`；關鍵發現段 → `s-findings`；引用來源 → `s-refs`。
- 既有 `inject_charts`／圖表測試不回歸；`render_report_pdf` 仍回 `b"%PDF"` 開頭。
- 視覺驗證（手動，非 CI）：以 `uv run --with pymupdf python` 出 PDF→PNG 逐頁確認封面/目錄/卡片/圖表＋CJK。

`tests/test_report.py`：斷言 `REPORT_SYSTEM_PROMPT` 含「第一個字元即 `# `」「不要前言」語意（字串子串檢查），不回歸既有 prompt 斷言。

## 部署

無 schema 變更。沿用既有 Noto Sans CJK 需求。合併後 `sudo systemctl restart report-mark-web.service`（既有部署慣例）。
