# 廷豐智能研報 — React 19 SPA 重寫設計（以 `廷豐智能研報.dc.html` 為準）

**日期**：2026-07-03
**狀態**：設計定案，待寫實作計畫（writing-plans）
**決策脈絡**：使用者拍板「前端改用 React 19 開發，完全沿用 `廷豐智能研報.dc.html` 的設計，不做任何調整」。本設計把該 Claude Design mockup 逐像素落地為一支全新的 React SPA。

---

## 0. 已定案決策（不可改）

| 決策 | 結論 |
|---|---|
| 建置策略 | **Greenfield**：全新 `frontend/`，不沿用 7/2 移除的 Mantine SPA 程式碼；舊碼與後端僅作**唯讀規格參考** |
| 設計來源 | `docs/design/廷豐智能研報.dc.html`（**已納入 repo**；源自 Claude Design 專案 `911c9279-717a-4810-b6ff-25c8939846bd` 同名檔）為**唯一權威**；`docs/DESIGN_SPEC.md` 為次要參考，衝突時以 `.dc.html` 為準 |
| 還原度 vs 對等 | mockup 沒畫到、但現有 vanilla 已有的功能，**沿用 `.dc.html` 的視覺語言補齊**；功能對等是硬約束，**唯一例外＝不做「市場分組／索引／drill-in」** |
| 語言 | TypeScript |
| UI 函式庫 | **不引入 Mantine**；零元件庫，行為原語自製；無圖表庫（監控市場分佈為 CSS bar） |
| 樣式機制 | **`tokens.css`（設計 token→CSS 變數）＋ per-component CSS Modules**；只有動態值（市場色、相關度條寬）走 inline |
| 交付 | 分階段、`/app` 與 vanilla 共存，最後 cutover；vanilla 全程不下線 |
| 後端 | `/api/*`、`/login`、`/logout` 的**契約與邏輯零改動**；允許的後端變更僅兩項路由/服務層：①Phase 0 服務 `/app`（shell＋immutable 資產）；②Phase 4 cutover `index()` `/`→307 `/app/search`。**middleware 不動** |

---

## 1. 範圍

**In scope**
- 全新 React SPA，服務於 `/app`，涵蓋三頁：檢索 `/app/search`、問答 `/app/ask`、監控 `/app/monitor`。
- 全站 App Shell：`.dc.html` 的可收合左側欄（60px 迷你軌 ↔ 272px 完整側欄）＋手機底部分頁列＋帳號選單。
- 登入頁：改寫 `web/static/login.html`（standalone，非 React route）對齊 `.dc.html` 登入卡。
- 後端重新加回 `/app` 服務（catch-all SPA shell ＋ immutable 雜湊資產靜態層）。

**Out of scope**
- 後端 API／檢索／問答／研報生成邏輯、資料欄位（只碰視覺與版面層；功能平價為硬約束）。
- 深色模式（token 預留，本設計只做淺色）。
- 深度研報 PDF（WeasyPrint）版面——後端另案。
- 使用手冊 `help.html`（維持原樣）。
- 市場分組／市場索引／drill-in（使用者明確排除）。

---

## 2. 技術基座

### 2.1 相依套件

| 類別 | 選用 | 版本對齊 |
|---|---|---|
| 核心 | `react` / `react-dom` 19.2、`react-router` 8、`@tanstack/react-query` 5、`zod` 4 | 沿用舊 SPA 版本 |
| 建置測試 | `vite` 8、`vitest` 4、`@playwright/test`、`eslint` + `typescript-eslint`、`typescript` | 沿用舊配置 |
| 行為原語 | **自製**：`Popover`、`Modal`、`Menu`、`useFocusTrap`、`useClickOutside`、`useMediaQuery` | — |
| 圖表 | 監控市場分佈＝CSS bar（div 寬度）。**不需圖表庫／KPI 元件**：`.dc.html` 與真實研報流程都不 inline 渲染研報 markdown/KPI/圖表 | — |

**明確不引入**：`@mantine/*`、`recharts`、`tailwind`、`@hookform/*`。

### 2.2 樣式機制（tokens.css + CSS Modules）

