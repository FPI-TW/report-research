# Phase 1：檢索頁 `/app/search` React 遷移 — 設計 spec

> Phase 0（地基＋App Shell＋登入＋後端 `/app` 服務）已完成（分支 `feat/react-spa-rebuild`）。本 spec 是總設計 `docs/superpowers/specs/2026-07-03-react-spa-rebuild-design.md` §6 的實作級細化，供 writing-plans 直接產出計畫。

**目標**：把 vanilla 檢索頁遷移為 React SPA 的 `/app/search`，逐像素對齊 `docs/design/廷豐智能研報.dc.html`，**後端契約與邏輯零改動**，**不 cutover**（與 vanilla 共存，Phase 4 才切換）。

**設計權威**：`.dc.html` 為唯一權威，總 spec §6 次之。本檔與兩者衝突時以 `.dc.html` 為準。

## 0. 已定案決策（brainstorming）

| 項目 | 決策 |
|------|------|
| 檢視模式 | **卡片 + 表格 兩種**（`ViewSwitch` 浮動 segmented 切換）；**不做「列表」檢視** |
| 分組 | **月分組恆在**（sticky pill 標頭為組織結構，非可切換）；**不做市場分組／索引／drill-in** |
| 無日期項的月分組 | 歸入 **「未標日期」** 組，置於所有月組**之後（最底）** |
| 資料抓取 | react-query 標準 `useQuery` + **手動 offset 累積**（非 `useInfiniteQuery`）；換排序即重抓、換檢視不重抓、載入更多累積 |
| 分頁大小 | **初始 50、每次 +50**（`SEARCH_PAGE_SIZE=50`，對齊 vanilla `BROWSE_PAGE/SEARCH_PAGE=50`；**取代總 spec §6.3 的「初始 8」**以維持平價） |
| 詳情 modal | **打 `/api/report/{id}/full` 取 `has_file`**，依 `has_file`／`isPdf` 四分支（PDF iframe／非 PDF 下載提示／缺檔 fallback／載入失敗）—— 平價保留 vanilla `modal.js` 行為（**修正總 spec §9.4「不打 API、固定 PDF」的縮水**） |
| 篩選 | 皆**單值**（對齊後端）：`market`／`instrument_type`／`report_type` 各單選 + `relates_stock`／`relates_futures` 布林；**無多值陣列、無標的值篩選** |
| 標的文字框 | `.dc.html` 的標的文字框以「個股／期貨」兩顆布林膠囊取代（後端無標的值篩選） |
| `最新` 徽章 | **前端計算**，鏡像 `app/services/answer.py:335-343`（嚴格大於才更新、同日保留第一筆、無日期不參與） |
| 後端 | 零改動；沿用 `/api/reports`、`/api/search`、`/api/report/{id}/full`、`/api/report/{id}/file`、`/api/stats` |
| 範圍外 | 問答（§7/Phase 2）、監控（§8/Phase 3）、cutover（Phase 4） |

## 1. 版面（照 `.dc.html`；細節見總 spec §6.1）

頂部固定工具列（白底、下邊框）：`SearchBar`（置中限寬 560、左 IconSearch 18、圓角 12、focus 金 ring、placeholder「搜尋主題、公司、事件…」）→ `MarketChipBar`（max-width 1120 置中；全部＋各市場膠囊，左 8px 語意色圓點＋全語料 count，active 深金底白字；右端 `margin-left:auto` 放 `SortMenu` 與 `更多篩選` 鈕）→ `MoreFiltersPopover`（420、右對齊）→ `ActiveChips`。

內容捲動區（max-width 1120）：`ResultsMeta` → 月分組（sticky pill：`f2f4f7` 底＋框，襯線 700 15px 標題＋金 count「N 篇」）→ `showCards`（兩欄卡片網格 gap 10，`isNarrow(<1024)` 一欄）／`showTable`（表格圓角 12＋框＋淡影容器）→ `EmptyState`（`total===0`）→ `LoadMore`。`ViewSwitch` 為 `position:fixed` 右下浮動 segmented（手機 bottom 80、桌機 24），卡片/表格圓形鈕。

