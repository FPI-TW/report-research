# 檢索結果排序與分頁優化（相關度分層內最新優先 + 載入更多）

- 日期：2026-06-23
- 範圍：關鍵字檢索（`/api/search` 與檢索頁搜尋模式），不動瀏覽模式與問答模式。

## 背景與動機

目前關鍵字檢索有兩個限制，與使用者期待不符：

1. **結果有硬性上限**：前端寫死 `k=12`，後端 `k` 上限 30，搜尋無分頁。使用者看不到第 12 篇之後的相關報告。
2. **排序未結合新近度**：
   - `relevance` 排序＝chunk 的 `(tier, fused)`，**完全不看日期**。
   - `date_desc`/`date_asc` 只重排**已截斷的前 12 篇**，語意是「最相關 12 篇之中再依日期排」，而非全召回集合的日期排序。

目標：**不限制結果上限（改用載入更多分頁）**，且預設排序為**相關度分層內最新優先**——相關度仍主導，新近度只在「相近相關度」之間決勝。

## 現況架構（變更前）

- 前端 `web/static/app/api.js` `run()`：呼叫 `/api/search?q=...&k=12&passages=4&sort=...`，無分頁；`render()` 以 `state.rows.length >= 12` 判定「已達顯示上限」。
- 前端 `web/static/app/render.js`：`appendLoadMore()` 僅在 `state.mode === "browse"` 時顯示；load-more 點擊固定呼叫 `loadBrowse(true)`。
- 後端 `web/server.py` `/api/search`：`k: Query(10, ge=1, le=30)`；呼叫 `hybrid_search(k=k)`，分組成報告後 `results = list(grouped.values())[:k]`，date 排序只動這前 k 篇。`SearchResponse = {query, market, results}`。
- 檢索核心 `app/services/retrieval.py` `hybrid_search`：dense 召回 `scan = max(120, k*8)`；lexical 召回 `LEX_LIMIT=200`、`LEX_CAP=2000`；回傳依 `(tier, fused)` 由高到低排序的 chunk 清單。
- 儲存層 `app/services/store.py`：`search_chunks_meta`（dense kNN）、`search_chunks_lexical`（trgm LIKE，回 chunk）。

## 設計

### 1. 排序語意：報告層級「相關度分層內最新優先」

分組成報告後，對**全部召回報告**計算排序鍵（取代 `list(grouped.values())[:k]` 的原順序）：

```
排序鍵 = (tier DESC, band DESC, report_date DESC, fused DESC)
```

- **tier**（硬分層，不變）：`2`＝片段正規化後含完整查詢片語 > `1`＝含全部查詢詞（≥2 詞）> `0`＝其他（純語意）。
- **band**：報告最佳 `fused` 量化成粗帶，`band = floor(fused / 0.05)`。**band 寬度 = 0.05（已拍板）**：相差 5% 餘弦相似度內視為「相近相關度」，才比日期；避免「語意較弱但較新」的報告壓過「語意很強但較舊」的。
- **report_date DESC**：同 tier 同 band 內，新→舊（`None` 殿後）。
- **fused DESC**：最終 tiebreak（同 tier 同 band 同日）。

每篇報告的代表 `tier`/`fused` 取其最佳 chunk。沿用 `hybrid_search` 已依 `(tier, fused)` 排序的特性：分組時「首見即最佳」，故首見 chunk 的 `tier`/`fused` 即報告代表值。**變更點**：目前分組迴圈 `for _tier, score, row` 丟棄了 `tier`，需改為保留每篇報告的 `tier`，供排序鍵使用。

> 排序在後端 server 層做（分組之後）。`hybrid_search` 仍回 `(tier, fused, row)` 的 chunk 清單，職責不變。

#### 三種排序模式變更後語意

- **`relevance`（預設）**：上述 `(tier, band, date, fused)` 組合鍵——即「相關度分層內最新優先」。
- **`date_desc` / `date_asc`**：對**全召回報告集合**純依 `report_date` 排序（`None` 殿後），不再只排前 12。分頁照走。
- 前端排序 chips 文字不變（相關度／日期新→舊／日期舊→新）。

### 2. 召回擴大（支撐「不限上限」）

分頁要能往下翻，召回不可再綁 `k`：

- **Dense**（`retrieval.py` + `store.search_chunks_meta`）：`scan` 從 `max(120, k*8)` 改為固定寬裕常數 `DENSE_SCAN = 600`，與頁大小/頁數脫鉤。`hnsw.ef_search` 已 `max(120, scan)`，自動跟上。
- **Lexical**（`retrieval.py` + `store.search_chunks_lexical`）：改為每篇報告取最佳 chunk，讓「含查詢詞的報告幾乎全數入列」：
  - SQL 在 materialized CTE 後加一層 `DISTINCT ON (report_id) ... ORDER BY report_id, distance`，再對結果 `ORDER BY distance LIMIT :limit`。
  - 常數調整：`LEX_LIMIT 200 → 1000`（此時計「報告數」而非 chunk 數），`LEX_CAP 2000 → 8000`（materialize 安全上限）。
  - 效果：精確命中（tier 1/2）對本語料規模等同不設上限。