- `frontend/src/styles/tokens.css`：所有設計 token 落成 `:root` CSS 變數（見 §3）。
- 每個元件一支 `X.module.css`：把 `.dc.html` 的 inline style 值搬成 class，逐一比對數值。
- **inline style 只保留動態值**：市場徽章底色 `background:{{mktColor}}`、相關度條寬 `width:{{score}}%`、進度條寬、市場分佈條寬與色。
- `.dc.html` 用的 `style-hover` / `style-focus` / `@media` 是 Design-Compiler 專屬語法，React inline 無法表達 → 一律落成 CSS Modules 的 `:hover` / `:focus-visible` / `@media`。**這屬於忠實實現設計意圖，非改設計。**
- 全域 reset 與 keyframes（`tf-pulse`、`tf-indet`、`tf-spin`、`tf-up`）＋ `prefers-reduced-motion` 歸零，照抄 `.dc.html` `<helmet>` 內的 `<style>`。
- Noto Serif TC 經 Google Fonts CDN（`display=swap`），CDN 不通退系統襯線、不阻塞渲染。

### 2.3 路由與整合

- `vite.config.ts`：`base:'/app/'`，dev proxy `/api`、`/login`、`/logout` → `http://localhost:8097`。
- react-router `createBrowserRouter(..., { basename: '/app' })`；layout route（App Shell）掛 `<Outlet/>`，子路由 `/search`、`/ask`、`/monitor`，`/` → `Navigate to /search`，`*` → NotFound。
- 後端 `web/server.py` 重新加回（移植自 `7e40cef` 前的實作精神，重寫）：
  - `GET /app` 與 `GET /app/{path:path}` catch-all → 回傳 `dist/index.html`（SPA shell）。
  - `/app/assets/**` 雜湊檔以 immutable 長快取；`index.html` 不快取。
  - **auth middleware 不動**：未登入存取 `/app/*` HTML → 302 `/login`（**不帶 next**，符合現有 `tests/test_auth_next.py`）；登入後回 `/` →（cutover 後）307 `/app/search`。`/api/*` 的 401 由前端 `getJSON` 觸發 `redirectToLogin()`（`location.assign('/login?next='+目前路徑)`），屬前端行為、不改後端。
  - **顯式取捨（deep-link 分享）**：已登入者分享 `/app/ask?c=...` 可正常還原（middleware 放行 → SPA 路由還原）；**未登入**收件者需先登入，登入後落在 `/`（cutover 後 →`/app/search`）而非原 deep-link——為維持後端零改動與現有 auth 測試，接受此限制（§6/§7 的「URL 可分享」指已登入 session 內）。

### 2.4 狀態與資料流

- **Server state**：TanStack Query（`getJSON` + Zod 驗證；401 → 導 `/login?next=`）。
- **UI state**：react-router `useSearchParams`（`q`、market、進階篩選、view 寫入 URL 可分享）＋局部 `useState`。
- **SSE**：fetch-based reader（`readSSE`），`/api/ask`、`/api/report` 皆用；latest-wins（每次送出遞增 request id，只有最新 id 的事件可寫入）；元件卸載時 `AbortController` 中止。

---

## 3. 設計 Token（`tokens.css`，值取自 `.dc.html`）

### 3.1 介面基調
```
--tf-canvas:#f6f7f9;  --tf-surface:#ffffff;
--tf-sidebar:#fbfbfc;               /* 側欄底 */
--tf-border:#e4e7ec;  --tf-border-weak:#f2f4f7;  --tf-divider:#eceef1;
--tf-text-1:#101828;  --tf-text-2:#344054;  --tf-text-3:#667085;  --tf-text-4:#98a2b3;
```
### 3.2 品牌金
```
--tf-gold-text:#8a5a0f;   /* 金色文字唯一合法色；主按鈕/選中底/score 填充 */
--tf-gold-strong:#ae7415; /* 只准 ≥18.66px 粗體大字或裝飾底 */
--tf-gold-hover:#7a4f0c;  /* 金按鈕 hover */
--tf-gold-tint:#faf3e3;   /* 淡金底：active 膠囊/回顯 chip/導覽 active 底 */
--tf-on-gold:#ffffff;  --tf-on-gold-muted:rgba(255,255,255,.75);
/* 10 階：#fbf3e3 #f3e4c6 #e7c98c #dbaf52 #d09a26 #c98e10 #c58808 #ae7415 #9c6710 #8a5a0f */
```
### 3.3 字型
```
--tf-serif:'Noto Serif TC',Georgia,'Times New Roman',serif;      /* 600/700，品牌 glyph 800 */
--tf-sans:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI','PingFang TC','Microsoft JhengHei',system-ui,sans-serif;
--tf-mono:ui-monospace,Menlo,monospace;   /* 監控時鐘/PDF 檔名 */
```
數字一律 `font-variant-numeric:tabular-nums`（統計、表格、KPI、count）。

