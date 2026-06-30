# 前端 Phase 2a — 檢索頁（browse/search）React 遷移設計

- 日期：2026-06-30
- 狀態：設計定稿，待實作計畫（writing-plans）
- 範圍：把檢索頁的「**搜尋 + 分組預設檢視（含市場索引→drill-in）+ 載入更多分頁 + URL 可分享狀態 + 篩選側欄**」遷進 React SPA（掛 `/app/search`），**達 live 平價但不 cutover**。
- 承接：`docs/superpowers/specs/2026-06-29-frontend-react-spa-foundation-design.md`（Phase 0/1，PR #40）、`docs/REFACTOR_TODO.md` Part B Phase 2。

---

## 1. 背景與決策

PR #40 完成 SPA 地基（Vite + React 19 + TS + Mantine + TanStack Query + Zod，掛 `/app`，沿用 `tf_session` 認證）與第一個切片 monitor。Phase 2 把流量最大的**檢索頁**遷入 React。

2026-06-30 brainstorming 定下：

1. **拆兩增量**：
   - **2a（本 spec）**：搜尋 + 分組預設檢視 + 分頁 + URL + 篩選側欄。
   - **2b（後續另立 spec）**：表格檢視 + 關鍵字高亮 + 檢視切換/group-by 下拉 UI + 視覺收尾 + **cutover**。
2. **2a 對齊 live 預設「分組檢視」**（非簡化卡片 MVP）。理由：現行 `index.html` 的 view-switch 只暴露 group（預設）/ table 兩種，**卡片(`gridCard`)程式碼存在但未接 UI**；live 使用者看到的預設即「分組」，故唯有對齊分組才談得上平價。
3. **方案 A「忠實元件化移植」**：版面外殼用 Mantine，**結果區沿用既有 bespoke CSS** 求逐像素平價；分組/排序/`groupViewMode` 等邏輯抽成 typed 純函式 + 測試（比照 monitor 的 `rate.ts`）。

---

## 2. 目標與非目標

### 目標（2a）
1. `/app/search` 掛載（react-router，沿用既有 `/app` basename 與認證白名單），與舊 `/` **共存**。
2. **篩選側欄**：market / instrument_type / 個股·期貨 toggle / report_type / sort chips，建成**可複用元件**（Phase 3 ask 會共用）。
3. **三端點消費（後端不動）**：`/api/stats`、`/api/reports`（browse）、`/api/search`（search），offset 分頁「載入更多」（page=50）。
4. **分組預設檢視**：依 `groupViewMode` 路由 — 月份/市場分組、市場索引→drill-in。
5. **URL 可分享狀態**：逐一對齊 `url.js` 參數結構；F5 還原；對 `/api/stats` 回傳的白名單驗證。
6. **測試**：Zod schema、純函式、元件、Playwright 與 live `/` 平價。

### 非目標（明確排除）
- **表格檢視、關鍵字高亮、檢視切換/group-by 下拉 UI、cutover** → Phase 2b。
- 任何後端**業務邏輯/schema/`/api/*` 行為**變動。可能**完全不需改 `web/server.py`**（`/app/*` 已由既有 catch-all shell 服務）。
- ask / report / help / login 的遷移。

---

## 3. 端點契約（消費，不改）

> 形狀取自唯讀盤點，與 `web/server.py` 對齊。

### `GET /api/stats`
- 參數：無。
- 回應：`{ total_reports, total_chunks, markets:[{market,count}], instrument_types:[{type,count}], report_types:[{type,count}], username }`。
- 用途：種子側欄 chips；URL 還原時驗證白名單。

### `GET /api/reports`（browse）
- 參數：`market`、`instrument_type`、`relates_stock`(bool)、`relates_futures`(bool)、`report_type`、`sort`(`date_desc`|`date_asc`，預設 `date_desc`)、`limit`(1–100，預設 50)、`offset`(≥0)。「全部」/None 視為不篩。
- 回應：`{ total, offset, items:[ReportListItem] }`。
- `ReportListItem`：`report_id, file_name, market, source, summary, report_date(ISO|null), report_type, instrument_types[]|null, relates_stock, relates_futures, stock_targets[]|null, futures_targets[]|null`。

