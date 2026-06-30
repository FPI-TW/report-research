# 前端 React 重構與遷移 — 後續開發 TODO

## 背景

PR #40（`feat/frontend-react-spa-foundation`）已交付 React SPA 的 **Phase 0/1**（SPA 地基 + monitor 垂直切片，掛 `/app`，後端僅加服務路由/認證 glue/cutover，無 schema 變更）。最終整支審查判定 MERGE AFTER FIXES，4 個必修（M1 Critical 等）已修畢並 live 驗證。

本文件把**後續前端重構/補強與逐頁遷移**整理成可執行 backlog，供後續開發逐項認領。範圍**聚焦前端重構 + vanilla→React 遷移**；後端 Roadmap 功能軌（每日簡報、結構化訊號、findb 整合、MCP/REST 對外）見 `docs/ROADMAP.md`，不在本文件。

> 來源：PR #40 最終整支審查的 Minor 發現 + 唯讀盤點（已交付 React 程式碼技術債 / vanilla 遷移盤點 / 文件延後項）去重彙整。完成一項請勾選並標注 PR。

---

## Part A — 已交付程式碼的後續補強（PR #40 的 Minor backlog）

均為技術債/規範落差、**無新功能**，每項多為單檔小修，可各自獨立小 PR。優先序建議：A1 測試 + A2(tsc) → A3 a11y → A4 正確性 → A5 決策（Phase 2 啟動前鎖定）。嚴重度：[中] / [小]。

### A1. 測試補強
- [x] **useTween 補 normal-motion 動畫測試** — `frontend/src/features/monitor/useTween.test.tsx` 只測 reduced-motion/null 快速路徑，600ms easing 的 RAF `step` 遞迴完全未測（假覆蓋）。補 `matchMedia=false` + fake timers 驗 `start→value` 收斂。[中]
- [x] **useMonitorRate 補測試檔** — `frontend/src/features/monitor/useMonitorRate.ts` 無對應測試。驗：sample=null 不觸發 effect、sample 參照變更算速率、NULL_RATE 初值→逐次更新。[中]
- [x] **MonitorPage.test 補非同步/輪詢/clock 驗證** — `frontend/src/features/monitor/MonitorPage.test.tsx` 僅驗 mock 後渲染；補 `waitFor`/`act` 驗 pending→success、輪詢呼叫、在地 clock 啟動。[小]
- [x] **e2e 監聽器提前註冊** — `frontend/e2e/monitor.spec.mjs` 的 `requestfinished` 監聽器註冊在 `toBeVisible()` 之後（邊界 flaky）；移到 `page.goto()` 之前。[小]
- [x] **test_spa_serving 解除對 dist 硬耦合** — `tests/test_spa_serving.py` 讀 gitignored `frontend/dist`，clean checkout 無 build 會 503 偽紅；dist 缺時 `pytest.skip`。[小]

### A2. Build / 設定
- [x] **spa-build 納入 tsc --noEmit** — `Makefile`（spa-build 目標）直呼 `npx vite build` 跳過型別檢查；改 `rtk npm --prefix frontend run build`（= `tsc --noEmit && vite build`）讓本機與部署門檻一致。[中]
- [x] **make spa-test 加 `--passWithNoTests`** — 無測試檔時 exit 1（已不發生，但讓目標可無條件呼叫）。[小]
- [x] **dist.prev 加入 .gitignore** — `frontend/.gitignore` 已列 `dist`/`dist.next`，漏 `dist.prev`（原子換版備份）。[小]
- [x] **engines.node 強制** — `frontend/package.json` engines 僅宣告未強制；加 `.npmrc engine-strict=true` 或 spa-build 前置 Node 版本檢查（底線 Node 22.22+）。[小]
- [x] **Vitest include 範圍（本批附帶修正）** — `frontend/vitest.config.ts` 無 `include`，預設 glob 收進 Playwright `e2e/*.spec.mjs` 致 suite 失敗；限定 `src/**`。[小]
- [ ] **spa-build 換版視窗** — `Makefile` 兩個 `mv` 間 `dist` 短暫不存在（微秒 503 視窗）；如要零視窗改 symlink 切換，否則於 spec 明確記錄為可接受取捨。[小]
- [ ] **版本鏈記錄** — `@eslint/js@10.0.1` vs `eslint@10.6.0` 為獨立版號（非 bug）；`typescript-eslint <6.1.0` 對 TS 有上限。於 CONTRIBUTING/README 標注版本鏈，升 TS 前查相容。[小/doc]
- [ ] **未用相依的保留/精簡決策** — Phase 0/1 預裝但未用：`react-hook-form`/`@hookform/resolvers`/`@mantine/form`/`@tanstack/react-query-devtools`（spec §3 已列、spec-sanctioned）。Phase 2/3 會用到，建議保留；若要精簡可延到對應 phase 再裝。[小/decision]