## 2. 元件分解（`frontend/src/features/search/`，每檔一責、CSS Modules）

| 元件 | 責任 |
|------|------|
| `SearchPage.tsx` | 組裝 + 狀態容器（讀寫 URL、驅動 query、持有 view/tableSort 呈現態） |
| `SearchBar.tsx` | 受控輸入 + 送出（Enter／IME 守衛 `e.nativeEvent.isComposing`） |
| `MarketChipBar.tsx` | 市場膠囊列（全語料 count 來自 `/api/stats.markets`）+ 掛 `SortMenu` + `更多篩選`鈕（金 badge=進階條件數） |
| `MoreFiltersPopover.tsx` | 商品類型單選 / 報告類型單選 / 個股·期貨布林 / 清除·套用（用 Phase 0 `Popover`） |
| `SortMenu.tsx` | 排序選單（用 Phase 0 `Menu`）；選項依模式（見 §4） |
| `ActiveChips.tsx` | 作用中進階條件可刪金膠囊（`商品：X`／`類型：X`／`個股`／`期貨`） |
| `ResultsMeta.tsx` | 結果統計文案（§7） |
| `MonthGroup.tsx` | sticky 月標頭 + 該組內容插槽（卡片網格或表格列） |
| `ResultCard.tsx` | 單卡（search／browse 兩模式，見 §3） |
| `TableView.tsx` | 表格。欄（browse）：**報告名稱／市場／類型／日期／來源／標的**；**search 模式再加 相關度／命中**（＝vanilla `render.js` `tableHtml` 全欄）。表頭可排序＝呈現態 `tableSort`，可排序鍵 `name/market/type/date/source/score/match`（**`標的` 欄不排序**）；search 分數顯 `round(best_score*100)%`、命中顯 `match_count` |
| `EmptyState.tsx` | 空結果（襯線標題＋補救句＋CTA：條件式「清除篩選再試」＋search 模式「瀏覽全部報告」，見 §7） |
| `LoadMore.tsx` | 次按鈕「載入更多（還有 N 篇）」 |
| `ViewSwitch.tsx` | 浮動卡片/表格切換 |

共用（`frontend/src/components/`）：
- `primitives/Modal.tsx`（**本階段新增**：fixed 遮罩＋click-outside＋Esc＋`useFocusTrap`；沿用 Phase 0 hooks）。
- `ReportDetailModal.tsx`（檢索用）：開啟時打 **`GET /api/report/{id}/full`**（取 metadata＋`has_file`）→ 依 `has_file`／`isPdf`(由 `file_name`) 分**四分支**（PDF iframe／非 PDF 下載提示／缺檔 fallback／載入失敗，詳見 §8）；連結指向 `/api/report/{id}/file`，scheme 守門（僅相對 `/` 或 `https?:`）。

### 2.1 `lib/` 純函式（新寫、以舊碼為規格參考、全附 Vitest）
`filters.ts`（篩選↔URL/API 單值參數，「全部」/預設不帶參）、`grouping.ts`（月分組 `YYYY-MM`，無日期→「未標日期」置底）、`highlight.ts`+`terms.ts`（片段高亮，回 React 節點、XSS 安全）、`isLatest.ts`（最新徽章，鏡像 `answer.py`）、`tableSort.ts`（表格欄排序，鍵 `name/market/type/date/source/score/match`）、`sortForMode.ts`（模式合法排序＋非法回退預設）、`resultsMeta.ts`（文案）。

