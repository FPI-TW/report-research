# 整站視覺重設計 PR1（地基：token + 字型 + 左軌/底部分頁骨架 + 監控換皮）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立整站重設計的設計系統地基（色彩 token、Noto Serif TC 襯線字型、卡片語言）並把全站骨架換成桌機左側窄軌 + 手機底部分頁列，以監控頁作為新語言的首個驗證頁。

**Architecture:** 純前端呈現層改造（Spec：`docs/superpowers/specs/2026-07-02-frontend-redesign-design.md`）。token 走 CSS variables（`tokens.css`）+ Mantine theme（`theme.ts`）雙層：CSS 變數為單一事實來源，theme 引用 `var(--tf-*)`。導覽由 `AppNav`（頂欄）換成 `AppRail`（桌機左軌）與 `MobileTabBar`（手機底欄），兩者共用 `NAV_LINKS` 與 `AccountMenu`。後端與業務邏輯零變動。

**Tech Stack:** React 19.2.7 + TypeScript、Mantine 9.4.1、react-router 8（從 `'react-router'` import）、@tanstack/react-query 5、vitest 4 + @testing-library/react（jsdom）、CSS Modules、新增依賴 `@tabler/icons-react`。

## Global Constraints

- 分支：`feat/frontend-redesign-foundation`（已存在，off origin/main，spec 已提交於其上）。
- 前端指令一律在 `/mnt/c/Users/User/Desktop/Project/report-mark/frontend` 下執行；Node >= 22.22.0。
- **共用工作樹**：他人有未提交 WIP（`AGENTS.md`、`frontend/src/features/ask/lib/markdown.*`）。`git add` 只准列明確檔案路徑，**嚴禁 `git add -A` / `git add .`**；每次 commit 前 `git diff --staged --stat` 核對範圍。
- UI 文案繁體中文、**不用 emoji**；導覽文案沿用現有「搜尋／問答／監控」（平價，不改名「檢索」）。
- 金色文字一律深金 `#8a5a0f`（`--tf-gold-text`）；`#ae7415` 只用於大字與裝飾。
- 手機斷點統一 `(max-width: 48em)`（=768px；取代 AppNav 舊的 40em/23em 二階）。
- 測試：vitest（`globals: true`，直接用全域 `test`/`expect`）；jsdom 無 `window.matchMedia` → 桌機為預設態，手機態用 `vi.stubGlobal('matchMedia', ...)`（沿用 `mockMatchMedia` 模式）。
- **RTK 陷阱**：若 `npm test` 經 rtk 包裝遮蔽非零 exit code，改跑 `rtk proxy npm test`（或 `rtk proxy npx vitest run ...`）核對真實結果。
- WSL drvfs 慢：`npm install`/`npm run build` 可能要數分鐘，Bash 呼叫給足 timeout（≥300000ms）。
- 功能平價硬約束：登出必須維持**原生 form POST /logout**；`data-testid="account-avatar"`、nav landmark `aria-label="主導覽"`、NavLink 的 `aria-current="page"` 契約全部保留。

---

### Task 1: 設計 token 與襯線字型（tokens.css + index.html + theme.ts）

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/index.html`（head 加字型 link）
- Modify: `frontend/src/theme.ts`
- Modify: `frontend/src/main.tsx`（import tokens.css）
- Test: `frontend/src/theme.test.ts`

**Interfaces:**
- Consumes: 無（地基任務）。
- Produces: CSS 變數 `--tf-canvas/--tf-surface/--tf-border/--tf-border-weak/--tf-text-1..4/--tf-gold-text/--tf-gold-tint/--tf-serif`；工具 class `tf-tabular-nums`；theme `headings.fontFamily = 'var(--tf-serif)'`；Card 預設 `radius 12 / withBorder / shadow 'xs'`。後續所有任務的 CSS 只准引用 `--tf-*` 變數，不准硬編碼色值。

- [ ] **Step 1: 在 theme.test.ts 追加失敗測試**

在 `frontend/src/theme.test.ts` 現有測試後追加：

```ts
test('headings 用襯線字型 token（var(--tf-serif)）', () => {
  expect(theme.headings?.fontFamily).toBe('var(--tf-serif)')
  expect(theme.headings?.fontWeight).toBe('700')
})

test('Card 預設帶新卡片語言（12px 圓角、邊框、淡陰影）', () => {
  expect(theme.components?.Card?.defaultProps).toMatchObject({
    radius: 12,
    withBorder: true,
    shadow: 'xs',
  })
})

