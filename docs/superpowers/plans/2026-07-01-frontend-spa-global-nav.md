# SPA 全站導覽 + cutover 落地整備 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。步驟用 `- [ ]` 追蹤。

**Goal:** 為 React SPA 補上全站頂部導覽（搜尋/問答/監控 + 品牌 + active 態）並改為共用 layout route，`/app` 落地改導向 `/app/search`；純加強 `/app`，不碰後端、不做 cutover。

**Architecture:** 新增 `AppNav` 呈現元件置於 `AppShell.Header`；`App.tsx` router 改為單一 `RootLayout`（`AppShell.Header` + `<Outlet/>`）包所有分頁；`/` 用 `Navigate` 導向 `/search`。

**Tech Stack:** React 19、react-router 8（`NavLink`/`Link`/`Navigate`/`Outlet`/`createBrowserRouter`，basename `/app`）、Mantine 9（`AppShell`/`Group`/`Text`）、Vitest 4 + Testing Library + `MemoryRouter`。

## Global Constraints

- **frontend-only**：不改 `web/server.py`/`app/**`/`db/**`；不做後端 `/`→`/app/search` redirect（延後）。
- **無 `dangerouslySetInnerHTML`**；不引入 Zustand/Redux；不新增相依。
- 品牌色用主題 gold（`c="gold.7"` = `--brand #ae7415`）；導覽文案：搖搜尋/問答/監控（正體）。
- `NavLink` active 由 router 自動加 `aria-current="page"`；品牌用 `Link`（不標 active）。
- 頁面自管寬度：`RootLayout` 不加 `Container`。
- commit 用 Conventional Commits + 正體中文 scope + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`；
  **只 stage 本任務檔案**（共用工作樹有他人 WIP：`AGENTS.md`、`markdown.tsx`、`markdown.test.tsx`、
  `check-a11y.cjs`——**絕不 stage/還原/觸碰**）。

---

### Task 1: `AppNav` 導覽元件

**Files:**
- Create: `frontend/src/components/AppNav.tsx`
- Test: `frontend/src/components/AppNav.test.tsx`

**Interfaces:**
- Produces: `export function AppNav(): JSX.Element` —— 無 props，於 `AppShell.Header` 內使用。

- [ ] **Step 1: 寫失敗測試** `frontend/src/components/AppNav.test.tsx`

```tsx
import { test, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { AppNav } from './AppNav'

const wrap = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <MantineProvider>
        <AppNav />
      </MantineProvider>
    </MemoryRouter>,
  )

test('渲染品牌與三個導覽連結（正確 href）', () => {
  wrap('/search')
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/monitor')
})

test('主導覽為具名 landmark', () => {
  wrap('/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
})

test('目前分路的連結帶 aria-current=page，其餘無', () => {
  wrap('/ask')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '搜尋' })).not.toHaveAttribute('aria-current', 'page')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/components/AppNav.test.tsx`
Expected: FAIL（`AppNav` 尚未存在）

- [ ] **Step 3: 實作** `frontend/src/components/AppNav.tsx`

```tsx
import { Group, Text } from '@mantine/core'
import { Link, NavLink } from 'react-router'

const LINKS: ReadonlyArray<{ to: string; label: string }> = [
  { to: '/search', label: '搜尋' },
  { to: '/ask', label: '問答' },
  { to: '/monitor', label: '監控' },
]

const noUnderline = { textDecoration: 'none' } as const