### A3. a11y / UX
- [x] **補全站 h1** — `frontend/src/App.tsx`（Home `:20`、NotFound `:29`）與 `MonitorPage.tsx:85` 全用 h2/h3、無 h1。於 Layout 加 `<h1 className="sr-only">廷豐智能研報</h1>` 或將 MonitorPage 改 `order={1}`，確保單一 h1 + 正常梯級。[小]
- [x] **Progress 條補 aria-label** — `frontend/src/features/monitor/MonitorPage.tsx:125/152`（標註/摘要進度）的 Mantine Progress 無 accessible name；加 `aria-label`。[小]
- [x] **LIVE 徽章初次 pending 態** — `MonitorPage.tsx:90` 為 `isError?'重連中':'LIVE'`，首次 loading 即顯 LIVE（暗示已有資料）；改 `isLoading && !data ? 連線中(灰) : ...`，對照舊 `monitor.html` 初始態。[小]

### A4. 正確性 / 健壯性
- [x] **footer 渲染 orchestrator pill** — spec §6.2 列 orchestrator pill，`frontend/src/lib/schemas.ts` 已解析 `orchestrator` 卻未在 footer 渲染狀態；補條件渲染（承 M1 的 null 守門）。[小]
- [x] **useTween `from.current` 過時基準** — `frontend/src/features/monitor/useTween.ts` 的 `from.current` 僅動畫完成時同步，中途換值/卸載會以過時基準起跳；目前 2s 輪詢/600ms 動畫不觸發，屬潛在脆弱。[小]
- [x] **_safe_next 收斂控制字元** — `web/server.py` `_safe_next` 目前僅濾 `\r`/`\n`，其餘 C0 控制字元靠下游 Starlette `quote()` 兜底；改 `any(ord(c) < 0x20 ...)` 讓白名單自身完備（縱深防禦）。[小]
- [x] **cutover 測試 per-request cookies 棄用** — `tests/test_spa_serving.py::test_legacy_monitor_redirects_to_spa` 用 per-request `cookies=`（deprecation warning）；改 `TestClient(app, cookies=_auth_cookies())` 實例式（與 Task 3 修法一致）。[小]

### A5. 遷移前需鎖定的架構決策
- [ ] **共用純函式統一策略** — `frontend/src/lib/eta.ts`、`features/monitor/rate.ts` 已移植自 `web/static/app/eta.js`。Phase 2+ 前明確：哪些純函式進 `frontend/lib/`、vanilla 版何時刪、是否 monorepo shared（目前直接 copy 可行）。
- [ ] **遷移頁的 UI 狀態管理** — 伺服器狀態用已選的 **TanStack Query**；UI 狀態（篩選/排序/檢視/URL 同步）用 React state/context + react-router `searchParams`。**不引入 Zustand/Redux**（避免偏離已定棧）。
- [ ] **SSE 整合手法（Phase 3 前鎖定）** — ask/report 串流務必沿用 `ask.js` 既有強健語意（result/assistant fallback、只對 API 529 重試、逾時已串文字則 fail-open）。TanStack Query 不原生支援 SSE → 採 `experimental_streamedQuery` 或手動 `setQueryData`，連線自管 + abort。
- [ ] **圖表庫選型（Phase 6 前鎖定）** — 候選 `@mantine/charts` / visx / ECharts；注意後端 `app/services/chart.py` 的 SVG 是給 PDF 用、與前端圖表庫無關。