test('陰影 xs 為設計系統的淡雙層卡片陰影', () => {
  expect(theme.shadows?.xs).toBe('0 1px 3px rgba(16, 24, 40, 0.06)')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/theme.test.ts`
Expected: 新增 3 個測試 FAIL（`headings` 為 undefined 等），原有 1 個 PASS。

- [ ] **Step 3: 建立 tokens.css**

Create `frontend/src/styles/tokens.css`：

```css
/* 設計 token（Spec 2026-07-02）：介面基調單一事實來源。
   深色模式日後在 [data-mantine-color-scheme='dark'] 覆寫同名變數即可。 */
:root {
  --tf-canvas: #f6f7f9; /* 頁面畫布底 */
  --tf-surface: #ffffff; /* 卡片面 */
  --tf-border: #e4e7ec;
  --tf-border-weak: #f2f4f7;
  --tf-text-1: #101828; /* 主標 */
  --tf-text-2: #344054; /* 內文 */
  --tf-text-3: #667085; /* 次要 */
  --tf-text-4: #98a2b3; /* 弱化 */
  --tf-gold-text: #8a5a0f; /* 金色文字一律用深金（白底 AA） */
  --tf-gold-tint: #faf3e3; /* 金色淡底（膠囊/選中態） */
  --tf-serif: 'Noto Serif TC', Georgia, 'Times New Roman', serif;
}

body {
  background-color: var(--tf-canvas);
}

/* 數字等寬：統計、表格、監控 KPI */
.tf-tabular-nums {
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 4: index.html 加 Noto Serif TC（CDN + swap 不阻塞）**

在 `frontend/index.html` 的 `<title>廷豐智能研報</title>` 之後、`</head>` 之前插入：

```html
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;700&display=swap"
      rel="stylesheet"
    />
```

- [ ] **Step 5: 改寫 theme.ts**

`frontend/src/theme.ts` 全檔改為：

```ts
import { Card, createTheme, Paper, type MantineColorsTuple } from '@mantine/core'

// 由品牌金 #ae7415（tokens.css --brand）衍生的 10 階色票；index 7 = 品牌主色。
const gold: MantineColorsTuple = [
  '#fbf3e3',
  '#f3e4c6',
  '#e7c98c',
  '#dbaf52',
  '#d09a26',
  '#c98e10',
  '#c58808',
  '#ae7415', // 品牌主色（--brand）
  '#9c6710',
  '#8a5a0f', // --brand-strong
]

export const theme = createTheme({
  colors: { gold },
  primaryColor: 'gold',
  primaryShade: { light: 7, dark: 6 },
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
  // 襯線編輯感：標題層級一律 Noto Serif TC（token 定義在 styles/tokens.css）
  headings: { fontFamily: 'var(--tf-serif)', fontWeight: '700' },
  defaultRadius: 'md',
  // 設計系統淡卡片陰影（覆寫 xs 一階，其餘沿用 Mantine 預設）
  shadows: { xs: '0 1px 3px rgba(16, 24, 40, 0.06)' },
  components: {
    Card: Card.extend({
      defaultProps: { radius: 12, withBorder: true, shadow: 'xs' },
    }),
    Paper: Paper.extend({
      defaultProps: { radius: 12 },
    }),
  },
})
```

- [ ] **Step 6: main.tsx import tokens.css**

在 `frontend/src/main.tsx` 的 `import '@mantine/charts/styles.css'` 之後加：

```ts
import './styles/tokens.css'
```

- [ ] **Step 7: 跑測試確認通過**

Run: `npx vitest run src/theme.test.ts`
Expected: 4 tests PASS。

- [ ] **Step 8: 全量單元測試（Card 預設變更可能波及快照式斷言）**

Run: `npx vitest run`
Expected: 全綠。若有測試因 Card 新預設（withBorder/shadow/radius）失敗，逐一檢視：斷言舊視覺細節的更新為新值；斷言行為/文案的不應失敗。

- [ ] **Step 9: Commit**

```bash
git add src/styles/tokens.css index.html src/theme.ts src/main.tsx src/theme.test.ts
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(設計系統): 設計 token 與襯線字型地基

tokens.css 定義 --tf-* CSS 變數（畫布/卡面/邊框/文字四階/金色文字與
淡底/襯線堆疊）與 tf-tabular-nums 工具 class；index.html 以 CDN +
display=swap 載入 Noto Serif TC 600/700；theme 標題層級改襯線、
Card/Paper 預設換新卡片語言（12px 圓角+邊框+淡陰影）。
深色模式日後覆寫同名變數即可，本次不做。
EOF
)"
```

---

### Task 2: AccountMenu 共用帳號選單

**Files:**
- Create: `frontend/src/components/AccountMenu.tsx`
- Test: `frontend/src/components/AccountMenu.test.tsx`

**Interfaces:**
- Consumes: `getStats`（`../features/search/api`，queryKey `['stats']` 快取共享）、`avatarUrl`（`../assets/avatar.jpg`）。
- Produces: `export function AccountMenu(props: { position?: 'right-end' | 'top-end' })` — 頭像鈕（`aria-label="帳號選單"`、`data-testid="account-avatar"`）點開 Mantine Menu：帳號名 + 登出（原生 form POST `/logout`）。Task 3/4 直接 `<AccountMenu position="right-end" />`（左軌）與 `<AccountMenu position="top-end" />`（底欄）。

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/components/AccountMenu.test.tsx`：

```tsx
import { test, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AccountMenu } from './AccountMenu'

// AccountMenu 讀 /api/stats.username；mock 只需回 username。
vi.mock('../features/search/api', () => ({
  getStats: vi.fn(async () => ({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '研究員',
  })),
}))

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <AccountMenu />
      </MantineProvider>
    </QueryClientProvider>,
  )
}

test('頭像鈕存在（data-testid 平價保留）', () => {
  wrap()
  expect(screen.getByTestId('account-avatar').getAttribute('src')).toMatch(/avatar\.jpg$/)
  expect(screen.getByRole('button', { name: '帳號選單' })).toBeInTheDocument()
})

test('點開選單：帳號名 + 登出（原生 form POST /logout）', async () => {
  wrap()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('研究員')).toBeInTheDocument()
  const logout = screen.getByRole('menuitem', { name: '登出' })
  expect(logout).toHaveAttribute('type', 'submit')
  const form = logout.closest('form')
  expect(form).toHaveAttribute('action', '/logout')
  expect(form).toHaveAttribute('method', 'post')
})