export function AppNav() {
  return (
    <Group h="100%" px="md" justify="space-between" wrap="nowrap">
      <Link to="/search" style={noUnderline}>
        <Text fw={700} c="gold.7" size="lg">
          廷豐智能研報
        </Text>
      </Link>
      <Group component="nav" aria-label="主導覽" gap="lg" wrap="nowrap">
        {LINKS.map(({ to, label }) => (
          <NavLink key={to} to={to} style={noUnderline}>
            {({ isActive }) => (
              <Text fw={isActive ? 700 : 500} c={isActive ? 'gold.7' : 'dimmed'}>
                {label}
              </Text>
            )}
          </NavLink>
        ))}
      </Group>
    </Group>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/components/AppNav.test.tsx`
Expected: PASS（3 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AppNav.tsx frontend/src/components/AppNav.test.tsx
git commit -m "feat(導覽): 新增 SPA 全站頂部導覽 AppNav（品牌 + 搜尋/問答/監控 + active 態）"
```

---

### Task 2: Router 改共用 layout route + `/app` 落地導向 `/app/search`

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`（新）

**Interfaces:**
- Consumes: `AppNav`（Task 1）。
- 行為：`/`（basename 下＝`/app`）→ `Navigate → /search`；所有分頁（monitor/search/ask/NotFound）
  皆渲染於含 `AppNav` 的 `RootLayout`。

- [ ] **Step 1: 寫失敗測試** `frontend/src/App.test.tsx`

```tsx
import { test, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { routes } from './App'

const renderAt = (path: string) => {
  const router = createMemoryRouter(routes, { initialEntries: [path], basename: '/app' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

test('/app 落地重導向到 /app/search（顯示導覽，非 stub）', async () => {
  renderAt('/')
  // 導覽恆在 → 品牌可見；且不再出現舊 stub 文案
  expect(await screen.findByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.queryByText('SPA 地基已就緒。')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/App.test.tsx`
Expected: FAIL（`routes` 尚未 export；或仍見 stub）

- [ ] **Step 3: 實作** `frontend/src/App.tsx`

改動要點（保留既有 lazy import 與 `RouteFallback`）：

1. 從 `react-router` 追加 import：`Navigate`、`Outlet`（`createBrowserRouter` 保留）。
2. 以 `RootLayout` 取代 `Layout`/`Home`：

```tsx
function RootLayout() {
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <AppNav />
      </AppShell.Header>
      <AppShell.Main>
        <VisuallyHidden component="h1">廷豐智能研報</VisuallyHidden>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  )
}

function NotFound() {
  return <Title order={3}>找不到頁面</Title>
}
```

3. **Export `routes`**（供測試用 `createMemoryRouter`），router 由其建立：

```tsx
export const routes = [
  {
    element: <RootLayout />,
    children: [
      { path: '/', element: <Navigate to="/search" replace /> },
      { path: '/monitor', element: <Suspense fallback={<RouteFallback />}><MonitorPage /></Suspense> },
      { path: '/search', element: <Suspense fallback={<RouteFallback />}><SearchPage /></Suspense> },
      { path: '/ask', element: <Suspense fallback={<RouteFallback />}><AskPage /></Suspense> },
      { path: '*', element: <NotFound /> },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })
```

4. 移除舊 `Home` 與舊 `Layout`（其 `Container` 內容併入 `RootLayout`，但**不含 `Container`**）；
   `import` 清掉不再用的 `Container`（若 lint 報未使用）。保留 `App` 預設匯出 `<RouterProvider router={router} />`。

- [ ] **Step 4: 跑測試確認通過 + 型別/lint/build**

```
npx vitest run src/App.test.tsx src/components/AppNav.test.tsx
npx tsc --noEmit
npx eslint src/App.tsx src/components/AppNav.tsx
npm run build
```
Expected: 測試 PASS、tsc 0 error、eslint 0、build 成功。

- [ ] **Step 5: 跑既有相關測試確認未回歸**

Run: `npx vitest run src/features/search src/features/ask src/features/monitor`
Expected: 全綠（分頁被包進 AppShell 後單元測試不受影響——分頁測試各自 render，不經 App router）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(導覽): App 改共用 layout route 掛載 AppNav，/app 落地導向 /app/search"
```

## Self-Review 註記

- 規格涵蓋：AppNav（Task 1）、layout route + `/` 落地（Task 2）皆有任務。
- 型別一致：`AppNav` 無 props；`routes` export 供測試；`Navigate`/`Outlet` 來自 `react-router`。
- **整合風險**（交 reviewer + live 驗證）：`AskPage` 全高 CSS 版面包進 `AppShell.Main` 可能需調
  `padding`；此為 live 平價項，非單元測試可涵蓋——實作若發現 build/型別問題以外的破版，記於報告。