### 3.4 圓角 / 陰影 / 動效 / 斷點
```
圓角：卡片/面板/輸入/modal 12；導覽 active 膠囊/segmented 10；膠囊 999；一般小控制 8
陰影：--tf-shadow-xs:0 1px 3px rgba(16,24,40,.06);   /* 卡片預設，搭 1px border */
      segmented active:0 1px 3px rgba(16,24,40,.12);
      浮動輸入卡:0 4px 16px rgba(16,24,40,.08);  浮動 view 鈕:0 6px 20px rgba(16,24,40,.14);
      popover/menu:0 12px 32px rgba(16,24,40,.16);  detail modal:0 20px 48px rgba(16,24,40,.24)
動效：--ease-out:cubic-bezier(.22,1,.36,1); --dur-1/2/3/4:120/180/240/340ms；reduced-motion 全歸零
斷點：手機 768（rail↔底欄）；卡片兩欄 1024（isNarrow 以下一欄）
版面：rail 60；full sidebar 272；底欄 56+safe-area；搜尋列 560；問答流 760；內容 max 1120
```

### 3.5 語意色（`meta.ts`，取自 `.dc.html` MARKETS/PTYPE，不得改）
```
市場：TW #34c759 台股｜US #007aff 美股｜HK #ff9500 港股｜CN #ff3b30 陸股｜FX #00c7be 外匯
     WTX #af52de 台指期｜MACRO #ff2d55 總經｜GLOBAL #5856d6 全球｜CRYPTO #a2845e 加密｜fallback #8e8e93
商品：股票 #0a84ff｜指數 #5e5ce6｜期貨 #ff9f0a｜選擇權 #bf5af2｜ETF #30d158｜債券 #0bb8c4｜外匯 #00c7be｜原物料 #ac8e68｜加密 #e0a400
狀態：成功/最新 #34c759（描邊字 #248a3d）｜警示 #ff9f0a｜錯誤 #ff3b30｜高亮 <mark> 底 #fff3bf
市場膠囊順序：全部, TW, US, HK, CN, WTX, FX, MACRO, GLOBAL, CRYPTO（僅顯示語料中存在者）
```

---

## 4. App Shell / 導覽（layout route）

**結構**：`display:flex;height:100vh;overflow:hidden`，左側欄 + 右側 `<Outlet/>`。視覺隱藏 `<h1>廷豐智能研報</h1>`。

### 4.1 元件
| 元件 | 責任 | 對應 `.dc.html` |
|---|---|---|
| `AppShell` | 讀 `sidebarCollapsed`(localStorage) 與 `isMobile`(useMediaQuery 768)，決定 rail 型態；掛 Outlet、DetailModal portal | root `<div>` |
| `SideRailMini`(60) | 廷 glyph（→ /search）、展開鈕、三導覽圖示鈕、底部帳號鈕＋Popover | `railMini` |
| `SideRailFull`(272) | 廷＋站名＋收合鈕、三導覽 row、divider、`新對話`、`歷史對話` 清單、底部帳號 row＋Popover | `railFull` |
| `NavItem` | active＝金膠囊底 `--tf-gold-tint`＋字 `--tf-gold-text`＋700＋`aria-current="page"` | `mk/mkRow/mkMini` |
| `ConversationList` | `新對話` → 導 `/ask` 開新對話；歷史清單 → 導 `/ask?c=<id>` | `convs` / `newConv` / `selectConv` |
| `MobileTabBar`(<768) | 四格：檢索/問答/監控/帳號，active 只變色（無底膠囊） | `isMobile` 底欄 |
| `AccountMenu`(Popover) | 帳號名＋「研究部 · 分析師」＋`登出`（原生 `POST /logout`） | `accountOpen` |