> 召回是 server 端逐頁重算，無伺服器狀態。embedding 由 `embed_query_cached` 快取，召回數百～千列在本機 LAN 工具足夠快，與瀏覽模式 offset 分頁同模式。

### 3. API 分頁：`/api/search`

- **參數**：移除 `k`，改用對齊 `/api/reports` 的分頁參數：
  - `limit: int = Query(50, ge=1, le=100)`（頁大小）
  - `offset: int = Query(0, ge=0)`
  - `passages: int = Query(3, ge=1, le=6)`（不變）
- **流程**：召回 → 分組成**全部**報告 → 算排序鍵排序 → `total = len(reports)` → 切 `[offset : offset+limit]` → 對切片內每篇填 `rank`。
- **回應**：`SearchResponse` 新增 `total: int`（本次查詢召回並符合過濾的相關報告數）。`results` 為當頁切片。
- 召回上限固定（`DENSE_SCAN` + lexical），故 `total` 語意為「本次召回到的相關報告數」，非全語料數——對語意檢索是誠實語意。

### 4. 前端：`web/static/app/api.js` 與 `render.js`

- **`run(append = false)`**（`api.js`）：
  - `append=false`：`state.offset = 0`，`skeleton()`，重建排序 chips。
  - URL 改帶 `limit=50&offset=${append ? state.offset : 0}&passages=4`（移除 `k=12`）。
  - 成功後（仍是最新請求且查詢未清空）：`state.mode="search"`；`state.rows = append ? state.rows.concat(items) : items`；`state.total = data.total`；`state.offset = state.rows.length`；append 時 `paintResults(false)`，否則走 `render(data)` 既有流程。
  - 沿用 `state.searchReq` 競態序號；append 早退/失敗時 `restoreLoadMore()`，不可清空整頁。
- **`render(data)`**（`render.js`）：移除 `capped = state.rows.length >= 12` 與「（已達顯示上限…）」文案，改為「找到 `data.total` 篇研報」（首屏顯示 total，非當頁列數）。
- **`appendLoadMore()`**（`render.js`）：解除 `state.mode !== "browse"` 限制，改為 `if (state.offset >= state.total) return;`，search 與 browse 皆顯示「載入更多（還有 N 篇）」。
- **load-more 點擊**（`render.js` `bindResultEvents()`）：依 `state.mode` 分派——browse→`loadBrowse(true)`、search→`run(true)`（目前固定 `loadBrowse(true)`）。
- `restoreLoadMore()` 既有實作以 `state.total`/`state.offset` 計算文案，search 模式自動適用，無需改。

### 5. 不變項（YAGNI）

- 瀏覽模式（`/api/reports`、`loadBrowse`）完全不動。
- 問答模式不動。
- 表格／分組檢視、片段展開、URL/localStorage 狀態、競態序號機制不動。
- `passages` 行為不變（每篇最多 4 個命中片段）。

## 資料流（變更後，搜尋一次）

1. 前端 `run()` → `GET /api/search?q=&limit=50&offset=0&passages=4&sort=relevance&<filters>`。
2. 後端 `embed_query_cached(q)` → `hybrid_search`（dense scan=600 + lexical 每報告最佳 chunk）→ 回 `(tier, fused, row)` 清單。
3. 後端分組成報告（保留 tier）→ 算 `(tier, band, date, fused)` 排序鍵排序 → `total` → 切片 `[offset:offset+limit]` → 填 `rank`。
4. 回 `{query, market, total, results}`。
5. 前端渲染當頁；`offset < total` 時顯示「載入更多」→ `run(true)` 帶新 offset 重打、append。

## 測試計畫

- **後端單元測試**（`tests/`）：
  - 排序鍵：同 tier 不同 band → band 高者在前；同 tier 同 band → 日期新者在前；跨 tier → 高 tier 永遠在前（即使較舊）。
  - 分頁：`offset`/`limit` 切片正確；`total` ＝召回報告數；末頁 `offset+limit >= total`。
  - 純日期模式：`date_desc`/`date_asc` 對全召回集合排序（非只前 12）。
  - lexical `DISTINCT ON (report_id)`：含詞報告每篇只回一列、取最近距離 chunk。
- **前端**：沿用零工具鏈 `*.test.mjs` 慣例，測 `run(true)` append 與 `appendLoadMore` 在 search 模式的條件（如純函式可抽則抽；否則以 Playwright 驗實際載入更多）。
- **手動／Playwright**：登入 → 搜尋熱門詞 → 確認「找到 N 篇」、捲動載入更多、切換日期排序後分頁仍正確。

## 風險與緩解

- **逐頁重算召回成本**：dense scan=600 + lexical 每查詢一次；embedding 已快取，本機足夠。若日後變慢，可改伺服器端快取排序結果（暫 YAGNI）。
- **`total` 語意誤解**：UI 文案用「找到 N 篇」而非「全部 N 篇」，避免暗示全語料。
- **band 邊界跳動**：band 為固定寬度量化，查詢內穩定；不同查詢的 fused 分布不同屬正常。
- **既有 `k` 參數呼叫端**：全文搜尋 `k` 僅 `/api/search` 使用，前端同步改；確認無其他呼叫端（problem/ask 不走此端點）。
