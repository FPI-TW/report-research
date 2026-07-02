# React 前端整站視覺重設計（精緻 SaaS × 襯線編輯）設計

**日期**：2026-07-02
**範圍**：SPA 三頁（/app/search、/app/ask、/app/monitor）+ 全站導覽骨架 + 登入頁（vanilla）
**策略**：地基先行、分段 PR（PR1 地基 → PR2 檢索 → PR3 問答 → PR4 登入+收尾），各分支從 `origin/main` 開

## 背景與目標

SPA 遷移（Phase 2-4）與 cutover 已完成，功能完備但視覺仍是「Mantine 預設 + 零散樣式」：
三頁之間風格不統一、缺乏品牌識別度與金融研報的專業質感、版面結構（頂欄+雙欄）未針對
內容型態最佳化。本設計做一次**整站視覺與版面重設計**，不動後端、不動業務邏輯。

## 使用者拍板決策（2026-07-02，經視覺 mockup 比較選定）

| 決策點 | 拍板 |
|--------|------|
| 視覺風格 | **C 精緻 SaaS 底 × B 襯線編輯標題**（Linear/Notion 質感 + FT 刊物感標題） |
| 範圍 | SPA 三頁 + 登入頁同步換裝；深度研報 PDF（WeasyPrint）不在本次範圍 |
| 深色模式 | 先不做；token 架構預留（色彩全走 CSS variables / theme token） |
| 全站導覽 | **左側窄軌**（60px，圖示+小字）；手機改**底部分頁列**（第 4 格＝帳號） |
| 檢索頁版面 | **頂部篩選工具列 + 全寬結果**（移除篩選側欄，卡片改兩欄網格） |
| 問答頁版面 | **對話側欄常駐可收合 + 置中訊息流**，回答用襯線編輯內文（去卡片框） |
| 監控頁 | 結構不變，換皮 |
| 實作策略 | 地基先行、分段 PR，每段合併後即可部署、可回退 |

## 設計系統基礎（PR1）

### 色彩 token

- 品牌金色票不變：`gold` tuple（主色 `#ae7415`、深金 `#8a5a0f`）。
- 新增介面基調（Mantine theme + CSS variables，供未來深色模式加層）：
  - 畫布底 `--canvas: #f6f7f9`（AppShell main 背景）
  - 卡片面 `--surface: #ffffff`
  - 邊框 `--border: #e4e7ec`（弱 `#f2f4f7`）
  - 文字四階：`#101828` 主標 / `#344054` 內文 / `#667085` 次要 / `#98a2b3` 弱化
- **對比硬規則**：金色作為文字色一律用深金 `#8a5a0f`（白底 ≈5.4:1，過 AA）；
  `#ae7415` 只用於大字（≥18.66px bold）與裝飾底色。

### 字型

- **襯線 `Noto Serif TC`**（600/700），Google Fonts CDN 載入（`index.html` preconnect +
  css2 link，`display=swap`；unicode-range 自動切片只下載用到的字）。
  斷網退路：`"Noto Serif TC", Georgia, "Times New Roman", serif`（CJK 落到系統襯線）。
- 襯線用途：品牌字、頁區標題、結果卡標題、問答回答的 markdown 標題層級、深度研報
  面板標題。**UI 操作元素（按鈕/輸入框/選單/標籤）維持現有無襯線堆疊**。
- 數字：統計、表格、監控 KPI 啟用 `font-variant-numeric: tabular-nums`。

### 元件語言

- 卡片：圓角 12px、`1px solid var(--border)`、淡陰影 `0 1px 3px rgba(16,24,40,.06)`；
  設為 Mantine `Card`/`Paper` 的 theme 預設（`components` defaultProps/styles）。
- 膠囊（pill）：市場標籤金調（`#faf3e3` 底 / `#8a5a0f` 字）、次要標籤灰調
  （`#f2f4f7` / `#475467`）；選中態深金底白字。
- Focus ring 金色；`defaultRadius` 維持 md，卡片/輸入框用 lg（12px）。
- 圖示：新增 **`@tabler/icons-react`**（Mantine 官方配套、tree-shakeable）。UI 不用 emoji。

## 全站骨架（PR1）

### 桌機（≥48em）：左側窄軌

`App.tsx` `RootLayout` 改為 `AppShell navbar={{ width: 60 }}`（移除 header）：

- 頂：襯線「廷」品牌 glyph（深金，link → /search）。
- 中：三個導覽項（NavLink，Tabler 圖示 + 10px label；active＝金底膠囊 + `aria-current`
  由 router 提供）：檢索 / 問答 / 監控。
- 底：頭像鈕 → Popover（帳號名（/api/stats `username`，沿用 queryKey `['stats']` 快取共享）
  + 登出）。登出維持**原生 form POST /logout**（伺服器 303 → /login，無需 JS）。

### 手機（<48em）：底部分頁列

- 左軌隱藏；底部 fixed 分頁列四格：檢索 / 問答 / 監控 / 帳號（第 4 格開登出選單）。
- `AppShell.Main` 預留底部 padding（safe-area-inset-bottom 相容）。
- 內容區無頂欄，垂直空間全給內容。

### 既有結構保留

- 全站唯一 h1（VisuallyHidden「廷豐智能研報」）保留在 main 內，標題梯級不變。
- Router 結構、lazy loading、Suspense fallback（改 Skeleton）不變。

## 各頁設計

### 檢索頁 /app/search（PR2）