### 4.2 資料
- 帳號名：`GET /api/stats`.username（fallback「分析師」）。
- 歷史對話：`GET /api/conversations?limit=50`（TanStack Query）；點擊帶 `conversation_id` 導向 ask。
- 登出：`AccountMenu` 內以原生 `<form method="post" action="/logout">` 送出（非 fetch，走瀏覽器導頁）。

---

## 5. 登入頁 `/login`（standalone HTML，非 React route）

- 在 auth 牆外，不能引用 SPA 資產 → 維持 `web/static/login.html`，樣式**全部 inline**。
- 逐一對齊 `.dc.html` 登入卡：平色畫布 `#f6f7f9`；360px 白卡（圓角 12、`--tf-border` 框、`--tf-shadow-xs`、padding 36/32）；廷 glyph 44px 襯線 800 金；站名 21px 襯線 700；副標「券商研究報告 · 智能檢索平台」12.5 `--tf-text-3`；帳號/密碼欄（圓角 8、focus 金框 `0 0 0 3px rgba(138,90,15,.12)`）；錯誤框（`#fef3f2`/`#fecdca`/`#b42318`）；全寬金鈕圓角 999。
- **保留現有登入表單語意**：`POST /login`（帳號/密碼/next），錯誤訊息沿用後端文案（`帳號或密碼錯誤。`／`嘗試次數過多，請稍後再試。`）。只換視覺，不換行為。

---

## 6. 檢索頁 `/app/search`

### 6.1 版面（照 `.dc.html`）
頂部固定工具列（白底、下邊框）：
1. 搜尋列 `SearchBar`：置中限寬 560、左 `IconSearch 18`、圓角 12、focus 金 ring。placeholder `搜尋主題、公司、事件…`。
2. `MarketChipBar`（max-width 1120 置中）：全部＋各市場膠囊（左 8px 語意色圓點＋全語料 count），active＝深金底白字；右端 `margin-left:auto` 放 `排序` 選單（`SortMenu`）與 `更多篩選` 鈕。
3. `MoreFiltersPopover`（420、右對齊）：**商品類型單選膠囊**（點一個選、再點取消 → 單值 `instrument_type`）、**報告類型單選膠囊**（→ 單值 `report_type`）、**個股／期貨切換**（兩顆膠囊 → `relates_stock`／`relates_futures` 布林；取代 `.dc.html` 的標的文字框，因後端無標的值篩選）、`清除條件`／`套用`。鈕上金 badge 顯進階條件數。
4. `ActiveChips`：作用中進階條件以可刪金膠囊回顯（`商品：X`／`類型：X`／`個股`／`期貨`）。

內容捲動區（max-width 1120）：
- `ResultsMeta`：見 §6.3 文案規則。
- 月分組：sticky pill 標頭（`f2f4f7` 底＋框，襯線 700 15px 標題＋金 count「N 篇」）。
- `showCards`：兩欄卡片網格（gap 10；`isNarrow(<1024)` 一欄）。
- `showTable`：表格（圓角 12＋框＋淡影容器）。
- `EmptyState`（`totalFound===0`）：襯線標題＋補救句＋金鈕 `清除篩選再試`。
- `LoadMore`：次按鈕 `載入更多（還有 N 篇）`。
- `ViewSwitch`：**浮動** segmented（`position:fixed` 右下；手機 bottom 80，桌機 24），卡片/表格圓形鈕。

### 6.2 元件
`SearchPage`、`SearchBar`、`MarketChipBar`、`MoreFiltersPopover`、`SortMenu`、`ActiveChips`、`ResultsMeta`、`MonthGroup`、`ResultCard`、`TableView`、`EmptyState`、`LoadMore`、`ViewSwitch`、`ReportDetailModal`（共用，§9.4）。