test('username 空白時顯示預設「使用者」', async () => {
  const api = await import('../features/search/api')
  vi.mocked(api.getStats).mockResolvedValueOnce({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '  ',
  } as never)
  wrap()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('使用者')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/components/AccountMenu.test.tsx`
Expected: FAIL（模組不存在）。

- [ ] **Step 3: 實作 AccountMenu.tsx**

Create `frontend/src/components/AccountMenu.tsx`：

```tsx
import { Menu, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getStats } from '../features/search/api'
import avatarUrl from '../assets/avatar.jpg'

/** 帳號選單：頭像鈕 → 帳號名 + 登出。
 *  登出維持原生 form POST /logout（伺服器 303 → /login），無需 JS。 */
export function AccountMenu({ position = 'right-end' }: { position?: 'right-end' | 'top-end' }) {
  // 與 SearchPage/AppRail 共用 queryKey ['stats'] → 快取共享，僅取 username。
  const { data } = useQuery({ queryKey: ['stats'], queryFn: getStats, staleTime: 5 * 60_000 })
  const name = (data?.username || '').trim() || '使用者'
  return (
    <Menu position={position} withArrow transitionProps={{ duration: 0 }}>
      <Menu.Target>
        <UnstyledButton aria-label="帳號選單" style={{ display: 'block', lineHeight: 0 }}>
          <img
            data-testid="account-avatar"
            src={avatarUrl}
            alt=""
            width={30}
            height={30}
            style={{ borderRadius: '50%', display: 'block' }}
          />
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label title="目前登入帳號">{name}</Menu.Label>
        <form method="post" action="/logout" style={{ margin: 0 }}>
          <Menu.Item component="button" type="submit">
            登出
          </Menu.Item>
        </form>
      </Menu.Dropdown>
    </Menu>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/components/AccountMenu.test.tsx`
Expected: 3 tests PASS。若 `getByRole('menuitem')` 抓不到，改斷言 `getByRole('button', { name: '登出' })`（Mantine Menu.Item 的 role 以實際渲染為準，兩者擇一穩定即可）。

- [ ] **Step 5: Commit**

```bash
git add src/components/AccountMenu.tsx src/components/AccountMenu.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(導覽): 共用帳號選單 AccountMenu

頭像鈕點開 Menu 顯示帳號名（/api/stats.username，快取共享
queryKey ['stats']）與登出；登出維持原生 form POST /logout 平價。
供桌機左軌（right-end）與手機底欄（top-end）共用。
EOF
)"
```

---

### Task 3: 桌機左側窄軌 AppRail（含 @tabler/icons-react 與 NAV_LINKS）

**Files:**
- Modify: `frontend/package.json`（新增 @tabler/icons-react）
- Create: `frontend/src/components/navLinks.ts`
- Create: `frontend/src/components/AppRail.tsx`
- Create: `frontend/src/components/AppRail.module.css`
- Test: `frontend/src/components/AppRail.test.tsx`

**Interfaces:**
- Consumes: `AccountMenu`（Task 2）、token 變數（Task 1）。
- Produces:
  - `navLinks.ts`：`export const NAV_LINKS: ReadonlyArray<{ to: string; label: string; Icon: <TablerIcon> }>`（搜尋/問答/監控）與 `export const MOBILE_NAV_QUERY = '(max-width: 48em)'`。
  - `export function AppRail()` — 60px 寬左軌內容（品牌「廷」+ 主導覽 + 底部 AccountMenu），Task 5 放入 `AppShell.Navbar`。

- [ ] **Step 1: 安裝 @tabler/icons-react**

Run（在 `frontend/`，timeout ≥ 300000ms）:

```bash
npm install --save-exact @tabler/icons-react
```

Expected: package.json dependencies 出現 `@tabler/icons-react`（精確版號）。

- [ ] **Step 2: 建立 navLinks.ts**

Create `frontend/src/components/navLinks.ts`：

```ts
import { IconActivity, IconMessages, IconSearch } from '@tabler/icons-react'

/** 全站主導覽連結（桌機左軌與手機底欄共用）。文案沿用現有「搜尋/問答/監控」平價。 */
export const NAV_LINKS = [
  { to: '/search', label: '搜尋', Icon: IconSearch },
  { to: '/ask', label: '問答', Icon: IconMessages },
  { to: '/monitor', label: '監控', Icon: IconActivity },
] as const

/** 手機斷點：<=48em 用底部分頁列，否則桌機左軌。 */
export const MOBILE_NAV_QUERY = '(max-width: 48em)'
```

- [ ] **Step 3: 寫失敗測試**

Create `frontend/src/components/AppRail.test.tsx`：

```tsx
import { test, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AppRail } from './AppRail'

vi.mock('../features/search/api', () => ({
  getStats: vi.fn(async () => ({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '研究員',
  })),
}))

const wrap = (path: string) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]} basename="/app">
      <QueryClientProvider client={qc}>
        <MantineProvider>
          <AppRail />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

test('品牌「廷」連到 /app/search，帶完整品牌名 aria-label', () => {
  wrap('/app/search')
  const brand = screen.getByRole('link', { name: '廷豐智能研報' })
  expect(brand).toHaveAttribute('href', '/app/search')
  expect(brand).toHaveTextContent('廷')
})

test('主導覽 landmark 含三個連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('目前分路帶 aria-current=page，其餘無', () => {
  wrap('/app/ask')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '搜尋' })).not.toHaveAttribute('aria-current', 'page')
})

