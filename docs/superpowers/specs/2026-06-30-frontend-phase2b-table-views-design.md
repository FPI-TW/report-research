# 前端 Phase 2b — 檢索頁表格檢視/檢視切換/高亮 React 遷移設計

> 接續 Phase 2a（PR #42）。本增量把檢索頁補到與**現行 live `/`** 平價，但**不 cutover**（退役舊頁留 Phase 2c）。分支 `feat/frontend-phase2b-table-views` 從 2a tip（`040eb54`）堆疊開出。

## 1. 背景與決策

Phase 2a 已交付 `/app/search`：搜尋 + 月份分組預設 + drill-in 機制 + 載入更多 + URL 可分享 + 篩選側欄 + 可點開最小詳情 modal，但**只暴露月份分組單一檢視**。2a 已建妥但休眠的 `MarketIndex`/`DrillView`（group=market→index/drill）尚未接通；`terms.ts` 的 `buildTerms` 僅空白分詞（CJK bigram 待 2b）。

**現行 live 真實檢視範圍校正**（讀 `web/static/app/state.js`）：`VIEWS = ["group","table"]`、`GROUPS = ["month","market"]`。卡片(grid)/扁平列表(list)檢視與 report_type 分組**早已自切換器移除**（render.js 仍留函式但不掛 UI）。故 REFACTOR_TODO「卡片/列表/表格/分組 4 檢視」已過時。

**使用者拍板決策（2026-06-30）**：
1. **cutover 另立 2c**：2b 只做功能到平價，仍是平行 `/app/search`、舊 `/` 不動；cutover（把 `/` 導向 `/app/search`，如 monitor 當初）待 live 驗證平價後另立增量。
2. **檢視範圍對齊現行 live**：只做 `group` + `table` 兩檢視、`month`/`market` 兩分組；**不復活**已移除的卡片/列表檢視與 report_type 分組（YAGNI）。

**核心架構決策**：
3. **檢視/分組/表格排序為前端呈現狀態，對已載入 `rows` 操作、不重打 API**（對齊 vanilla `paintResults` 切檢視免 fetch）。故 `view`/`group` **不得進 `useSearchResults` 的 query key**（否則切檢視會重抓）；與資料維度 `Filters` 分層。
4. **關鍵字高亮 XSS 安全**：vanilla `highlight` 用 `esc()`+`<mark>` 字串注入；React 端**禁 `dangerouslySetInnerHTML`**，改純函式把文字切成 segment 陣列、由元件渲染 `<mark>{seg}</mark>` 純節點。

## 2. 目標與非目標

### 目標（2b）
- **表格檢視 TableView**：欄位 報告名稱/市場/類型/日期/來源/標的（search 多 相關度/命中）；**用戶端可排序欄位**（點表頭循環 asc/desc，aria-sort + 鍵盤），僅作用於已載入資料。
- **檢視切換器**（列表 group ⇄ 表格 table）：roving-tabindex radiogroup、`localStorage rm_view` 持久化、URL `view`。
- **分組切換器**（日期 month ⇄ 市場 market）：可及性下拉、URL `group`、僅 group 檢視時顯示；接通 2a 的 MarketIndex（全部→index）/DrillView（某市場→drill）。
- **關鍵字高亮**：`buildTerms` 升級完整 CJK bigram；search 模式片段內關鍵詞 `<mark>` 黃底高亮（XSS 安全）；片段「顯示其他 N 段」展開（對齊 vanilla）。
- **2a 審查延後平價清單**（5 項）：MarketIndex 改吃 `/api/stats` 全語料 count、DrillView 標頭總篇數、LoadMore「還有 N 篇」、清單 `React.memo(ResultCard)` + memo `rows`、未分類群 sentinel 對齊。

### 非目標（明確排除）
- **cutover**（退役舊 `/`）→ Phase 2c。
- 卡片(grid)/扁平列表(list) 檢視、report_type 分組（現行 live 已移除）。
- 後端 `/api/*`、`db/**`、`app/**` 任何變更；無 schema 變更。
- 引入 Zustand/Redux（棧鎖定 Phase 0）。

## 3. 端點契約（消費，不改）

與 2a 相同：`GET /api/stats`（含 `markets[].count` 全語料計數，供 MarketIndex）、`GET /api/reports`、`GET /api/search`、`GET /api/report/{id}/full`、`GET /api/report/{id}/file`。2b 不新增任何端點。

## 4. 架構（沿用 2a 方案 A）

### 4.1 呈現狀態分層（關鍵）
- **資料維度** `Filters`（q/market/instrument/stock/futures/type/sort）→ 餵 `useSearchResults`，決定抓什麼。**不變**。
- **呈現狀態** `ViewState`（`view: 'group'|'table'`、`group: 'month'|'market'`）→ **不進 query key**，只決定怎麼畫已載入的 `rows`。
- **表格排序** `tableSort: { key: string|null; dir: 'asc'|'desc' }` → TableView 內部 local state，`rows` 變更時重置（對齊 vanilla `render()` 每次重置 tableSort）；**不入 URL**。