`ResultCard`：市場徽章（語意色底白字）＋`最新`徽章（綠描邊，**前端計算**，規則見 §6.3）＋襯線標題 14.5/700＋商品類型/標的膠囊（`--tf-border-weak` 底）；
- **searchMode**：高亮片段（`<mark>` 底 `#fff3bf`）＋相關度條（金填充，寬＝score）＋`相關度 N`；
- **browseMode**：摘要（`summary`，≤88 字截斷）；
- 底列：`來源 · 日期` ｜ `查看全文 ›`（金字）。整卡可點 → DetailModal；hover 金框。

### 6.3 資料流（真實 API 對應）
- **browseMode（無 `q`）** → `GET /api/reports?market=&instrument_type=&report_type=&relates_stock=&relates_futures=&sort=&limit=&offset=`；排序預設日期新→舊；卡片顯 `summary`。
- **searchMode（有 `q`）** → `GET /api/search?q=&passages=N&market=&...`；排序相關度；卡片顯 passage 片段＋`best_score`（相關度條）。
- **月分組**：對已載入結果依 `report_date` 前 7 碼（`YYYY-MM`）分組，標頭文案 `YYYY 年 M 月`；組內順序沿用當前排序。（**不做市場分組**）
- **`最新` 徽章（前端計算）**：鏡像後端 ask 來源規則（`answer.py:335-343`，**僅日期嚴格大於才更新**）——在**目前已載入結果集**中取 `report_date` 嚴格最大者標「最新」；**同日並列時保留最先出現者（當前排序下第一筆），只標一篇**；無 `report_date` 者不參與。結果集變動（載入更多／換篩選／改排序）即重算，純函式可單元測試。
- **篩選 → URL / API 參數（皆單值，對齊後端）**：`q`、`market`、`instrument_type`、`report_type`、`relates_stock`、`relates_futures`、`sort`、`view`。「全部」不帶該參數。切 `view` 為呈現態，**不進 query key、不重抓**。（**不支援** 多值陣列或標的值篩選——後端無此契約）
- **排序（`SortMenu`，功能平價）**：搜尋模式＝`相關度`(relevance，預設)／`最新`(date_desc)／`最舊`(date_asc)；瀏覽模式＝`最新`(date_desc，預設)／`最舊`(date_asc)（無相關度）。sort 改變**會重打 API**（進 query key，與 view/group 不同）；對應現行 vanilla 的排序 chip。
- **分頁**：`載入更多` 提升 `limit`/`offset`（初始 8，每次 +N；沿用後端分頁），`hasMore = 已載入 < total`，`remaining = total - 已載入`。
- **ResultsMeta 文案**：有 q → `「q」· <市場> — 找到 N 篇研報`；無 q 有篩選 → `<市場|全部研報> — N 篇`；皆無 → `全部研報 — 共 N 篇`。
- **EmptyState 文案**：`找不到「q」的相關研報` / `沒有符合條件的研報`＋`換個說法或關鍵字試試，或清除目前的篩選條件重新檢索。`

---

## 7. 問答頁 `/app/ask`

### 7.1 版面（照 `.dc.html`）
置中訊息流 760；底部浮動 composer。空狀態＝廷 glyph 34＋`向廷豐智能體提問`＋副標＋**置中 composer（不放範例膠囊）**。

### 7.2 元件
| 元件 | 責任 |
|---|---|
| `AskPage` | 讀 `?c=<id>` 載入對話；管理 turns、SSE、捲動貼底 |
| `AskEmpty` | 空狀態（照 §7.1） |
| `UserBubble` | 金底右氣泡，圓角 16/16/4/16 |
| `AssistantMessage` | `已思考 N 秒`＋襯線 markdown（h3 襯線/p/ul）＋行內 `[n]` 金膠囊＋動作列 |
| `ThinkingSteps` | 思考中卡：理解問題→檢索研報→閱讀整理→(網路補充)→生成回答；三態 ✓/spinner/dot |
| `SourcesPanel` / `ExtSourcesPanel` | `資料來源 N` / `外部參考 N` 可展開面板 |
| `DeepReportPanel` | offer(要/不用)→生成中(金進度條，映射 `status` 階段)→完成(金卡＋`下載 PDF`，href=`done.download_url`)→**失敗態**(amber/紅卡＋重試)。**無 inline markdown/KPI/圖表，無全文 modal** |
| `AskComposer` | 底部浮動卡、textarea 自增高、金圓送出鈕 |