---

## Part B — vanilla → React 逐頁遷移（Phase 2–6，各自 spec → plan → 實作）

每個 Phase 走既有流程：`superpowers:brainstorming → writing-plans → subagent-driven-development`，採**平價閘門 + 舊頁保留到達標**。

### Phase 2 — 檢索頁 browse / search　[風險 中｜建議先做建立信心]
- **遷移模組**：`state.js`(41) / `api.js`(135) / `search.js`(22) / `chips.js`(114) / `render.js`(433，分組邏輯最複雜) / `url.js`(52) / `meta.js`(31) / `dropdown.js`(70) / `dom.js`(26)
- **消費端點**（後端不動）：`/api/stats`、`/api/reports`、`/api/search`、`/api/markets`
- **重點**：卡片/列表/表格/分組 4 檢視 + 關鍵字高亮 + 分頁載入更多；URL 可分享連結；視覺平價對照舊頁
- **依賴**：無（可獨立於 Phase 3）
- **進度（2026-06-30）**：拆兩增量交付。**Phase 2a 已完成**（分支 feat/frontend-phase2a-browse-search，SDD 10 任務 + opus 整支審查 Ready-to-merge）：搜尋 + 月份分組預設 + drill-in 機制 + 載入更多分頁 + URL 可分享 + 可複用篩選側欄 + 結果卡片可點開最小研報詳情 modal，掛載 `/app/search`（**未 cutover**，舊 `/` 保留）；MarketIndex/DrillView 已建但 2a 不暴露。**Phase 2b 已完成**（分支 feat/frontend-phase2b-table-views）：表格檢視 + 關鍵字高亮（`<mark>`）+ 分組切換器（ViewSwitch + GroupBySelect，URL + localStorage 持久化）+ 市場索引/drill-in 串接全語料 count + DrillView 標頭總篇數 + LoadMore 剩餘篇數 + `src/test/setup.ts` 補 ResizeObserver + TableView eslint 修正（render-time setState）；172 vitest + build + eslint 全綠，e2e 語法驗證通過。**Phase 2c 待辦**：視覺平價確認後 cutover 退役舊頁。
- **Phase 2b 平價清單（已全部收齊）**：(1) `MarketIndex` 改吃 `/api/stats` 全語料各市場 count ✅；(2) `DrillView` 標頭補總篇數 ✅；(3) `LoadMore` 補「還有 N 篇」 ✅；(4) `React.memo(ResultCard)` 已在 2a 實作 ✅；(5) 未分類群 sentinel 以「未分類」呈現達語意平價 ✅。
- **更新（2026-06-30，2b 最終審查）**：opus 整支審查 **Ready-to-merge（0 Critical）**；173 vitest + build + eslint + live e2e（:8098）2 passed 全綠。審查後補：SearchPage「切檢視不重抓」回歸測試、drill+search 片段高亮透傳。
- **Phase 2c 清單（cutover 增量，含 2b 審查延後 Minor）**：cutover 把 `/` 導向 `/app/search`（如 monitor，需 :8097 後端先對齊現行 main）；market 分組經表格往返遺失（持久化 group 或文件化接受）；檢視工具列改恆顯（空/載入也可切）；a11y 批次（表格列 role/aria-label、展開鈕 aria-expanded、ViewSwitch 鈕 type=button）；TableView 空 market 顯 '—' 對齊。