### 4.2 URL / 持久化（對齊 url.js）
- URL 同時承載 `Filters` 與 `ViewState`：`view`（≠'group' 才寫）、`group`（view='group' 且 ≠'month' 才寫）。
- `view` 另存 `localStorage rm_view`；載入優先序 **URL > localStorage > 預設 'group'**。`group` 無 localStorage，預設 'month'。
- `Filters` 與 `ViewState` 寫同一份 searchParams，setter 必須合併另一半再序列化（不互相清掉）。

### 4.3 純函式 lib（皆附 Vitest）
- `lib/filters.ts`（擴充）：新增 `VIEWS=['group','table']`、`GROUPS=['month','market']`、`ViewState` 型別、`DEFAULT_VIEW_STATE`；新增 `viewStateToParams(vs)`（回 entries 供合併）與 `paramsToViewState(sp)`（白名單、預設）。`Filters` 型別與既有 filters 函式**不動**。
- `lib/terms.ts`（升級）：`buildTerms(q)` 改為**完整 CJK bigram**——逐 token：含 CJK 字則 length===1 加單字、否則加所有相鄰 2-gram；非 CJK 且 length≥2 加整詞；去重後**依長度由長到短排序**（對齊 render.js:13-23，供高亮最長匹配優先）。
- `lib/highlight.ts`（新）：`highlightSegments(text, terms): { text: string; mark: boolean }[]`——以跳脫後的 terms 組 `gi` regex 切 `text`，回 segment 陣列（mark 標示是否命中）；terms 空回單一 `{text, mark:false}`。純函式、無 DOM、無 HTML 字串。
- `lib/tableSort.ts`（新）：`sortedRows(rows, tableSort): Row[]`——key 為 null 回原序；依 key 取值（name 小寫/market 用 mLabel/type 用 tLabel/date/source/score=best_score/match=match_count），數值相減、字串 `localeCompare(...,'zh-Hant')`，乘 dir（對齊 render.js sortedRows）。`nextTableSort(cur, key)`——同 key 翻 dir、異 key 設 {key, dir:'asc'}。
- `lib/grouping.ts`（沿用）：`groupViewMode`/`groupKey`/`groupRows` 不變。

### 4.4 元件（新增 / 擴充，皆附測試）
- **新 `TableView.tsx`**：`<table class="rtable">` + 可排序表頭（th `role=button` tabIndex 0、Enter/Space、aria-sort）+ 列 `data-report-id` 整列可點 `onOpen` + 鍵盤。內部 `tableSort` state、`useEffect` 於 `rows` 參照變更時重置。標的欄用 `targetsSummary` 純函式（移入 `meta.ts` 或 TableView）。
- **新 `ViewSwitch.tsx`**：兩鈕（列表/表格）radiogroup，`aria-checked` + roving tabIndex；`value: 'group'|'table'`、`onChange`。
- **新 `GroupBySelect.tsx`**：可及性下拉（月/市場）。**採 Mantine `Select`**（無障礙、鍵盤、focus 管理現成；控制項非逐像素關鍵區，樣式調金色品牌即可），免重寫 dropdown.js 自訂 listbox。`value: 'month'|'market'`、`onChange`；僅 `view==='group'` 顯示。
- **`ResultsView.tsx`（擴充）**：頂層 `view: 'group'|'table'` + `group`；`view==='table'` → `TableView`；否則 `groupViewMode(group, market)` → grouped/index/drill（現狀）。傳 `terms` 下去供高亮。
- **`ResultCard.tsx`（擴充）**：search 片段用 `highlightSegments` 渲染 `<mark>`；補「顯示其他 N 段片段」展開（首段顯示、其餘收合，對齊 vanilla `row-more`）。**`React.memo` 包裝**（平價清單效能）。
- **`MarketIndex.tsx`（修）**：改吃 `stats.markets`（全語料 count，依篇數多→少），非已載入 `rows` 聚合。新增 `stats`/`markets` prop。
- **`DrillView.tsx`（修）**：標頭補總篇數（傳入 `total`，對齊 vanilla `state.total`）。
- **`LoadMore.tsx`（修）**：顯示「載入更多（還有 N 篇）」，新增 `remaining` prop。
- **`SearchPage.tsx`（組合）**：以擴充後的 hook 取 `filters` + `viewState`；`const view = viewState.view==='table' ? 'table' : groupViewMode(viewState.group, filters.market)`；`terms = buildTerms(filters.q)`；版面加 ViewSwitch + （group 時）GroupBySelect；`rows` 以 `useMemo` 穩定後傳下（memo 效能）。

### 4.5 hooks
- `hooks/useSearchParamsState.ts`（擴充為頁面狀態）：回 `{ filters, viewState, setFilters, setViewState }`；內部讀寫 `searchParams`（filters + view + group 合併序列化）+ `localStorage rm_view`。`useSearchResults(filters)` **簽名不變**（只吃 Filters）。

### 4.6 樣式
- 表格 `.rtable`、檢視切換 `.view-switch`、分組下拉、片段 `<mark>`/`.passage`/`row-more` 的 bespoke CSS 從 `web/static/index.html` inline `<style>` 萃取到 `SearchPage.module.css`（或新 `results.module.css`），對齊 live。