### 7.3 資料流
- **送出** → `POST /api/ask`（body：question、conversation_id?）SSE：
  - `status{stage,count,thinking_ms}` → 驅動 `ThinkingSteps`（stage 名對應五步）。
  - `sources[]` / `ext_sources[]` → 掛到當前 turn（每 turn 獨立來源）。
  - `token`（字串）→ 串流 append 回答本文。
  - `notice`（字串）→ **離題提示**（amber，沿用視覺語言）。
  - `done{cited,qa_id,conversation_id,thinking_ms,offer_report,report_title}` → 收尾；`offer_report` 決定是否顯示深度研報 offer。
- **markdown 渲染**：解析 `[n]` → 金膠囊（點擊展開對應來源面板）；標題層級襯線化、內文行高 1.7。串流與重播兩路徑一致；XSS 安全（不用 `dangerouslySetInnerHTML`；外部連結僅 http/https）。
- **多輪**：帶 `conversation_id`；後端 condense；載入既有對話 `GET /api/conversations/{id}` → `HistoryItem[]` 重播（含 sources/ext_sources/reports/feedback/is_offtopic）。
- **回饋**：`POST /api/feedback{qa_id,value}` best-effort（失敗不打擾）。
- **深度研報** → `POST /api/report`（body：**`question`〔必填〕**＋可選 `qa_id`／`conversation_id`）SSE：`status{stage: retrieving|writing|rendering}`（另可能 `searching_web`）→ 驅動進度條、`token`→ 撰寫中片段（用於進度感，**不 inline 渲染 markdown**）、`done{report_id,title,download_url,thinking_ms}`→ 完成卡（`下載 PDF` href=`download_url`＝`/api/report-doc/{id}/pdf`）、`error`→ 失敗態。**生成研報無 markdown 端點**——完成即下載 PDF、不做全文 markdown modal。完成研報**跨輪/跨對話持久化**（寫入該 turn，去重避免第二份抹除；歷史重播由 `HistoryItem.reports[].download_url` 顯下載卡）。下載連結 scheme 守門（僅同源/相對路徑）。
- **IME 守衛**：Enter 送出前檢查 `e.nativeEvent.isComposing`（React19 SyntheticEvent 的 `isComposing` 會是 undefined），組字中不送出；Shift+Enter 換行。

---

## 8. 監控頁 `/app/monitor`

### 8.1 版面（照 `.dc.html`，換皮不改結構）
- Header：襯線 `研報導入監控` 26＋`N 篇已導入 · X/4 條管線執行中`＋`LIVE`徽章（金底、呼吸綠點 `tf-pulse`）＋時鐘（mono、tabular、1s tick）。
- `KpiGrid`：2 欄（手機）→ 4 欄；tile＝label 12＋大數 26/700/tabular＋後綴＋delta。四項：已導入報告／總片段 CHUNKS／標註進度／摘要進度。
- 面板網格（2 欄）×2 列：
  - `語意標註`：done/total＋pct＋金進度條＋速率行。
  - `報告導入`：current 檔名＋**不定式**金滑塊（`tf-indet`）＋速率行。
  - `摘要生成`：done/total＋pct＋金進度條＋速率行。
  - `處理管線`：四列（dot＋name＋狀態徽章）。
- `MarketDist`：橫條（label 52＋bar＋count 66＋pct 38），色用市場語意色。
- Footer：`資料每 30 秒自動更新 · 廷豐智能研報導入管線`。

### 8.2 資料流
- `GET /api/progress`（`cache:no-store`）每 **30s** 輪詢：`db{reports,chunks,markets[]}`、`summary{done,total,pct}`、`tagging{pct,done,total,fail}`、`ingest{ingested,fail}`、`pipelines{web,ingest,tag,summaries}`、`orchestrator{label,status,timestamp}`。
- `GET /api/stats` 取總數。
- **速率/ETA**（延伸）：開頁後以差分算 `篇/分`，面板顯 `速率 X.X 篇/分 · 預估剩餘 ~N 分`（純函式 `rate`/`eta`，可單元測試）。
- **數字補間**（延伸）：KPI/大數用 `useTween` 平滑過渡；`reduced-motion` 時直接跳值。
- `LIVE` 徽章三態：執行中金/閒置灰/錯誤紅（依 pipelines 與 fail）。