### `GET /api/search`（search）
- 參數：`q`(必填)、上列同套篩選、`sort`(`relevance`|`date_desc`|`date_asc`，預設 `relevance`)、`limit`、`offset`、`passages`(1–6，**前端送 4**)。
- 回應：`{ query, market, total, results:[ReportResult] }`；`rank`/`total` 為全域跨頁。
- `ReportResult` = `ReportListItem` 全欄 + `rank`、`best_score`(0–1)、`match_count`、`passages:[{score, chunk_index, content}]`。

> **欄位不對稱**：browse 回 `items`（無 rank/score/passages），search 回 `results`（有）。前端正規化成統一 `Row` 型別 + `mode:'browse'|'search'` 區分。

---

## 4. 架構（方案 A）

### 4.1 路由與掛載
- 新增 `/app/search` route（`frontend/src/App.tsx`，沿用 `Layout`）。
- `/app/*` 既有後端 catch-all 已服務 SPA shell → **預期無後端改動**（部署免 restart，`make spa-build` 後即生效）。

### 4.2 狀態管理
- **伺服器狀態 → TanStack Query**：
  - `useInfiniteQuery`，`queryKey = ['reports'|'search', {q, market, instrument, stock, futures, type, sort}]`，`pageParam = offset`（step 50），`getNextPageParam` 依 `total` 判斷（`offset + loaded < total`）。
  - 對齊舊節奏：last-wins（Query 內建以 queryKey 取代舊手寫序號），`q`/篩選變更換 key 即重抓。
- **UI / URL 狀態 → react-router `useSearchParams`**（單一真實來源）：
  - 參數 schema 對齊 `url.js`：`q, market, instrument, stock=1, futures=1, type, sort`（2a 不含 `view`/`group` 的切換 UI，但預設 group/month 行為照舊）。
  - 進站 / F5：讀 searchParams → 先 `/api/stats` → 用白名單驗證每個篩選（不合法回「全部」）→ 才發 reports/search 請求（對齊舊 `restoreFromURL` 順序）。
  - **不引入 Zustand/Redux**（守 Phase 0 既定棧）。

### 4.3 純函式 lib（`frontend/src/features/search/lib/`，皆附 Vitest）
- `groupViewMode(group, market)` — 對齊舊 `state.test.mjs`：`group=market` 且 `market=全部` → `index`；`group=market` 且指定市場 → `drill`；`group=month` 或其他 → `grouped`。
- `groupRows` / `groupKey` / `groupLabel`（月份、市場分組）。
- `normalizeRow`（browse item / search result → 統一 `Row`）。
- `buildTerms`（查詢字串 → 詞，供 2b 高亮；2a 先建好不渲染）。

### 4.4 元件樹
`SearchPage`（route 元件）
- `SearchBar`（`#q` + 清除，debounce 450ms / Enter / 空字串回 browse）
- `FilterSidebar`（**可複用**）：`MarketChips` / `InstrumentChips` / `SubjectToggles` / `TypeChips` / `SortChips` + `activeFilterCount`/重置
- `ResultsMeta`（「q · market — 找到 N 篇」）
- `ResultsView`（2a 依 `groupViewMode` 切 `GroupedList` / `MarketIndex` / `DrillView`）
  - `ResultCard`（分組列項；browse 整列可點開報告 modal，search 顯示命中數/相似度）
  - `LoadMore`（`offset < total` 才顯示，點擊 `fetchNextPage`）
- `EmptyState` / `ErrorState`（inline 重試，對齊舊語意）

> 報告詳情 modal（舊 `openFull`）屬 report 功能邊界。2a 點擊結果 → 開一個**最小唯讀詳情 modal**（讀既有 `/api/report/{id}/full`）即達平價；完整 modal 系統遷移留 Phase 4，不在 2a 重寫。

### 4.5 樣式
- 結果區（卡片/分組/索引/drill）**bespoke CSS 從 `index.html` inline `<style>` 萃取對應規則** → `SearchPage.module.css`，逐像素對齊。
- 外殼/側欄用 Mantine + 既有 `tokens.css` 金色品牌（`--brand`）。