### Phase 3 — ask 串流問答　[風險 高｜最高難度區]　✅ 已完成（PR #44）
- **遷移模組**：`ask.js`(676，SSE+多輪狀態) / `markdown.js`(172) / `modal.js`(75) / `confirm.js`(52)
- **消費端點**：`/api/ask`(SSE) / `/api/history` / `/api/conversations`(`/{id}`) / `/api/feedback` + 各 delete 端點
- **重點**：SSE 自解 + abort、多輪 `conversation_id` 快取、引用 `[n]` 綁定、歷史側欄、切換對話中止進行中串流、markdown 安全渲染
- **硬約束**：見 A5「SSE 整合手法」；策略＝先移植不重構、保 vanilla 為對照、寫 e2e 驗事件序
- **依賴**：Phase 2（共用側欄/篩選面板）
- **交付（2026-07-01，PR #44，off main 89a0d8e）**：SDD 12 任務 + 逐任務審查 + opus 整支審查（Merge after fixes→已修）；新增 `/app/ask`（**未 cutover**，後端零變動、無 schema）。雙重 latest-wins、XSS 安全 markdown（無 `dangerouslySetInnerHTML`、外部連結 http-only 串流/重播兩路徑一致）、多輪 condense、live e2e（:8098）綠。
- **Phase 3c 待辦（cutover/polish，承最終審查 Minor）**：cutover `/`→`/app/ask`（需 :8097 對齊現行 main）；`markdown.tsx` module-level `keySeq` 改局部閉包（每 token 全量 remount 隱患）；e2e Step 4 來源無命中時 soft-assert 讓 skip 可見；`openConversation`/`onClose` 用 `useCallback`；Sidebar 空清單/active class 測試；補各 hook 邊界測試（401 redirect、catch 錯誤訊息等，見 `.superpowers/sdd/progress.md` Minor 累積）。

### Phase 4 — 深度研報 report　[風險 中高]
- **範圍**：問答區「生成深度研報」面板 + report SSE + PDF 下載 + 報告 modal；report_gate（問答後是否建議報告）
- **消費端點**：`/api/report`(SSE) / `/api/report-doc/{id}/pdf` / `/api/report/{id}/full` / `/api/report/{id}/file`
- **重點**：複用 Phase 3 的 SSE 基礎抽象；長回答 markdown；PDF 下載 UX
- **依賴**：Phase 3

### Phase 5 — login + help + 終局退役　[風險 中]
- **範圍**：`login.html`(91) 可選移 `/app/login`（或保留 vanilla、認證層分離）；`help.html`(347) 靜態→React 元件；切 basename `/app`→`/`、退役 vanilla、刪 `web/static`、`index.html`(1031) 取消掛載
- **依賴**：Phase 2–4 完成且平價（「SPA 全面佔領」的終局）

### Phase 6+ — 原生 SPA 新頁（真正的驅動目標）　[風險 視頁面]
- **範圍**：圖表/儀表板、表單/後台 CRUD（React 生態回本最高的場景）
- **依賴**：Phase 0–5 地基；圖表頁需先鎖定圖表庫（A5）

---

## 驗證與使用方式

- **Part A**：每項可獨立小 PR（多單檔）。改 `frontend/**` 免重啟（`_NoCacheStatic`/build 後即生效），改 `web/server.py` 需 restart。前端改動跑 `rtk npm --prefix frontend run build` 與 `rtk npm --prefix frontend run test`；後端改動跑 `rtk uv run pytest -q`。
- **Part B**：每個 Phase 完成走 live 平價——不擾動正式 `:8097` systemd，於 `127.0.0.1:8098` 跑分支 server + Playwright MCP 真瀏覽器驗（login → 渲染 → 0 console error → 與舊頁視覺/行為平價），再做 cutover redirect。

> 後端/全棧 Roadmap 軌（每日簡報、結構化訊號、findb 整合、MCP/REST）見 `docs/ROADMAP.md`，各自獨立規劃，不在本文件範圍。