---

## 9. 共用底層

### 9.1 `frontend/src/styles/tokens.css`
§3 全部 token＋全域 reset＋keyframes＋`prefers-reduced-motion` 歸零。

### 9.2 `lib/meta.ts`
市場/商品語意色與標籤（§3.5）；`marketColor(code)`、`marketLabel(code)`、`ptypeColor(name)`，fallback `#8e8e93`。

### 9.3 `lib/`（邏輯重寫，以舊碼為規格參考）
`api.ts`(getJSON+ApiError+redirectToLogin)、`readSSE.ts`、`markdown.tsx`(cite 膠囊)、`grouping.ts`(月分組)、`filters.ts`、`highlight.ts`/`terms.ts`(命中高亮)、`tableSort.ts`、`rate.ts`/`eta.ts`、`normalize.ts`。全部附 Vitest。

### 9.4 行為原語與共用元件
- `Popover`/`Menu`/`Modal`（fixed 遮罩＋click-outside＋Esc 關閉＋`useFocusTrap`）。
- `Icon`：Tabler 風 SVG（`stroke-width 1.8`，size 18–21，直接內嵌與 `.dc.html` 相同的 path）。
- `ReportDetailModal`（檢索用）：資料取自搜尋結果 item（標題/市場/來源/日期/類型/商品/摘要，**無需再打 API**）＋PDF iframe（`/api/report/{id}/file`）＋`在新分頁開啟`／`下載原始檔`（同一端點）。

---

## 10. API 合約（前端消費，後端不改）

| 端點 | 用途 | 回應要點 |
|---|---|---|
| `GET /api/stats` | 導覽帳號名、檢索篩選選項 | `total_reports,total_chunks,markets[],instrument_types[],report_types[],username` |
| `GET /api/reports` | 瀏覽 | `total,offset,items[reportItem]` |
| `GET /api/search` | 語意檢索 | `query,market,total,results[reportItem+rank,best_score,match_count,passages[]]` |
| `GET /api/progress` | 監控 | 見 §8.2 |
| `POST /api/ask`(SSE) | 問答（body：`question`＋conversation_id＋單值篩選＋`k`） | `status/sources/ext_sources/token/notice/done/error` |
| `POST /api/report`(SSE) | 深度研報（body：`question`＋可選 qa_id/conversation_id） | `status/sources/token/done{report_id,title,download_url,thinking_ms}/error` |
| `GET /api/report-doc/{id}/pdf` | **生成研報 PDF**（`done.download_url` 指向此） | `application/pdf`（pdf_path 不存在時由 markdown 即時重建） |
| `POST /api/feedback` | 讚/倒讚 | `{ok}`（best-effort） |
| `GET /api/conversations` / `/{id}` | 對話清單/重播 | `ConversationSummary[]` / `HistoryItem[]` |
| `DELETE /api/conversations/{id}` | 刪對話 | 404/405 退回 `POST .../delete` |
| `GET /api/report/{id}/full` | **來源研報** metadata（非生成研報、**無 markdown**） | `{report_id,file_name,market,source,summary,report_date,report_type,has_file}` |
| `GET /api/report/{id}/file` | **來源研報**原始 PDF | iframe/下載 |
| `POST /login` `POST /logout` | 認證 | 原生表單導頁 |

reportItem 欄位：`report_id,file_name,market,source,summary,report_date,report_type,instrument_types[],relates_stock,relates_futures,stock_targets[],futures_targets[]`。

---

## 11. 交付分階段（每階段獨立 PR、可驗證、vanilla 不下線）