## 3. `ResultCard` 兩模式
共同：市場徽章（語意色底白字）＋`最新`徽章（綠描邊 框 `#34c759`／字 `#248a3d`，前端算）＋襯線標題 14.5/700＋商品類型/標的膠囊（`--tf-border-weak` 底）＋底列 `來源 · 日期` ｜ `查看全文 ›`（金字）。整卡可點 → `ReportDetailModal`；hover 金框。
- **searchMode**：高亮片段（`<mark>` 底 `#fff3bf`，見 §9）＋相關度條（金填充寬＝`best_score`）＋`相關度 N`。片段來源＝`results[].passages[].content`（純文字，前端高亮）。
- **browseMode**：摘要 `summary`（≤88 字截斷）。

## 4. 資料流與狀態（react-query）

- **browseMode（無 `q`）** → `GET /api/reports?market=&instrument_type=&report_type=&relates_stock=&relates_futures=&sort=&limit=&offset=`；回 `ReportListResponse{total, offset, items[]}`。
- **searchMode（有 `q`）** → `GET /api/search?q=&passages=3&market=&instrument_type=&report_type=&relates_stock=&relates_futures=&sort=&limit=&offset=`；回 `SearchResponse{query, market, total, results[]}`（`results[]` 每筆有 `best_score`／`match_count`／`passages[]`）。
- **query key** = `['search', mode, q, market, instrument_type, report_type, relates_stock, relates_futures, sort]`。
  - `view`（cards/table）、`tableSort`（表頭欄排序）為**呈現態**，**不進 query key、不重抓**。
  - `sort` **進 query key**（換排序重打 API）。
- **分頁（手動累積）**：state 持有 `pages`（已載入頁）與 `loaded`（已載入筆數）；`載入更多` 以 `offset=loaded` 抓下一頁並 append；`hasMore = loaded < total`；`remaining = total - loaded`。**初始 `limit=50`，每次 `+50`（`SEARCH_PAGE_SIZE=50`，對齊 vanilla `BROWSE_PAGE/SEARCH_PAGE`）**。換 query key（q/篩選/sort）→ 重置累積。
- **URL 同步**：`q`、`market`、`instrument_type`、`report_type`、`relates_stock`、`relates_futures`、`sort`、`view` 進 querystring（可分享）。「全部」不帶該市場參數；布林為 false 不帶；`sort`／`view` 為預設值不帶。
- **全語料 count**（市場膠囊）：來自 `GET /api/stats`（Phase 0 `useStats` 已有）`markets[]`；與結果集無關。

### 4.1 排序（`SortMenu`，功能平價）
- searchMode：`相關度`(relevance，預設) / `最新`(date_desc) / `最舊`(date_asc)。
- browseMode：`最新`(date_desc，預設) / `最舊`(date_asc)（**無相關度**）。
- 切模式（有無 q）時，若當前 sort 在新模式不合法（如 browse 下 relevance），回退該模式預設。

## 5. `最新` 徽章（前端計算，純函式 `isLatest.ts`）
鏡像 `app/services/answer.py:335-343`：在**目前已載入結果集**中取 `report_date` **嚴格最大**者標「最新」；**同日並列保留當前排序下第一筆，只標一篇**；無 `report_date` 者不參與。結果集變動（載入更多／換篩選／換排序）即重算。

## 6. 篩選 ↔ URL／API 參數（皆單值）
`market`／`instrument_type`／`report_type` 單選（「全部」→ 不帶參）；`relates_stock`／`relates_futures` 布林（true 才帶）。**不支援**多值陣列或標的值篩選（後端無此契約）。`MoreFiltersPopover` 的「進階條件數」＝`instrument_type`＋`report_type`＋`relates_stock`＋`relates_futures` 中作用中者數量（`market`、`q` 不計）。

**選項來源（單一資料源）**：市場膠囊、商品類型膠囊、報告類型膠囊的**可選項與全語料 count 皆取自 `GET /api/stats`**（Phase 0 `useStats`：`markets[]`／`instrument_types[]`／`report_types[]`，各為 `{type|market, count}`），與當前結果集無關（即「全語料計數」，非隨 q/篩選變動——沿用既有設計）。市場順序用 `meta.ts` `MARKET_ORDER`；商品類型色用 `ptypeColor`。