test('左軌含帳號選單（頭像鈕）', () => {
  wrap('/app/search')
  expect(screen.getByTestId('account-avatar')).toBeInTheDocument()
})
```

- [ ] **Step 4: 跑測試確認失敗**

Run: `npx vitest run src/components/AppRail.test.tsx`
Expected: FAIL（模組不存在）。

- [ ] **Step 5: 實作 AppRail**

Create `frontend/src/components/AppRail.module.css`：

```css
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  padding: 14px 0 12px;
  background: var(--tf-surface);
}

.brand {
  font-family: var(--tf-serif);
  font-weight: 800;
  font-size: 22px;
  line-height: 1;
  color: var(--tf-gold-text);
  text-decoration: none;
  margin-bottom: 14px;
}

.nav {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 8px 9px;
  border-radius: 10px;
  min-width: 46px;
  color: var(--tf-text-3);
  text-decoration: none;
  font-size: 10px;
  line-height: 1;
}

.item:hover {
  background: var(--tf-border-weak);
}

.active {
  background: var(--tf-gold-tint);
  color: var(--tf-gold-text);
  font-weight: 700;
}

.account {
  margin-top: auto;
}
```

Create `frontend/src/components/AppRail.tsx`：

```tsx
import { Link, NavLink } from 'react-router'
import { AccountMenu } from './AccountMenu'
import { NAV_LINKS } from './navLinks'
import classes from './AppRail.module.css'

