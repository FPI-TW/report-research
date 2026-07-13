# 問答頁「檢索／問答」切換鈕 ＋ 共享元素轉場 — 設計文件

- 日期：2026-07-13
- 狀態：設計已核准，待寫實作計畫
- 分支基準：`origin/main`（改版 1a 墨青×鎏金，`47fa724`）
- 範圍：純前端（React SPA `frontend/`）。後端、資料流、API 契約零改動。

## 1. 背景與現況

改版 1a 在檢索頁 hero 放了一組「檢索研報／智能問答」分段控制（segmented control），但**只存在於檢索頁、且只在 hero 態（空查詢＋無篩選）顯示**。問答頁沒有對應的切換入口。

現況關鍵事實：

- **切換鈕來源**：`frontend/src/features/search/SearchPage.tsx` 內 inline 的 `heroModes` 區塊——
  - `檢索研報`：`<span class="heroMode heroModeActive" aria-current="page">`（白底 thumb）
  - `智能問答`：`<Link to="/ask" class="heroMode">`
  - 樣式在 `SearchPage.module.css`：`.heroModes`（`background:#eceae3; border-radius:12px; padding:3px`）、`.heroMode`、`.heroModeActive`（`background:var(--tf-surface); box-shadow:var(--tf-shadow-seg)`）。
- **問答頁**：`frontend/src/features/ask/AskPage.tsx`
  - 空狀態（`turns.length === 0`）→ `AskEmptyState`（BrandLogo＋「向廷豐智能體提問」＋sub＋置中 Composer），**無切換鈕**。
  - 有對話 → `.flow` 訊息流 ＋ 底部 `.composerBar`。
- **換頁動效**：`AppShell.tsx` 把 `<Outlet/>` 包在 `<div key={pathname} class={styles.routeReveal}>`；`.routeReveal { animation: tf-fade var(--tf-dur-2) var(--tf-ease-out) both }`（opacity 0→1，180ms，刻意只做透明度、不加位移，`key=pathname` 讓換頁重播、同頁改 query 不重播）。
- **導覽**：左軌 `SideRail`（桌機）與 `MobileTabBar`（手機）已有 檢索／問答／監控 三項 `NavItem`。
- **技術棧**：React 19.2.7、**react-router 8.0.1**、vite 8、vitest 4。
- **動效 token**（`styles/tokens.css`）：`--tf-ease-out: cubic-bezier(0.22,1,0.36,1)`、`--tf-dur-1..4`（120/180/240/340ms）；全域 `@media (prefers-reduced-motion: reduce)` 已把 animation/transition 歸零；`.tf-reveal`＝`tf-up`（淡入＋上浮 6px，封頂 stagger）。
- react-router 8.0.1 已確認提供：`<Link viewTransition>` / `<NavLink viewTransition>`（boolean prop，觸發 `document.startViewTransition`）與 `useViewTransitionState(to)` hook。不支援 View Transitions API 的瀏覽器自動回退為即時導覽。

## 2. 目標與非目標

**目標**

1. 在問答頁常駐顯示「檢索研報／智能問答」切換鈕——空狀態與對話進行中皆可見。
2. 兩頁切換時做**共享元素轉場**：active 白 thumb 在兩頁之間滑動／變形（iOS 分段控制觀感），其餘內容 crossfade。
3. 「完整銜接不卡頓」：避免 lazy 路由 Suspense 造成的空白閃爍、避免與既有 `.routeReveal` 疊加成雙淡入。

**非目標**

- 不改後端、API、資料流、qa_log、SSE。
- 不改左軌／手機列導覽的行為（去監控頁維持即時換頁）。
- **檢索頁維持 hero-only**：打了查詢字仍收起切換鈕，不在檢索頁加常駐列。兩頁略不對稱是刻意取捨（使用者選定「問答頁持續顯示」）。

## 3. 已核准的決策

| 決策 | 選定 |
|------|------|
| 切換鈕在問答頁的位置 | **持續顯示（含對話中）** |
| 轉場風格 | **共享元素：active pill 滑動（View Transitions API）**，含 fallback 與 reduced-motion |
| 檢索頁 | 維持 hero-only（不加常駐列） |
| 對話進行中切換列位置 | 問答欄頂端置中、在捲動流之外 |

## 4. 元件設計

### 4.1 新增共用元件 `ModeSwitch`

`frontend/src/components/shell/ModeSwitch.tsx` ＋ `ModeSwitch.module.css`。

職責：呈現「檢索研報／智能問答」分段控制，自行判定 active、掛上 View Transition 名稱與 `viewTransition` 連結、負責對向路由預載。

介面（props）：

```ts
interface ModeSwitchProps {
  /** 視覺尺寸；hero 用較大、對話列用較緊湊。預設 'md' */
  size?: 'md' | 'sm'
  className?: string
}
```