- **搜尋列**：置中限寬（~560px）、尺寸加大、金色 focus ring；送出/清除行為不變。
- **篩選工具列**（取代 `FilterSidebar` 側欄，元件重構為工具列 + Popover）：
  - 一排市場膠囊快篩（含全語料篇數，來源 /api/stats；選中＝深金底白字），
    對應現有側欄市場 chip 行為（含 drill-in 分組聯動）。
  - 「更多篩選」Popover：商品類型、報告類型、個股/期貨布林、具體標的等現有條件全數搬入；
    作用中的進階條件以可刪除 chip 回顯在工具列上。
  - 右端：檢視切換（卡片/表格）+ 分組選單（group 檢視時）。
- **結果區**：全寬。卡片檢視改**兩欄網格**（<64em 一欄）：襯線卡標題、膠囊標籤、
  摘要 3 行截斷、底列「來源 · 日期｜查看全文」。表格/分組/市場索引/drill-in 檢視
  功能全保留、只換皮。
- 載入態：Skeleton 骨架屏（取代單一 Loader）；空/錯誤態沿用現有補救行為、換新語言。
- **狀態邏輯零變動**：`useSearchParamsState`、`useSearchResults`、URL 分享、
  localStorage 檢視記憶、query key 設計全部不動——純呈現層改造。

### 問答頁 /app/ask（PR3）

- **對話側欄**：功能不變（清單/新對話/刪除），`#fbfbfc` 底 + active 金調；
  新增**收合鈕**（收合狀態 localStorage 記憶；收合時訊息流拿到全寬）。
- **訊息流**：置中限寬（~760px）。使用者訊息＝右側金調氣泡；助理回答**去卡片框**，
  markdown 標題層級襯線化、內文行高 1.7、引用徽章 `[n]` 金色膠囊。
  沿用現有 XSS 安全 markdown 渲染管線（串流/重播兩路徑），只改樣式層。
- 來源摺疊區、「產生深度研報」入口、is_latest 徽章等對齊新語言。
- **深度研報面板**：進度步驟、KPI 卡（up/down 綠紅語意不變）、@mantine/charts 圖表、
  完成卡全部換皮（襯線標題 + 新卡片語言）；SSE 狀態機、latest-wins、
  turn.reports 持久化邏輯不碰。
- **輸入框**：底部浮動圓角卡片 + 陰影；範例問題改建議膠囊。
  IME 守衛（`e.nativeEvent.isComposing`）等行為邏輯不碰。

### 監控頁 /app/monitor（PR1，作為地基驗證頁）

- 結構不變：KPI 卡用新卡片語言、數字 tabular-nums 大字、區塊標題襯線、
  進度條金色、速率/ETA 顯示不變。

### 登入頁（後端 vanilla HTML，PR4）

- 純樣式改造：`--canvas` 畫布置中白卡、襯線品牌字、金色主按鈕、新輸入框語言。
- 表單行為、錯誤訊息、rate limit、auth 邏輯**零變動**；樣式維持內嵌
  （login 在 auth 牆外，不可連 SPA 資產——沿用既有慣例）。

## 邊界與相容（硬約束）

1. **功能平價**：檢視切換、分組、drill-in、載入更多、URL 分享、詳情 modal、
   關鍵字高亮、多輪問答、來源引用、深度研報全流程、監控速率、登出——
   重設計後全部可達且行為不變。後端 API 零變動。
2. **a11y 不退步**：focus trap、aria-label、鍵盤可達、對比 AA；底部分頁列
   `aria-current`；動畫尊重 `prefers-reduced-motion`。
3. **字型失敗安全**：`display=swap` 不阻塞渲染，CDN 不通時退系統襯線。
4. **既有陷阱不碰**：SSE 狀態機、latest-wins、IME 守衛、Zod `.nullish()`、
   資產 immutable 快取策略——只動樣式與版面層。

## 測試策略

- **單元**：現有前端測試全綠；版面重排導致的 DOM 斷言更新視為測試維護（逐一檢視）。
  新元件（左軌、底部分頁列、篩選 Popover、側欄收合）補測試。
- **E2E**：`:8098` 工作樹驗證法（預熱 BGE-M3；`ss` 核對埠避免誤殺正式 `:8097`）。
  桌機 + 手機視窗各跑導覽/檢索/問答冒煙；Playwright 截圖人工確認視覺。
- **每段 PR**：`npm run build` + eslint + vitest 綠才合併。

## 交付切分

| PR | 內容 | 驗收 |
|----|------|------|
| PR1 | 設計 token + 字型 + 左軌/底部分頁骨架 + 監控頁換皮 | 全站新骨架下三頁功能平價；監控頁展示新語言 |
| PR2 | 檢索頁：篩選工具列 + 兩欄卡片 + 各檢視換皮 | 檢索全功能平價 + e2e |
| PR3 | 問答頁：襯線內文 + 側欄收合 + 研報面板換皮 | 問答/研報全流程平價 + e2e |
| PR4 | 登入頁樣式 + 全站收尾（空/錯誤態、Skeleton 統一） | 登入平價、全站視覺一致 |

每段合併後即可部署（`npm run build` → `sudo systemctl restart report-mark-web.service`），
正式站漸進換裝、隨時可回退。

## 非範圍

- 深色模式（token 架構已預留，另案）。
- 深度研報 PDF（WeasyPrint）視覺對齊（後端，另案）。
- Phase 5 終局退役（basename flip、刪 web/static）與新功能頁。