/** 桌機左側窄軌：品牌 glyph + 主導覽（圖示+小字）+ 底部帳號選單。 */
export function AppRail() {
  return (
    <div className={classes.rail}>
      <Link to="/search" className={classes.brand} aria-label="廷豐智能研報">
        廷
      </Link>
      <nav aria-label="主導覽" className={classes.nav}>
        {NAV_LINKS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              isActive ? `${classes.item} ${classes.active}` : classes.item
            }
          >
            <Icon size={20} stroke={1.8} aria-hidden />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className={classes.account}>
        <AccountMenu position="right-end" />
      </div>
    </div>
  )
}
```

- [ ] **Step 6: 跑測試確認通過**

Run: `npx vitest run src/components/AppRail.test.tsx`
Expected: 4 tests PASS。

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json src/components/navLinks.ts src/components/AppRail.tsx src/components/AppRail.module.css src/components/AppRail.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(導覽): 桌機左側窄軌 AppRail

新增 @tabler/icons-react；NAV_LINKS/MOBILE_NAV_QUERY 抽共用模組。
左軌＝襯線品牌「廷」+ 三導覽項（圖示+小字、active 金底膠囊、
aria-current 由 NavLink 提供）+ 底部 AccountMenu。樣式全走 --tf-* token。
EOF
)"
```

---

### Task 4: 手機底部分頁列 MobileTabBar

**Files:**
- Create: `frontend/src/components/MobileTabBar.tsx`
- Create: `frontend/src/components/MobileTabBar.module.css`
- Test: `frontend/src/components/MobileTabBar.test.tsx`

**Interfaces:**
- Consumes: `NAV_LINKS`（Task 3）、`AccountMenu`（Task 2）。
- Produces: `export function MobileTabBar()` — 56px 高底欄內容（三導覽格 + 第 4 格帳號），Task 5 放入 `AppShell.Footer`。

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/components/MobileTabBar.test.tsx`：

```tsx
import { test, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MobileTabBar } from './MobileTabBar'

vi.mock('../features/search/api', () => ({
  getStats: vi.fn(async () => ({
    total_reports: 0,
    markets: [],
    instrument_types: [],
    report_types: [],
    username: '研究員',
  })),
}))

const wrap = (path: string) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]} basename="/app">
      <QueryClientProvider client={qc}>
        <MantineProvider>
          <MobileTabBar />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

test('主導覽 landmark 含三個分頁連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('目前分路帶 aria-current=page', () => {
  wrap('/app/monitor')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '問答' })).not.toHaveAttribute('aria-current', 'page')
})

test('第 4 格帳號：頭像鈕點開有登出', async () => {
  wrap('/app/search')
  expect(screen.getByText('帳號')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '帳號選單' }))
  expect(await screen.findByText('研究員')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/components/MobileTabBar.test.tsx`
Expected: FAIL（模組不存在）。

- [ ] **Step 3: 實作 MobileTabBar**

Create `frontend/src/components/MobileTabBar.module.css`：

```css
.bar {
  display: flex;
  align-items: stretch;
  height: 100%;
  background: var(--tf-surface);
  padding-bottom: env(safe-area-inset-bottom);
}

.nav {
  display: flex;
  flex: 3;
}

.item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  color: var(--tf-text-3);
  text-decoration: none;
  font-size: 10px;
  line-height: 1;
}

.active {
  color: var(--tf-gold-text);
  font-weight: 700;
}

.account {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  font-size: 10px;
  line-height: 1;
  color: var(--tf-text-3);
}
```

Create `frontend/src/components/MobileTabBar.tsx`：

```tsx
import { NavLink } from 'react-router'
import { AccountMenu } from './AccountMenu'
import { NAV_LINKS } from './navLinks'
import classes from './MobileTabBar.module.css'