## 7. 文案
- **ResultsMeta**：有 q → `「{q}」· {市場} — 找到 {N} 篇研報`；無 q 有篩選 → `{市場|全部研報} — {N} 篇`；皆無 → `全部研報 — 共 {N} 篇`。
- **月標頭**：`{YYYY} 年 {M} 月`；無日期組 → `未標日期`。
- **EmptyState**：標題 `找不到「{q}」的相關研報`（有 q）／`沒有符合條件的研報`（無 q）；補救句 `換個說法或關鍵字試試，或清除目前的篩選條件重新檢索。`
  - **CTA（平價 vanilla `render.js:77-78`）**：`清除篩選再試`（**僅當有作用中進階篩選或 市場≠全部**時顯示 → 清篩選+重查）＋（**search 模式**）`瀏覽全部報告`（清空 `q`＋清篩選 → 切 browse）。browse 模式無 q，只顯條件式`清除篩選再試`。
- **LoadMore**：`載入更多（還有 {remaining} 篇）`。

## 8. `ReportDetailModal` 與 `Modal` 原語
- `Modal`（新原語）：`{opened, onClose, title?, children}`；fixed 遮罩、click-outside/Esc 關閉、`useFocusTrap`、`role="dialog"` `aria-modal`。
- `ReportDetailModal`：開啟時 **`GET /api/report/{id}/full`**（react-query，`enabled: opened`；回 `{file_name, market, source, summary, report_date, report_type, has_file}`）。`isPdf = file_name.toLowerCase().endsWith('.pdf')`。**四分支（鏡像 vanilla `modal.js`）**：
  1. **`has_file && isPdf`** → PDF iframe（`src=/api/report/{id}/file`）＋載入中覆蓋層（iframe `onload` 移除、`onerror` 提示改用新分頁）＋`在新分頁開啟`（`<a target="_blank" rel="noopener">`）。
  2. **`has_file && !isPdf`**（如 `.docx`）→ 「{副檔名大寫} 文件 無法內嵌預覽，請下載查看」＋`下載原始檔`（`<a>` 指向 `/file`）。
  3. **`!has_file`** → 「找不到原始檔。」（無連結）。
  4. **query error** → 「報告載入失敗，請稍後再試或重新整理頁面。」
  metadata 顯示：市場徽章＋來源＋日期＋類型＋摘要（皆取自 `/full`）。所有連結 scheme 僅允許相對 `/` 或 `https?:`。載入中先顯「載入中…」。

## 9. 命中高亮（`highlight.ts` + `terms.ts`，XSS 安全）
- `terms(q)`：切詞（沿用舊碼規則為規格參考）。
- `highlight(text, terms)`：回傳 React 節點陣列（文字段 + `<mark>` 段），**不使用 `dangerouslySetInnerHTML`**；純函式、可單元測試（斷言分段結果）。