### 4.6 Zod schema（`frontend/src/features/search/schemas.ts`）
- `statsSchema` / `reportsSchema` / `searchSchema`（含 `passageSchema`）。
- 後端 Optional 欄位一律 `.nullish()`（記取 PR #40 M1：`.optional()` 拒 null 會炸頁）。

---

## 5. 資料流

1. 進站：`useSearchParams` 讀初值 → `useQuery(['stats'])` → 驗白名單 → 有 `q` 走 search、否則 browse。
2. 改篩選/排序/搜尋：更新 `searchParams`（`replace`）→ queryKey 變 → Query 重抓第一頁。
3. 載入更多：`fetchNextPage`（offset += 50）→ Query 累積頁 → `ResultsView` 攤平渲染。
4. 分組路由：`groupViewMode` 決定 grouped / index / drill 的渲染分支。
5. URL 與畫面始終由 `searchParams` 驅動 → 可分享、F5 一致。

---

## 6. 錯誤處理與安全

- **401** → 沿用 `lib/api` 的 `redirectToLogin()`（帶 `next` 回原 SPA 路徑）。
- **空/錯誤狀態** inline 呈現 + 重試鈕，對齊舊頁。
- **XSS**：React 預設轉義；**禁止** `dangerouslySetInnerHTML` 注入未轉義內容（取代舊 `html``/raw` 信任標記路徑）。passages/summary 以純文字節點渲染。
- 分頁競態：TanStack Query 以 queryKey + 取消機制處理 stale（取代舊手寫 `browseReq/searchReq` 序號）。

---

## 7. 平價標準（2a「做完」的客觀定義）

1. `/app/search` 與 live `/` **同三端點、同分組預設、同 drill-in 行為、同 50 筆分頁**。
2. URL 參數與 live **逐一對齊**、可分享、F5 還原一致（白名單驗證同語意）。
3. 篩選/排序行為與舊頁平價（含「全部」語意、search/browse sort 集合差異）。
4. 視覺貼近（bespoke CSS 對齊；金色品牌）。
5. **0 console error**（Playwright 與舊 `/` 對照）。
6. **不 cutover**：舊 `/` 完全不動；`/app/search` 為平行頁。

---

## 8. 測試策略

- **Vitest**：Zod schema 解析 / 純函式（`groupViewMode` 對齊 `state.test.mjs`、group/sort/normalize）/ 元件（搜尋送出、載入更多、URL 同步、drill 切換）。
- **Playwright**：`/app/search` 與 `/` 平價劇本（登入→渲染→分組→drill→載入更多→0 console error），跑非正式 `:8098` 不擾動正式 `:8097`。
- 執行：`cd frontend && npx vitest run src` + `npm run build`。
  （本分支由 main 開，暫無 Part A 的 `vitest include` 設定 → 測試以 `vitest run src` 範圍避開 Playwright `e2e/` 收集；PR #41 合併後 rebase 即恢復 `npm run test`。）

---

## 9. 部署
- 純前端 + 一條新 route。`make spa-build` 後即生效（`_NoCacheStatic`/dist 原子換版）。
- 若最終確認無 `web/server.py` 改動 → **免 restart**；若有則 `sudo systemctl restart report-mark-web.service`。

---

## 10. 風險與緩解

| 風險 | 緩解 |
|---|---|
| `render.js` 433 行分組/drill 邏輯複雜、易行為漂移 | 先抽純函式 + 對齊既有 `state.test.mjs`，元件保持薄殼 |
| 視覺與舊頁漂移 | 萃取 bespoke CSS + Playwright 對照平價 |
| browse/search 欄位不對稱 | `normalizeRow` 統一 `Row` 型別 + Zod 守門 |
| 與 Part A（PR #41）分支重疊（App.tsx / vitest.config） | 2a 由 main 開、改動點不同（route vs h1）、衝突極小；#41 合併後 rebase |

---

## 11. 2b 預告（非本 spec）
表格檢視、關鍵字高亮（`buildTerms`/`highlight`）、檢視切換 + group-by 下拉（`dropdown.js` 對應）、視覺收尾，達完整平價後做 `/` → `/app` cutover、退役對應 vanilla。