/** 手機底部分頁列：三導覽格 + 第 4 格帳號（點開登出選單）。 */
export function MobileTabBar() {
  return (
    <div className={classes.bar}>
      <nav aria-label="主導覽" className={classes.nav}>
        {NAV_LINKS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              isActive ? `${classes.item} ${classes.active}` : classes.item
            }
          >
            <Icon size={20} stroke={1.8} aria-hidden />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className={classes.account}>
        <AccountMenu position="top-end" />
        <span>帳號</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/components/MobileTabBar.test.tsx`
Expected: 3 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/components/MobileTabBar.tsx src/components/MobileTabBar.module.css src/components/MobileTabBar.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(導覽): 手機底部分頁列 MobileTabBar

三導覽格（NAV_LINKS 共用、active 金字）+ 第 4 格帳號
（AccountMenu top-end 開登出）；safe-area-inset-bottom 相容。
EOF
)"
```

---

### Task 5: RootLayout 接骨架（App.tsx）並移除 AppNav

**Files:**
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/components/AppNav.tsx`、`frontend/src/components/AppNav.test.tsx`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `AppRail`（Task 3）、`MobileTabBar`（Task 4）、`MOBILE_NAV_QUERY`（Task 3）、`useMediaQuery`（`@mantine/hooks`）。
- Produces: RootLayout＝桌機 `AppShell navbar 60px`（內容 AppRail）／手機 `AppShell footer 56px`（內容 MobileTabBar）。routes 結構、lazy loading、VisuallyHidden h1 不變。

- [ ] **Step 1: 在 App.test.tsx 追加失敗測試**

在 `frontend/src/App.test.tsx` 追加（import 區補 `import { afterEach, vi } from 'vitest'`，並沿用檔內既有 `renderAt`）：

```tsx
afterEach(() => {
  vi.unstubAllGlobals()
})

function mockMatchMedia(matchingQueries: ReadonlyArray<string>) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: matchingQueries.includes(query),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

test('桌機：渲染左軌（品牌廷 + 主導覽），無底部帳號分頁格', async () => {
  renderAt('/app/search')
  expect(await screen.findByRole('link', { name: '廷豐智能研報' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.queryByText('帳號')).toBeNull()
})

test('手機（<=48em）：渲染底部分頁列（含帳號格），無品牌廷', async () => {
  mockMatchMedia(['(max-width: 48em)'])
  renderAt('/app/search')
  expect(await screen.findByText('帳號')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '廷豐智能研報' })).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/App.test.tsx`
Expected: 新增 2 個 FAIL（找不到品牌廷/帳號格）；既有重導向測試 PASS。

- [ ] **Step 3: 改寫 App.tsx 的 RootLayout**

`frontend/src/App.tsx`：移除 `import { AppNav } from './components/AppNav'`，改為：

```tsx
import { useMediaQuery } from '@mantine/hooks'
import { AppRail } from './components/AppRail'
import { MobileTabBar } from './components/MobileTabBar'
import { MOBILE_NAV_QUERY } from './components/navLinks'
```

`AppShell` 相關 import 移除 `VisuallyHidden` 以外不變（`AppShell, Loader, Title, VisuallyHidden` 照舊）。RootLayout 改為：

```tsx
function RootLayout() {
  // 手機（<=48em）：底部分頁列；桌機：左側窄軌。jsdom 無 matchMedia → 預設桌機。
  const isMobile = useMediaQuery(MOBILE_NAV_QUERY, false, { getInitialValueInEffect: false })
  return (
    <AppShell
      navbar={isMobile ? undefined : { width: 60, breakpoint: 0 }}
      footer={isMobile ? { height: 56 } : undefined}
      padding="md"
    >
      {!isMobile && (
        <AppShell.Navbar>
          <AppRail />
        </AppShell.Navbar>
      )}
      <AppShell.Main>
        {/* 全站唯一 h1（視覺隱藏）：確保每頁有正常的 h1→h2 標題梯級 */}
        <VisuallyHidden component="h1">廷豐智能研報</VisuallyHidden>
        <Outlet />
      </AppShell.Main>
      {isMobile && (
        <AppShell.Footer>
          <MobileTabBar />
        </AppShell.Footer>
      )}
    </AppShell>
  )
}
```

routes、`createBrowserRouter(..., { basename: '/app' })`、NotFound、RouteFallback 全部不動。

- [ ] **Step 4: 刪除 AppNav**

```bash
git rm src/components/AppNav.tsx src/components/AppNav.test.tsx
```

（AppNav 的 a11y 契約——landmark、aria-current、logout form、avatar testid——已由 AppRail/MobileTabBar/AccountMenu 測試接手。）

- [ ] **Step 5: 全量測試**

Run: `npx vitest run`
Expected: 全綠（App.test.tsx 4 tests；不再有 AppNav.test）。

- [ ] **Step 6: Commit**

```bash
git add src/App.tsx src/App.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(導覽): RootLayout 換左軌/底部分頁骨架，移除 AppNav 頂欄

桌機 AppShell navbar 60px 放 AppRail；手機（<=48em）footer 56px 放
MobileTabBar；頂欄退役。h1 梯級、routes、lazy loading 不變。
AppNav 的 a11y 契約由新元件測試接手。
EOF
)"
```

---

### Task 6: 監控頁換新設計語言

**Files:**
- Modify: `frontend/src/features/monitor/MonitorPage.tsx`
- Test: `frontend/src/features/monitor/MonitorPage.test.tsx`（既有測試為行為斷言，預期不需改；跑掉才修）

**Interfaces:**
- Consumes: Task 1 的 Card 預設（12px 圓角/邊框/淡陰影自動生效）、`tf-tabular-nums`、theme 標題襯線。
- Produces: 監控頁作為新設計語言的首個驗證頁；資料流（useQuery 2s 輪詢、useTween、rate/eta）零變動。

- [ ] **Step 1: 先跑既有測試建立基線**

Run: `npx vitest run src/features/monitor/MonitorPage.test.tsx`
Expected: 5 tests PASS（基線）。

- [ ] **Step 2: 改造 MonitorPage.tsx（只動呈現）**

對 `frontend/src/features/monitor/MonitorPage.tsx` 做以下修改（資料流零變動）：

1. `Tile` 的 Card 移除顯式外觀 props、KPI 數字放大＋等寬：

```tsx
function Tile({ label, value, suffix, sub }: { label: string; value: number | null; suffix?: string; sub?: string }) {
  const shown = useTween(value)
  return (
    <Card padding="md">
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} fz={26} lh={1.3} className="tf-tabular-nums">
        {nf(shown)}
        {suffix ? (
          <Text span size="sm" c="dimmed">
            {suffix}
          </Text>
        ) : null}
      </Text>
      {sub ? (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      ) : null}
    </Card>
  )
}
```

2. 其餘 5 處 `<Card withBorder padding="md" radius="md">` 一律改 `<Card padding="md">`（外觀交給 theme 預設）。

3. 區塊標題改 heading（襯線自動生效、a11y 梯級 h2→h3）：
   - `<Text fw={600}>標註 TAGGING</Text>` → `<Title order={3} size="h5">標註 TAGGING</Title>`
   - `<Text fw={600}>導入 INGEST</Text>` → `<Title order={3} size="h5">導入 INGEST</Title>`
   - `<Text fw={600}>摘要 SUMMARY</Text>` → `<Title order={3} size="h5">摘要 SUMMARY</Title>`
   - `<Text fw={600} mb="xs">管線 PIPELINES</Text>` → `<Title order={3} size="h5" mb="xs">管線 PIPELINES</Title>`
   - `<Text fw={600} mb="xs">市場分佈 MARKETS</Text>` → `<Title order={3} size="h5" mb="xs">市場分佈 MARKETS</Title>`

4. 市場分佈的數量加等寬：`<Text size="sm" c="dimmed" className="tf-tabular-nums">{nf(m.count)}</Text>`。

5. 其他（時鐘 ff="monospace"、Badge、Progress color="gold"、更新於 footer、orchestrator 區）不動。

- [ ] **Step 3: 跑測試**

Run: `npx vitest run src/features/monitor/MonitorPage.test.tsx`
Expected: 5 tests PASS（斷言全是文字/行為，`findByText(/標註 TAGGING/)` 對 Title 同樣命中）。若有失敗，只准更新「斷言舊視覺細節」的斷言，行為斷言失敗＝實作錯誤要修實作。

- [ ] **Step 4: Commit**

```bash
git add src/features/monitor/MonitorPage.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
refactor(監控): 監控頁換新設計語言

Card 外觀交給 theme 新預設（12px 圓角+邊框+淡陰影）；KPI 數字放大
並套 tabular-nums；區塊標題 Text 改 Title h3（襯線+標題梯級）。
資料流（2s 輪詢/useTween/rate）零變動。
EOF
)"
```

---

### Task 7: 全量驗證 + e2e 冒煙 + 開 PR

**Files:**
- 無新檔（驗證與交付）。

**Interfaces:**
- Consumes: Task 1-6 全部完成。
- Produces: PR（`feat/frontend-redesign-foundation` → main）。

- [ ] **Step 1: 全量單元測試 + typecheck + lint + build**

Run（在 `frontend/`，timeout ≥ 600000ms）:

```bash
npx vitest run && npm run lint && npm run build
```

Expected: vitest 全綠、eslint 0 error、`tsc --noEmit` + vite build 成功產出 `dist/`。

- [ ] **Step 2: 起 :8098 驗證站（勿動正式 :8097）**

Run（在 repo 根目錄，背景執行）:

```bash
ss -tlnp | grep -E '8097|8098'   # 先確認 8098 沒被占用、8097 是正式站勿殺
make serve PORT=8098
```

Expected: uvicorn 起在 :8098（BGE-M3 暖機需 1-2 分鐘，等 `GET /api/stats` 回 200 或 302）。

- [ ] **Step 3: e2e 冒煙（monitor 平價 spec + 全頁截圖）**

Run（在 `frontend/`）:

```bash
MONITOR_BASE_URL=http://localhost:8098 npx playwright test e2e/monitor.spec.mjs
```

Expected: PASS（登入 → /app/monitor → 輪詢 ≥2 次 → 0 console error）。

再用 Playwright（腳本或 MCP）對 :8098 登入後截圖人工檢視：
- 桌機 1280×800：`/app/search`、`/app/ask`、`/app/monitor` —— 左軌顯示、active 金底膠囊、標題襯線、卡片新語言、頁面功能可操作。
- 手機 390×844：同三頁 —— 底部分頁列顯示（含帳號格）、左軌隱藏、內容不被底欄遮擋。
- 點頭像 → 選單顯示帳號名與登出。

- [ ] **Step 4: 收尾驗證站**

```bash
ss -tlnp | grep 8098   # 找到 :8098 的 pid（核對埠號，嚴禁殺到 :8097）
kill <8098 的 pid>
```

- [ ] **Step 5: Push + 開 PR**

```bash
git log --oneline origin/main..HEAD   # 核對本分支 commits（spec + Task 1-6）
git push -u origin feat/frontend-redesign-foundation
gh pr create --title "feat(設計系統): 整站重設計 PR1 地基（token+襯線字型+左軌/底部分頁骨架+監控換皮）" --body "$(cat <<'EOF'
## Summary
- 整站視覺重設計第 1 段（spec: docs/superpowers/specs/2026-07-02-frontend-redesign-design.md）
- 設計 token（--tf-* CSS 變數）+ Noto Serif TC 襯線字型（CDN + swap 降級）
- 全站骨架：桌機左側窄軌 AppRail / 手機底部分頁列 MobileTabBar（AppNav 頂欄退役）
- 監控頁換新設計語言（Card 新預設、KPI tabular-nums、區塊標題襯線）
- 後端零變動；深色模式不做（token 已預留）

## Parity
- 導覽三頁可達、aria-current、主導覽 landmark、登出原生 form POST /logout、account-avatar testid 全保留
- 監控頁資料流（2s 輪詢/useTween/rate）零變動

## Test plan
- [ ] vitest 全綠 + eslint + build
- [ ] e2e monitor spec（:8098）PASS
- [ ] 桌機/手機截圖人工檢視三頁

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR 建立成功，URL 回報給使用者。

---

## Self-Review 紀錄

- **Spec 覆蓋（PR1 範圍）**：色彩 token（Task 1）、襯線字型與載入策略（Task 1）、卡片語言＋Card/Paper 預設（Task 1）、金色文字對比規則（tokens.css 註解 + Global Constraints）、tabular-nums（Task 1 工具 class、Task 6 套用）、左軌（Task 3/5）、底部分頁列（Task 4/5）、帳號區與登出平價（Task 2）、h1 梯級保留（Task 5）、監控換皮（Task 6）、測試策略與 :8098 e2e（Task 7）。PR2-4 範圍（檢索/問答/登入頁）不在本計畫，依 spec 另立計畫。
- **佔位掃描**：所有程式碼步驟均含完整程式碼與確切指令；無 TBD/「適當處理」類字眼。
- **型別/命名一致性**：`NAV_LINKS`/`MOBILE_NAV_QUERY`（Task 3 定義，Task 4/5 引用）；`AccountMenu({ position })`（Task 2 定義，Task 3 用 `right-end`、Task 4 用 `top-end`）；`--tf-*` 變數名跨 Task 1/3/4 一致；`tf-tabular-nums` class 名 Task 1 定義、Task 6 引用。