- active 判定：`const { pathname } = useLocation()`；`pathname.startsWith('/ask')` → 問答 active，否則檢索 active。（basename `/app` 由 router 處理，`useLocation().pathname` 已是去 basename 後的 `/search`、`/ask`。）
- 渲染：
  - active 項 → `<span className={active} aria-current="page">`，掛 `view-transition-name: tf-mode-thumb`（見 §5）。
  - 非 active 項 → `<Link to={to} viewTransition onPointerEnter={preload} onFocus={preload}>`。
  - container `<div>` 掛 `view-transition-name: tf-mode-switch`。
- 兩項固定文案與圖示：`檢索研報`（`Icon name="search"`）、`智能問答`（`Icon name="messages"`），對齊現有 hero。

### 4.2 三處接入

1. **檢索頁 hero**（`SearchPage.tsx`）：把 inline `heroModes` 換成 `<ModeSwitch />`。`SearchPage.module.css` 移除 `.heroModes/.heroMode/.heroModeActive`（改由 ModeSwitch 擁有），保留 hero 版面其餘樣式。位置、外觀維持不變。
2. **問答頁空狀態**（`AskEmptyState.tsx`）：在 sub 文字與 Composer 之間插入 `<ModeSwitch />`（鏡像檢索 hero 的位置關係）。
3. **問答頁對話進行中**（`AskPage.tsx`）：在 `.column` 頂端、`.flow` 之上加一條 `.modeBar`（置中、不隨 `.flow` 捲動）放 `<ModeSwitch size="sm" />`。僅在 `turns.length > 0` 顯示（空狀態已由 AskEmptyState 內的 ModeSwitch 負責，避免重複）。

> 注意：問答頁的 ModeSwitch 在「空狀態」與「對話中」是兩個不同 DOM 位置（hero 內／頂端列）。同頁內兩者不會同時存在（互斥於 `turns.length`），故 `view-transition-name` 在該頁仍唯一。送出第一則訊息時由空狀態切到對話態，屬同頁 re-render；此切換不啟用 VT（無 `viewTransition` 導覽），走既有 `.tf-reveal`／即時，不在本案動畫範圍。

## 5. 共享元素轉場機制

### 5.1 命名與觸發

- **靜態命名**（非條件式）：因每頁只渲染一個 ModeSwitch，`view-transition-name` 天然唯一，採靜態即可，毋須 `useViewTransitionState` 動態掛名。
  - active pill：`view-transition-name: tf-mode-thumb`
  - container：`view-transition-name: tf-mode-switch`
- **觸發**：ModeSwitch 內非 active 的 `<Link>` 帶 `viewTransition`。點擊 → react-router 以 `document.startViewTransition` 包住 DOM 交換。
- **效果**：瀏覽器擷取舊頁（thumb 在「檢索」、container 在 hero）與新頁（thumb 在「問答」、container 在頂端列或 hero）兩快照，morph：白 thumb 左→右滑動、container 位移到新位置；頁面其餘走預設 `::view-transition-group(root)` crossfade。

### 5.2 只在 ModeSwitch 觸發

只有帶 `viewTransition` 的 ModeSwitch 連結會啟動 VT。左軌／手機列 `NavItem`、hero 「試試」chip、SearchBar 送出等一律不帶 `viewTransition`，維持即時換頁。因此 thumb morph 只發生在刻意的檢索↔問答切換。

### 5.3 與 `.routeReveal` 調和

`.routeReveal` 的 `tf-fade`（整頁 opacity 淡入）與 VT root crossfade 會重疊成雙淡入。處理：

- 讓 VT 瀏覽器由 root crossfade 主導頁面淡入；`.routeReveal` 退為「不支援 VT 時」的既有行為。
- 實作手法（擇一，實測後定）：
  - (a) 在 `document.startViewTransition` 期間於 `:root` 標記狀態，CSS 讓 `.routeReveal` 於該期間 `animation: none`；或
  - (b) 直接讓 `::view-transition-old(root)/new(root)` 的 crossfade 取代 `.routeReveal` 觀感，若雙淡入實測不明顯即保留現狀。
- 驗收以「切換當下無明顯雙淡入、無閃爍」為準（§8）。

## 6. 不卡頓：Suspense 與 fallback

- **對向路由預載**：路由為 `lazy()`＋`<Suspense>`；若目標 chunk 未載入，VT 會擷取 Suspense fallback → 空白閃爍。對策：
  - 在 ModeSwitch 非 active 連結 `onPointerEnter`／`onFocus` 觸發對向頁的 `import()` 預載。
  - 於 `App.tsx` 提供集中式路由預載小工具（同一組 `import` thunk 供 lazy 與預載共用，避免重複字串路徑），並在 app 掛載後 `requestIdleCallback`（不可用則 `setTimeout`）預載 `/search`、`/ask` 兩頁。