## 5. 資料流

1. URL/localStorage → `useSearchParamsState` → `{filters, viewState}`。
2. `filters` → `useSearchResults`（**view/group 變更不在此**）→ `rows`/`total`/`mode`/分頁。
3. `viewState.view==='table'` → `TableView(sortedRows(rows, tableSort))`；否則 `groupViewMode(group, market)` → GroupedList / MarketIndex(stats) / DrillView(total)。
4. 切檢視/分組/表頭排序 → 只改呈現狀態、**重畫不重抓**。
5. `terms = buildTerms(filters.q)` → ResultCard 片段 `highlightSegments` 高亮。

## 6. 錯誤處理與安全

- **XSS**：高亮、片段、表格儲存格、標的、檔名全以純文字節點渲染；`highlightSegments` 只產資料、`<mark>` 由 JSX 出；**全程無 `dangerouslySetInnerHTML`**。沿用 2a Zod `.nullish()`。
- 表格排序 `localeCompare` 對 null 欄位以 `''`/`0` 取值，不丟例外。
- 切檢視/分組不打 API，無新增載入錯誤面；既有 isLoading/isError/empty 分支沿用。

## 7. 平價標準（2b「做完」的客觀定義）

1. **表格檢視**：欄位齊全；點任一可排序表頭循環 asc↔desc（aria-sort 正確、鍵盤可操作）；僅排序已載入資料；列可點開 modal。
2. **檢視切換**：列表⇄表格即時切換、不重打 API；`rm_view` 持久化；URL `view` 可分享/還原；切換時 roving tabIndex/aria-checked 正確。
3. **分組切換**：月⇄市場；market 時「全部」→市場索引（**全語料 count**）、點市場→drill（**標頭顯總篇數**）、返回回索引；URL `group` 可分享。
4. **高亮**：search 片段關鍵詞黃底 `<mark>`；CJK 詞以 bigram 命中；「顯示其他 N 段」可展開；XSS 注入字串不洩漏為 HTML。
5. **平價清單**：LoadMore 顯「還有 N 篇」；清單 `React.memo` 不必要重渲染消除。
6. **不 cutover**：舊 `/` 完全不動；`/app/search` 仍為平行頁。

## 8. 測試策略

- **純函式**：`buildTerms`（CJK 單字/bigram/英數/混合/排序）、`highlightSegments`（多 term、最長優先、無 term、特殊字跳脫、CJK）、`sortedRows`/`nextTableSort`（各 key、null、dir 翻轉）、`paramsToViewState`/`viewStateToParams`（白名單/預設/往返）。
- **元件**：`TableView`（表頭排序循環 + aria-sort + 鍵盤 + 列可點）、`ViewSwitch`（aria-checked/鍵盤/onChange）、`GroupBySelect`（onChange/僅 group 顯示）、`ResultCard`（高亮 segment 渲染 + 片段展開 + XSS 純文字）、`MarketIndex`（吃 stats count）、`DrillView`（標頭篇數）。
- **整合**：`SearchPage` 切換 view/group 不重打 API（mock 計次）、URL view/group 還原、localStorage rm_view。
- **e2e**（`frontend/e2e/search.spec.mjs` 擴充或新增）：登入→切表格→點表頭排序→切分組市場→索引→drill→返回→搜尋見高亮→0 console error；對 `:8098`（現行後端）跑。
- 測試指令本分支沿用 `cd frontend && npx vitest run src`；最終 `npm run build` + `eslint .`。

## 9. 部署

無 schema、後端不動。`make spa-build` 後 `sudo systemctl restart report-mark-web.service`（並讓 :8097 對齊現行 main，解 [[web-502-recovery]] 舊後端 `/app/*` 導向裸 `/login` 問題）。cutover 不在本增量。

## 10. 風險與緩解

| 風險 | 緩解 |
|---|---|
| view/group 誤入 query key 致切檢視重抓 | 嚴格分層：useSearchResults 只吃 Filters；ViewState 獨立；測試 mock 計次驗不重抓 |
| 高亮 XSS 回歸 | 純函式只產 segment 資料、`<mark>` JSX 出、無 dangerouslySetInnerHTML；注入字串測試 |
| 視覺與舊頁漂移 | bespoke CSS 萃取 + Playwright 對 :8098 平價 |
| Mantine Select 與 live 自訂下拉視覺差 | 控制項非逐像素關鍵區；調品牌金樣式即可；可及性換得簡潔 |
| 表格排序與 URL 衝突 | tableSort 為 ephemeral local，不入 URL、rows 變更重置（對齊 vanilla） |
| 堆疊於未合併 2a | 2b 分支 base=2a；PR base=2a 分支（GitHub 於 2a 併後自動改指 main）|

## 11. 2c 預告（非本 spec）

cutover：把 `/`（vanilla index）導向/取代為 `/app/search`（如 monitor `server.py:489` redirect 手法），live 平價驗證後執行；需 :8097 後端對齊現行 main。屬對外決策，獨立增量。