| Phase | 內容 | 驗收 |
|---|---|---|
| **0 骨架＋Shell＋登入** | Vite/TS/測試腳手架、`tokens.css`、路由、App Shell（左側欄收合＋手機底欄＋帳號選單）、後端加回 `/app` 服務、改寫 `login.html` | build/lint/typecheck 綠；`/app/search` 空殼可導覽；登入頁對齊 `.dc.html`；vanilla 仍在 |
| **1 檢索頁** | SearchBar/市場快篩/更多篩選/回顯/結果卡(兩欄)/表格/月分組/載入更多/檢視切換/DetailModal；真實 `/api/reports`＋`/api/search` | 單元＋e2e(:8098)；瀏覽/檢索雙模式、篩選 URL 分享、詳情 modal |
| **2 問答＋研報** | 訊息流/SSE 串流/thinking steps/來源+外部參考/離題 notice/回饋/多輪/深度研報 offer→生成(進度條)→完成(下載 PDF)/失敗；`/api/ask`、`/api/report` | live e2e(:8098，預熱 BGE-M3)；串流/重播一致、IME 守衛、latest-wins |
| **3 監控頁** | KPI/面板/管線/市場分佈/速率 ETA/補間；`/api/progress` | 單元(rate/eta)＋e2e；30s 輪詢、LIVE 三態 |
| **4 cutover** | `index()` 改 307 → `/app/search`；對齊 auth 測試；（選）退役 vanilla 靜態 | auth 測試綠；未登入 `/` 仍 302 login |

---

## 12. 測試策略

- **Vitest 單元**：純函式（grouping/filters/highlight/rate/eta/markdown/tableSort）、hook（useSearchResults/useAskStream/useReportStream/useConversations/useMonitorRate）、元件（Testing Library）。每元件 TDD。
- **Playwright e2e**：打**工作樹起的 `:8098`**（非正式 `:8097`）；先預熱 BGE-M3（首問冷啟動會假性 render 壞掉）；跑完 `kill`，殺埠前 `ss` 核對避免誤殺正式站。問答/研報 live LLM 用 terminal-agnostic 斷言（`done` 或優雅降級皆綠）、延遲容忍（研報首 token 可達 ~180s、done ~300s）。
- **CI 門檻**：`tsc --noEmit && vite build`、`eslint .`、`vitest run` 全綠才進下一 Phase。
- **RTK 陷阱**：RTK 會遮蔽 vitest 非零 exit → 跑測試用 `rtk proxy` 或直接呼叫二進位確認 exit code。

---

## 13. 無障礙硬規則（不可退步）

1. 金色文字一律 `#8a5a0f`（AA）；`#ae7415` 僅 ≥18.66px 粗體。
2. 全站鍵盤焦點環 `outline:2px solid #8a5a0f;offset:2px`，永不移除。
3. 導覽 active 帶 `aria-current="page"`；手機底欄與帳號格觸控目標 ≥44px。
4. `prefers-reduced-motion` 時所有動效時長歸零。
5. 每頁唯一 `h1`（視覺隱藏「廷豐智能研報」）；標題梯級不跳號。
6. 篩選 chip `aria-pressed`；Popover 觸發鈕 `aria-expanded`；表格排序 `aria-sort`。

---

## 14. 風險與陷阱（來自歷史教訓）

- **BGE-M3 背景暖機**：e2e 首問/首檢索冷啟動假性失敗 → 測前預熱。
- **React19 `isComposing`**：SyntheticKeyboardEvent 回 undefined → 用 `e.nativeEvent.isComposing` 守 IME，否則 IME Enter 誤送。
- **latest-wins**：問答/研報 SSE 必須以 request id 過濾舊事件；卸載 Abort。
- **研報跨輪殘留**：完成研報去重寫入該 turn（唯一 turn id），cancel 重置，避免第二份抹除或跨對話洩漏。
- **`:8098` vs `:8097`**：一律對工作樹埠跑 e2e，殺埠前 `ss` 核對。
- **immutable 靜態快取**：`/app/assets/**` 長快取＋雜湊檔名；`index.html` 不快取，否則改版不生效。
- **後端 `llm.py` 長研報行上限**：已於 `4839293` 修（stdout limit 提高），本前端重寫不受影響，但研報 e2e 需容忍長生成。
- **共用工作樹**：其他使用者可能有未提交 WIP；一律 `git add <明確檔>`，提交前 `git diff --staged --stat` 驗範圍。

---

## 15. 非目標重申

只做視覺與版面層；後端 API／檢索排序／問答邏輯／研報生成／資料欄位一律不動，功能平價為硬約束。深色模式、PDF 版面、`help.html`、市場分組不在本次範圍。