- **Suspense fallback**：維持最小且穩定（不要大版面跳動）。預載到位後正常情況不會觸發 fallback。
- **Fallback（無 VT 瀏覽器）**：`document.startViewTransition` 不存在時 react-router 自動即時換頁，切換鈕照常運作，只是無 morph。

## 7. reduced-motion 與可及性

- 既有全域 `@media (prefers-reduced-motion: reduce)` 已歸零一般 animation／transition。
- 另加（全域 `styles/view-transitions.css`）：

  ```css
  @media (prefers-reduced-motion: reduce) {
    ::view-transition-group(*),
    ::view-transition-old(*),
    ::view-transition-new(*) { animation: none !important; }
  }
  ```

  使 VT 於偏好減少動態時變即時切換。
- active 項維持 `aria-current="page"`；非 active 為真正 `<Link>`（鍵盤可達、可 middle-click 開新分頁、可預覽 href）。
- 分段控制的對比沿用現有墨青 thumb 樣式（已於 1a 校過）。

## 8. 驗證計畫

- `pnpm/npm test`（vitest）全綠，含新增測試。
- `tsc --noEmit` 通過、`vite build` 成功。
- 以 Playwright 或 dev server 實測（VT 需 Chromium 系）：
  1. 檢索 hero → 點「智能問答」→ 觀察白 thumb 滑到右、container 位移到問答頁，無空白閃爍、無雙淡入。
  2. 問答空狀態 → 點「檢索研報」→ 反向 morph。
  3. 問答對話中（有 turns）→ 頂端列可見切換鈕，點擊可回檢索。
  4. `prefers-reduced-motion: reduce` 下切換為即時、無動畫。
  5. 首次點擊（chunk 未預載情境）仍不空白（驗證預載）。

## 9. 測試策略（vitest / jsdom）

- 新增 `components/shell/ModeSwitch.test.tsx`：
  - 兩標籤「檢索研報」「智能問答」都在。
  - `MemoryRouter initialEntries={['/search']}` → 檢索為 active（`aria-current="page"`）、問答為連向 `/ask` 的 link。
  - `initialEntries={['/ask']}` → 反之。
- 更新 `features/ask/AskEmptyState.test.tsx`：空狀態渲染出切換鈕（查得到「檢索研報」link/問答 active）。
- 更新 `features/ask/AskPage.test.tsx`：對話進行中（mock `turns.length > 0`）頂端列出現切換鈕。
- 既有 `features/search/SearchPage.test.tsx`：文案 `檢索研報／智能問答` 不變，預期照過；實跑確認抽元件未破壞既有查詢。
- jsdom 無 `document.startViewTransition`，測試不驗動畫本身，只驗 DOM／連結／active 態。react-router 對缺 API 會 feature-detect，測試不因 VT 崩。

## 10. 檔案清單

**新增**
- `frontend/src/components/shell/ModeSwitch.tsx`
- `frontend/src/components/shell/ModeSwitch.module.css`
- `frontend/src/components/shell/ModeSwitch.test.tsx`
- `frontend/src/styles/view-transitions.css`（VT reduced-motion；於 `main.tsx` import）

**編輯**
- `frontend/src/features/search/SearchPage.tsx`（改用 `<ModeSwitch/>`）
- `frontend/src/features/search/SearchPage.module.css`（移除 `heroModes` 系列樣式）
- `frontend/src/features/ask/AskEmptyState.tsx`（＋`<ModeSwitch/>`）
- `frontend/src/features/ask/AskEmptyState.module.css`（版位微調）
- `frontend/src/features/ask/AskPage.tsx`（＋對話態頂端 `.modeBar`）
- `frontend/src/features/ask/AskPage.module.css`（`.modeBar` 樣式）
- `frontend/src/App.tsx`（路由預載小工具＋閒置預載；lazy import thunk 共用）
- 視實測需要：`frontend/src/components/shell/AppShell.module.css`（`.routeReveal` 調和）

## 11. 風險與緩解

| 風險 | 緩解 |
|------|------|
| Suspense 空白閃爍 | hover/focus＋閒置預載對向 chunk（§6） |
| 與 `.routeReveal` 雙淡入 | 讓 VT root crossfade 主導、`.routeReveal` 退 fallback；實測定案（§5.3） |
| `view-transition-name` 撞名 | 每頁僅一個 ModeSwitch，靜態命名天然唯一；監控頁無 ModeSwitch 亦不觸發 VT |
| 舊瀏覽器無 VT | react-router 自動即時換頁，功能不受影響（§6） |
| 抽元件破壞既有檢索 hero 測試 | 文案／結構對齊原樣，實跑 `SearchPage.test.tsx` 確認（§9） |

## 12. 開放問題

- `.routeReveal` 調和最終手法（(a) 或 (b)）待實測觀感後定，屬實作細節，不影響本設計成立。
