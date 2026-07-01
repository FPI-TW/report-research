# SPA 全站導覽 + cutover 落地整備 設計（Phase 2c/3c/4 cutover 前置）

**日期**：2026-07-01
**分支**：`feat/frontend-spa-global-nav`（off `origin/main` 199a887，含已合併 Phase 4 #45 + backend fix #46）

## 背景與問題

React SPA 已把 vanilla `index.html`（搜尋＋問答＋研報同頁、以 mode toggle 切換）拆成獨立分路
`/app/search`、`/app/ask`、`/app/monitor`。但拆分後：

1. **`/app` `Home` 是佔位 stub**（「SPA 地基已就緒」），非真實落地頁。
2. **SPA 完全沒有全站導覽**——`App.tsx` 的 `AppShell` 只有 `Main`（無 Header/Navbar）；
   `SearchPage`/`AskPage`/`MonitorPage` 彼此無連結。vanilla 的搜尋↔問答切換能力遺失。

因此若直接做 cutover（`/` → `/app/search`）會**把使用者困在單頁**，無法到達問答/監控。
本設計補齊此平價缺口，是 cutover 的**前置**。

## 範圍（本 PR）與非範圍（延後）

- **本 PR（frontend-only、不部署、不碰後端）**：
  - 新增全站頂部導覽 `AppNav`。
  - Router 改為**共用 layout route**（`RootLayout` 以 `AppShell.Header` + `<Outlet/>` 包所有分頁）。
  - `/app` 落地由 stub 改為 `Navigate → /app/search`。
  - 純加強 `/app`；**不改變 `/` 目前服務 vanilla `index.html` 的行為**（非 cutover）。
- **延後（cutover 本體，待使用者確認 #47/#48 合併 + main 部署到 :8097 後）**：
  - 後端 `web/server.py`：`/` → `307` → `/app/search`（一行，仿既有 `/monitor` → `/app/monitor`）。
- **非本設計（Phase 5 終局退役）**：切 basename `/app`→`/`、刪 `web/static`、退役 vanilla。

## 使用者拍板（2026-07-01）

- 導覽含 **搜尋 / 問答 / 監控** 三頁。
- `/` cutover 落地目標 = **`/app/search`**（解 REFACTOR_TODO 內部 2c→search／3c→ask 矛盾）。
- 現在就開始（frontend-only；與並行 session 的 markdown/AGENTS.md WIP 不同檔，避開）。

## 架構與元件

### `frontend/src/components/AppNav.tsx`（新）

頂部導覽內容，渲染於 `AppShell.Header` 內。純呈現、無資料相依。

- 版面：`<Group h="100%" px="md" justify="space-between" wrap="nowrap">`。
- **品牌（左）**：`react-router` `Link`（非 NavLink，避免品牌被標 `aria-current`）`to="/search"`，
  內含 `<Text fw={700} c="gold.7" size="lg">廷豐智能研報</Text>`，`Link` 去底線。
- **導覽（右）**：`<Group component="nav" aria-label="主導覽" gap="lg" wrap="nowrap">`，
  三個 `react-router` `NavLink`（`to` = `/search`、`/ask`、`/monitor`；label = 搜尋、問答、監控）。
  - 每個 `NavLink` 去底線，用 **function-children** 取 `isActive`：
    `{({ isActive }) => <Text fw={isActive ? 700 : 500} c={isActive ? 'gold.7' : 'dimmed'}>{label}</Text>}`
  - `NavLink` 於 active 時**自動加 `aria-current="page"`** → a11y 由 router 提供，不需手動。
- 品牌色用主題 gold（`c="gold.7"` = `--brand #ae7415`）；未 active 用 `dimmed`。

### `frontend/src/App.tsx`（改）

改為共用 layout route，讓導覽出現在所有分頁：

```tsx
function RootLayout() {
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <AppNav />
      </AppShell.Header>
      <AppShell.Main>
        {/* 全站唯一 h1（視覺隱藏）：維持每頁 h1→h2 標題梯級 */}
        <VisuallyHidden component="h1">廷豐智能研報</VisuallyHidden>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}

const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/', element: <Navigate to="/search" replace /> },
        { path: '/monitor', element: <Suspense fallback={<RouteFallback />}><MonitorPage /></Suspense> },
        { path: '/search',  element: <Suspense fallback={<RouteFallback />}><SearchPage /></Suspense> },
        { path: '/ask',     element: <Suspense fallback={<RouteFallback />}><AskPage /></Suspense> },
        { path: '*', element: <NotFound /> },
      ],
    },
  ],
  { basename: '/app' },
)
```

- 移除 `Home` stub 元件；`/` 改 `Navigate to="/search" replace`。
- `NotFound` 併入 layout children（沿用 `Layout` 內容或改用 `RootLayout` 的 Outlet 呈現「找不到頁面」）。
- **頁面自管寬度**：`RootLayout` 不強加 `Container`（現行分頁本就無 Container，強加 `size="lg"`
  會壓窄含側欄的檢索頁），只提供 `AppShell.Main`（`padding="md"` + header 高度自動 offset）。

### 整合風險（實作＋ live 驗證需確認）

- `AskPage` 用 `AskPage.module.css`（側欄＋聊天全高版面）。包進 `AppShell.Main` 後需確認高度/捲動
  不破版；必要時把 `AppShell` 的 `padding` 調 `0` 或於頁面 CSS 微調。
- `padding="md"` 對三頁的間距是新增；以 :8098 live 對照 vanilla 視覺平價，偏差則調整。

## 測試

### `frontend/src/components/AppNav.test.tsx`（新，`MemoryRouter` + `MantineProvider`）

- 渲染品牌文字「廷豐智能研報」與三個導覽連結（搜尋/問答/監控）。
- 連結 `href` 正確（`MemoryRouter` 無 basename → `/search`、`/ask`、`/monitor`）。
- active 態：`initialEntries={['/ask']}` 時「問答」連結帶 `aria-current="page"`，其餘無。
- a11y：存在 `role="navigation"` 且 `aria-label="主導覽"` 的 landmark。

### `frontend/src/App.test.tsx`（新或補；用 `createMemoryRouter` + `RouterProvider`）

- `/`（basename 下＝`/app`）重導向到 `/search`（渲染出 SearchPage 標誌元素或至少不再是 stub）。
- 任一分路（如 `/search`）皆可見全站導覽（品牌 + 三連結）。

*註*：涉及 lazy 分頁的路由測試可用 `createMemoryRouter([...], { initialEntries, basename })` +
`RouterProvider`，並 `await` Suspense 落地；若分頁載入在 jsdom 太重，改以「`/` 重導向後 location 為
`/search`」的輕量斷言為主，分頁內容渲染交由既有分頁測試與 e2e。

### e2e（`frontend/e2e/nav.spec.mjs`，可選、live）

- 於 `/app/search` 見導覽；點「問答」→ URL 到 `/app/ask` 且該連結 active；點「監控」→ `/app/monitor`。
- 交由 cutover 前的 :8098 live 平價一併驗。

## 交付後

- vitest（新增 + 既有全綠）、`tsc --noEmit`、`eslint`、`npm run build` 全綠。
- :8098 live 平價（三頁在 AppShell 內渲染正常、0 console error、導覽切換正確）。
- 開 PR（frontend-only）。**後端 `/`→`/app/search` redirect 為延後一行**，待使用者部署後另行落地。