## 10. 測試
- **純函式（Vitest，RED→GREEN）**：`filters`(參數↔URL 雙向、單值、預設不帶)、`grouping`(YYYY-MM 分組、無日期置底、組內序沿排序)、`highlight`/`terms`(分段、無 term 原樣、特殊字元不炸)、`isLatest`(嚴格大於、同日第一筆、無日期不參與、載入更多重算)、`tableSort`(各欄升降序，含 `score`/`match` 數值欄)、`sortForMode`(**browse 下 `relevance` 回退 `date_desc`**、合法值原樣、各模式預設)、`resultsMeta`(三種文案分支)。
- **元件（Testing Library）**：SearchBar 送出＋IME 守衛；MarketChipBar active＋count；MoreFiltersPopover 單選切換＋badge 計數＋清除；ActiveChips 可刪；ResultCard 兩模式（高亮/相關度條 vs 摘要）；**`TableView` 欄位（browse 6 欄／search 8 欄含 標的/相關度/命中）＋表頭排序（`標的` 不可排）**；ViewSwitch 切換**不重抓**（mock fetch 呼叫次數不增）；LoadMore 累積；EmptyState；`Modal` 開關/Esc/focus-trap；**`ReportDetailModal` 四分支（mock `/full`：`has_file+pdf`→iframe／`has_file+docx`→下載提示／`!has_file`→缺檔文案／`/full` query error→載入失敗 fallback）＋下載 scheme 守門**；EmptyState 兩 CTA（search：清除篩選再試[有篩選]＋瀏覽全部報告；browse：清除篩選再試）。
- **排序/狀態（整合）**：`relevance` 從 search 切回 browse（清空 q）自動回退 `date_desc`；URL 帶 `sort` 可還原到對應選項；改 `sort` **會** refetch（query key 變、fetch 次數增），改 `view`／`tableSort` **不** refetch。
- **e2e 冒煙（`:8098`，Playwright）**：登入 → `/app/search` browse 載入 → 輸入關鍵字搜尋 → 套用一個篩選 → 月分組顯示 → 切表格檢視（不重抓）→ 載入更多 → 開 `ReportDetailModal`（PDF 內嵌）。
- 執行慣例：前端 `./node_modules/.bin/vitest run <path>`（RTK 遮 exit code → 直接 binary；完整套件 WSL flake → 焦點檔＋build）；e2e 由控制端跑（工作樹 `:8098`、`ss` 核對埠免誤殺正式 `:8097`、預熱非必要因不觸發 LLM）。

## 11. 後端合約（消費，不改）
| 端點 | 用途 | 關鍵欄位 |
|------|------|---------|
| `GET /api/reports` | browse | `ReportListResponse{total, offset, items[ReportListItem]}`；item 有 `report_id/file_name/market/source/summary/report_date/report_type/instrument_types/relates_stock/relates_futures/stock_targets/futures_targets` |
| `GET /api/search` | search | `SearchResponse{query, market, total, results[ReportResult]}`；`ReportResult` 加 `best_score/match_count/passages[Passage{score,chunk_index,content}]` |
| `GET /api/report/{id}/full` | modal metadata + 檔案狀態 | `{file_name, market, source, summary, report_date, report_type, has_file}`（**modal 開啟時打**，`has_file` 決定分支） |
| `GET /api/report/{id}/file` | 原始檔 | **PDF → `inline`（iframe 內嵌）；非 PDF（.docx 等）→ `attachment`（下載）；缺檔 → 404**。路徑由 DB 依 id 取，無路徑注入 |
| `GET /api/stats` | 市場/類型 count + 選項 | `markets[{market,count}]`、`instrument_types[{type,count}]`、`report_types[{type,count}]`、`username`（Phase 0 `useStats`） |

參數：`market/instrument_type/report_type`（`"全部"` 或空＝不篩）、`relates_stock/relates_futures`（bool）、`sort`（reports: date_desc|date_asc，預設 date_desc；search: relevance|date_desc|date_asc，預設 relevance）、`limit`(1–100)、`offset`(≥0)、search 另有 `passages`(1–6，用 3)、`q`(1–SEARCH_QUERY_MAX_CHARS)。

## 12. 非目標 / 延後
- 列表檢視、市場分組/索引/drill-in（依約定移除）。
- 問答（Phase 2）、監控（Phase 3）、cutover（Phase 4）。
- 後端任何改動。

## 13. 交付驗收
- 前端 vitest 全綠 + tsc 0 + eslint 0 + build 0；e2e 冒煙綠。
- `/app/search` 與 vanilla `/` 共存；未 cutover；正式站 `:8097` 不受影響。
- 對照 vanilla 平價清單（除移除項）：搜尋、瀏覽、市場膠囊+count、進階篩選、排序、月分組、卡片/表格、載入更多、空狀態、URL 可分享、詳情 Modal、命中高亮、最新徽章。
