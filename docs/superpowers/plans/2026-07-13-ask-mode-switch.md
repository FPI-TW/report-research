# 問答頁切換鈕 ＋ 共享元素轉場 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓問答頁常駐「檢索研報／智能問答」切換鈕（空狀態＋對話進行中），並在兩頁切換時以 View Transitions API 做 active pill 共享滑動、完整銜接不卡頓。

**Architecture:** 抽出共用 `ModeSwitch` 分段控制元件，用於檢索頁 hero、問答頁空狀態、問答頁對話頂端列三處；自行以 `useLocation` 判定 active。切換連結帶 react-router 的 `viewTransition`，配合全域 `view-transitions.css` 用 `view-transition-name` 讓白 thumb 與容器在兩頁間 morph。以「hover/focus 預載對向路由 chunk」消除 Suspense 空白閃爍。後端、API、資料流零改動。

**Tech Stack:** React 19.2.7、react-router 8.0.1（`<Link viewTransition>` / `useViewTransitionState`）、CSS Modules、Vite 8、Vitest 4、Playwright。

## Global Constraints

- 純前端（`frontend/`）；後端、API、資料流、SSE、qa_log 零改動。
- react-router 8.0.1：切換連結用 `<Link viewTransition>`；**不要**手動 feature-detect —— 不支援 View Transitions 的瀏覽器由 react-router 自動退為即時導覽。
- **檢索頁維持 hero-only**：不在檢索頁加常駐切換列。
- 左軌 `SideRail`／手機 `MobileTabBar` 導覽**不加** `viewTransition`（去監控頁等維持即時換頁）。
- 固定文案：`檢索研報`、`智能問答`（與現有 hero 一致，勿更動）。
- 圖示：`檢索研報` 用 `Icon name="search"`、`智能問答` 用 `Icon name="messages"`（皆已存在於 `Icon.tsx`）。
- View Transition 名稱放**全域** `styles/view-transitions.css`、以 `[data-vt=...]` 綁定——CSS Modules 會在地化 class／animation 名，名稱放 module 內有落空風險；每頁僅渲染一個 `ModeSwitch`，故 `view-transition-name` 天然唯一。
- 動效 token：morph 用 `--tf-dur-3`（240ms）＋`--tf-ease-out`（`cubic-bezier(0.22,1,0.36,1)`）；`@media (prefers-reduced-motion: reduce)` 必須讓 VT 立即切換。
- 測試：`npm test`（`vitest run`）；jsdom 無 `document.startViewTransition`，測試只驗 DOM／連結／`aria-current`，不驗動畫。
- **worktree 無 `node_modules`**：任何測試前先 `npm ci`。
- Commit：Conventional Commits＋繁中 scope（如 `feat(問答):`）；`git add <明確路徑>`，不用 `-A`/`.`。commit 訊息尾加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 已知環境陷阱：RTK 可能遮蔽 vitest 離開碼；若 `npm test` 的通過／失敗與實際不符，改用 `rtk proxy npm test`。

---

## File Structure

**新增**
- `frontend/src/lib/routePreload.ts` — 路由 lazy loader 註冊表＋去重預載（`routeLoaders`、`preloadRoute`、`preloadIdle`）。App 的 `lazy()` 與預載共用同一組 import thunk（單一真相）。
- `frontend/src/lib/routePreload.test.ts`
- `frontend/src/components/shell/ModeSwitch.tsx` — 「檢索研報／智能問答」分段控制；自判 active、掛 `viewTransition`＋`data-vt`、hover/focus 預載對向路由。
- `frontend/src/components/shell/ModeSwitch.module.css`
- `frontend/src/components/shell/ModeSwitch.test.tsx`
- `frontend/src/styles/view-transitions.css` — VT 名稱綁定＋brand 化 morph 時序＋reduced-motion 歸零；於 `main.tsx` import。

**編輯**
- `frontend/src/App.tsx` — `lazy()` 改吃 `routeLoaders`；掛載後閒置預載 `search`/`ask`。
- `frontend/src/main.tsx` — import `./styles/view-transitions.css`。
- `frontend/src/features/search/SearchPage.tsx` — hero 內 inline `heroModes` 換成 `<ModeSwitch/>`；移除多餘 `Link`/`Icon` import。
- `frontend/src/features/search/SearchPage.module.css` — 移除 `.heroModes/.heroMode/.heroModeActive`，加 `.heroSwitch` 間距。
- `frontend/src/features/ask/AskEmptyState.tsx` — 副標與 Composer 間插入 `<ModeSwitch/>`。
- `frontend/src/features/ask/AskEmptyState.module.css` — `.switch` 間距。
- `frontend/src/features/ask/AskEmptyState.test.tsx` — 包 `MemoryRouter`＋新增切換鈕斷言。
- `frontend/src/features/ask/AskPage.tsx` — 對話態（`turns.length > 0`）於 `.flow` 上方加 `.modeBar` 放 `<ModeSwitch size="sm"/>`。
- `frontend/src/features/ask/AskPage.module.css` — `.modeBar` 樣式。
- `frontend/src/components/shell/AppShell.module.css` — `@supports` 收斂 `.routeReveal` 時長，避免與 VT root crossfade 雙淡入。

---

## Task 0: 環境設定與基準線

**Files:** 無變更（僅安裝依賴與跑基準測試）

- [ ] **Step 1: 安裝依賴**

Run:
```bash
cd frontend && npm ci
```
Expected: 安裝完成、無錯誤（首次可能較久）。

- [ ] **Step 2: 確認基準測試全綠**

Run:
```bash
cd frontend && npm test
```
Expected: 全數 PASS（這是改動前基準；若 RTK 遮蔽離開碼，改 `rtk proxy npm test`）。

- [ ] **Step 3: 確認型別基準**

Run:
```bash
cd frontend && npm run typecheck
```
Expected: 無錯誤。

（本任務不 commit。）

---

## Task 1: 路由預載註冊表

**Files:**
- Create: `frontend/src/lib/routePreload.ts`
- Test: `frontend/src/lib/routePreload.test.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces:
  - `type RouteKey = 'search' | 'ask' | 'monitor'`
  - `const routeLoaders: Record<RouteKey, () => Promise<{ default: ComponentType }>>`
  - `function preloadRoute(key: RouteKey): void` — 首次呼叫觸發對應 `import()`，之後去重。
  - `function preloadIdle(keys: RouteKey[]): void` — `requestIdleCallback`（不可用則 `setTimeout(…,200)`）批次預載。

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/lib/routePreload.test.ts`:
```ts
import { afterEach, expect, test, vi } from 'vitest'
import * as rp from './routePreload'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('preloadRoute 首次呼叫觸發 loader、重複呼叫去重', () => {
  const spy = vi.spyOn(rp.routeLoaders, 'ask').mockResolvedValue({ default: () => null } as never)
  rp.preloadRoute('ask')
  rp.preloadRoute('ask')
  expect(spy).toHaveBeenCalledTimes(1)
})

test('preloadIdle 於 requestIdleCallback 可用時排程並預載', () => {
  const spy = vi.spyOn(rp.routeLoaders, 'search').mockResolvedValue({ default: () => null } as never)
  const ric = vi.fn((cb: () => void) => { cb(); return 0 })
  vi.stubGlobal('requestIdleCallback', ric)
  rp.preloadIdle(['search'])
  expect(ric).toHaveBeenCalledTimes(1)
  expect(spy).toHaveBeenCalledTimes(1)
})

test('preloadIdle 無 requestIdleCallback 時退回 setTimeout', () => {
  vi.stubGlobal('requestIdleCallback', undefined)
  vi.useFakeTimers()
  const spy = vi.spyOn(rp.routeLoaders, 'monitor').mockResolvedValue({ default: () => null } as never)
  rp.preloadIdle(['monitor'])
  vi.runAllTimers()
  expect(spy).toHaveBeenCalledTimes(1)
  vi.useRealTimers()
})
```
（三個測試各只用一個 route key：`ask`/`search`/`monitor`，避開模組級去重 Set 的跨測試干擾。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- src/lib/routePreload.test.ts`
Expected: FAIL（`Cannot find module './routePreload'`）。

- [ ] **Step 3: 實作 `routePreload.ts`**

Create `frontend/src/lib/routePreload.ts`:
```ts
import type { ComponentType } from 'react'

export type RouteKey = 'search' | 'ask' | 'monitor'

/** lazy() 與預載共用同一組 import thunk（單一真相，避免路徑字串重複） */
export const routeLoaders: Record<RouteKey, () => Promise<{ default: ComponentType }>> = {
  search: () => import('../features/search/SearchPage'),
  ask: () => import('../features/ask/AskPage'),
  monitor: () => import('../features/monitor/MonitorPage'),
}

const started = new Set<RouteKey>()

/** 觸發對向路由 chunk 預載；同一 key 只跑一次 */
export function preloadRoute(key: RouteKey): void {
  if (started.has(key)) return
  started.add(key)
  void routeLoaders[key]()
}

/** 閒置時批次預載（requestIdleCallback → 退回 setTimeout） */
export function preloadIdle(keys: RouteKey[]): void {
  const run = () => keys.forEach(preloadRoute)
  if (typeof requestIdleCallback === 'function') requestIdleCallback(run)
  else setTimeout(run, 200)
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npm test -- src/lib/routePreload.test.ts`
Expected: PASS（3 passed）。

- [ ] **Step 5: 接線 `App.tsx`（lazy 共用 loaders＋閒置預載）**

Modify `frontend/src/App.tsx` — 目前為：
```tsx
import { Suspense, lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'

const SearchPage = lazy(() => import('./features/search/SearchPage'))
const AskPage = lazy(() => import('./features/ask/AskPage'))
const MonitorPage = lazy(() => import('./features/monitor/MonitorPage'))
```
改為：
```tsx
import { Suspense, lazy, useEffect } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'
import { routeLoaders, preloadIdle } from './lib/routePreload'

const SearchPage = lazy(routeLoaders.search)
const AskPage = lazy(routeLoaders.ask)
const MonitorPage = lazy(routeLoaders.monitor)
```
並把 `App` 由：
```tsx
export default function App() {
  return <RouterProvider router={router} />
}
```
改為：
```tsx
export default function App() {
  useEffect(() => { preloadIdle(['search', 'ask']) }, [])
  return <RouterProvider router={router} />
}
```
（其餘 `routes`/`router`/`NotFound` 不動。）

- [ ] **Step 6: 型別與全測試**

Run: `cd frontend && npm run typecheck && npm test`
Expected: typecheck 無錯；測試全綠。

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/lib/routePreload.ts src/lib/routePreload.test.ts src/App.tsx
git commit -m "$(cat <<'EOF'
feat(前端): 路由預載註冊表，App lazy 共用 loaders＋閒置預載

新增 routePreload（routeLoaders/preloadRoute/preloadIdle），供切換鈕
hover 預載對向路由 chunk 之用，消除 View Transition 期間的 Suspense 空白。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `ModeSwitch` 元件 ＋ 全域 View Transitions 樣式

**Files:**
- Create: `frontend/src/components/shell/ModeSwitch.tsx`
- Create: `frontend/src/components/shell/ModeSwitch.module.css`
- Create: `frontend/src/styles/view-transitions.css`
- Create: `frontend/src/components/shell/ModeSwitch.test.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `preloadRoute`, `RouteKey`（Task 1）；`Icon`, `IconName`（`components/primitives/Icon`）。
- Produces: `function ModeSwitch(props: { size?: 'md' | 'sm'; className?: string }): JSX.Element` — 渲染兩項分段控制；`useLocation().pathname.startsWith('/ask')` 判定 active；active 項為 `<span aria-current="page" data-vt="mode-thumb">`，非 active 為 `<Link viewTransition data-…>`；容器帶 `data-vt="mode-switch"`。

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/components/shell/ModeSwitch.test.tsx`:
```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, test, vi } from 'vitest'

vi.mock('../../lib/routePreload', () => ({ preloadRoute: vi.fn() }))
import { preloadRoute } from '../../lib/routePreload'
import { ModeSwitch } from './ModeSwitch'

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><ModeSwitch /></MemoryRouter>)
}

test('在 /search：檢索為 active、問答為連向 /ask 的連結', () => {
  renderAt('/search')
  expect(screen.getByText('檢索研報').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /智能問答/ })).toHaveAttribute('href', '/ask')
})

test('在 /ask：問答為 active、檢索為連向 /search 的連結', () => {
  renderAt('/ask')
  expect(screen.getByText('智能問答').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /檢索研報/ })).toHaveAttribute('href', '/search')
})

test('hover 非 active 連結預載對向路由', () => {
  renderAt('/search')
  fireEvent.pointerEnter(screen.getByRole('link', { name: /智能問答/ }))
  expect(preloadRoute).toHaveBeenCalledWith('ask')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- src/components/shell/ModeSwitch.test.tsx`
Expected: FAIL（找不到 `./ModeSwitch`）。

- [ ] **Step 3: 實作 `ModeSwitch.tsx`**

Create `frontend/src/components/shell/ModeSwitch.tsx`:
```tsx
import { Link, useLocation } from 'react-router'
import { Icon, type IconName } from '../primitives/Icon'
import { preloadRoute, type RouteKey } from '../../lib/routePreload'
import styles from './ModeSwitch.module.css'

interface ModeSwitchProps {
  size?: 'md' | 'sm'
  className?: string
}

/** 「檢索研報／智能問答」分段控制。每頁僅渲染一個；View Transition 名稱由全域
 *  view-transitions.css 依 data-vt 綁定，切換時 active thumb 於兩頁間 morph。 */
export function ModeSwitch({ size = 'md', className }: ModeSwitchProps) {
  const askActive = useLocation().pathname.startsWith('/ask')
  const cls = [styles.switch, size === 'sm' ? styles.sm : '', className ?? ''].filter(Boolean).join(' ')
  return (
    <div className={cls} data-vt="mode-switch" role="group" aria-label="檢索與問答切換">
      <ModeItem to="/search" icon="search" label="檢索研報" active={!askActive} routeKey="search" />
      <ModeItem to="/ask" icon="messages" label="智能問答" active={askActive} routeKey="ask" />
    </div>
  )
}

interface ModeItemProps {
  to: string
  icon: IconName
  label: string
  active: boolean
  routeKey: RouteKey
}

function ModeItem({ to, icon, label, active, routeKey }: ModeItemProps) {
  if (active) {
    return (
      <span className={`${styles.mode} ${styles.active}`} aria-current="page" data-vt="mode-thumb">
        <Icon name={icon} size={15} />{label}
      </span>
    )
  }
  const preload = () => preloadRoute(routeKey)
  return (
    <Link to={to} className={styles.mode} viewTransition onPointerEnter={preload} onFocus={preload}>
      <Icon name={icon} size={15} />{label}
    </Link>
  )
}
```

- [ ] **Step 4: 實作 `ModeSwitch.module.css`**

Create `frontend/src/components/shell/ModeSwitch.module.css`（沿用原 hero `heroModes` 視覺）:
```css
.switch { display: inline-flex; background: #eceae3; border-radius: 12px; padding: 3px; }
.mode { display: inline-flex; align-items: center; gap: 7px; border-radius: 9px; padding: 7px 20px; font-size: 13.5px; color: var(--tf-text-3); text-decoration: none; }
.mode:hover { color: var(--tf-text-2); }
.active { background: var(--tf-surface); color: var(--tf-ink-deep); font-weight: 600; box-shadow: var(--tf-shadow-seg); }
.active:hover { color: var(--tf-ink-deep); }
.sm .mode { padding: 5px 14px; font-size: 12.5px; }
```

- [ ] **Step 5: 實作全域 `view-transitions.css`**

Create `frontend/src/styles/view-transitions.css`:
```css
/* 共享元素轉場：檢索↔問答切換鈕。名稱定義於全域（非 CSS Module），避免在地化雜湊；
   每頁僅一個 ModeSwitch，故名稱唯一。 */
[data-vt='mode-switch'] { view-transition-name: tf-mode-switch; }
[data-vt='mode-thumb'] { view-transition-name: tf-mode-thumb; }

/* 讓 thumb／容器的 morph 使用品牌時序（240ms＋tf-ease-out） */
::view-transition-group(tf-mode-thumb),
::view-transition-group(tf-mode-switch) {
  animation-duration: var(--tf-dur-3);
  animation-timing-function: var(--tf-ease-out);
}

/* 偏好減少動態 → View Transition 立即切換（無 morph／crossfade） */
@media (prefers-reduced-motion: reduce) {
  ::view-transition-group(*),
  ::view-transition-old(*),
  ::view-transition-new(*) { animation: none !important; }
}
```

- [ ] **Step 6: 於 `main.tsx` 引入全域樣式**

Modify `frontend/src/main.tsx` — 在 `import './styles/tokens.css'` 之後加一行：
```tsx
import './styles/tokens.css'
import './styles/view-transitions.css'
```

- [ ] **Step 7: 跑測試確認通過**

Run: `cd frontend && npm test -- src/components/shell/ModeSwitch.test.tsx`
Expected: PASS（3 passed）。

- [ ] **Step 8: 型別與全測試**

Run: `cd frontend && npm run typecheck && npm test`
Expected: typecheck 無錯；測試全綠。

- [ ] **Step 9: Commit**

```bash
cd frontend && git add src/components/shell/ModeSwitch.tsx src/components/shell/ModeSwitch.module.css src/components/shell/ModeSwitch.test.tsx src/styles/view-transitions.css src/main.tsx
git commit -m "$(cat <<'EOF'
feat(前端): 抽共用 ModeSwitch 切換鈕＋全域 View Transitions 樣式

檢索研報／智能問答分段控制，自判 active、切換連結帶 viewTransition、
hover/focus 預載對向路由；view-transition-name 以 data-vt 綁在全域樣式，
morph 用品牌時序，reduced-motion 立即切換。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 檢索頁 hero 改用 `ModeSwitch`

**Files:**
- Modify: `frontend/src/features/search/SearchPage.tsx`
- Modify: `frontend/src/features/search/SearchPage.module.css`
- Test: `frontend/src/features/search/SearchPage.test.tsx`（既有，須續綠）

**Interfaces:**
- Consumes: `ModeSwitch`（Task 2）。

- [ ] **Step 1: 替換 hero 內 inline `heroModes`**

Modify `frontend/src/features/search/SearchPage.tsx`：

1. import 調整——
   - 第 2 行 `import { Link, useSearchParams } from 'react-router'` → `import { useSearchParams } from 'react-router'`
   - 移除第 14 行 `import { Icon } from '../../components/primitives/Icon'`
   - 在 `import { BrandLogo } ...` 後加：`import { ModeSwitch } from '../../components/shell/ModeSwitch'`
2. 把 hero 內這段（現第 84–91 行）：
```tsx
            <div className={styles.heroModes}>
              <span className={`${styles.heroMode} ${styles.heroModeActive}`} aria-current="page">
                <Icon name="search" size={15} />檢索研報
              </span>
              <Link to="/ask" className={styles.heroMode}>
                <Icon name="messages" size={15} />智能問答
              </Link>
            </div>
```
換成：
```tsx
            <ModeSwitch className={styles.heroSwitch} />
```

- [ ] **Step 2: 更新 `SearchPage.module.css`**

Modify `frontend/src/features/search/SearchPage.module.css`：移除 `.heroModes`、`.heroMode`、`.heroMode:hover`、`.heroModeActive`、`.heroModeActive:hover`（現第 11–15 行），改加：
```css
.heroSwitch { margin-top: 30px; }
```

- [ ] **Step 3: 跑既有 SearchPage 測試確認未破壞**

Run: `cd frontend && npm test -- src/features/search/SearchPage.test.tsx`
Expected: PASS（文案 `檢索研報／智能問答` 不變；hero 測試 `browse 載入結果` 與 `切表格檢視` 皆續綠）。

- [ ] **Step 4: 型別檢查（確認移除 import 無殘留使用）**

Run: `cd frontend && npm run typecheck`
Expected: 無「`Link`/`Icon` is declared but never used」等錯誤。

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/features/search/SearchPage.tsx src/features/search/SearchPage.module.css
git commit -m "$(cat <<'EOF'
refactor(檢索): hero 切換鈕改用共用 ModeSwitch

移除 SearchPage 內 inline heroModes 與其樣式，改用 ModeSwitch，
使檢索頁切換鈕與問答頁一致並可參與共享元素轉場。行為與外觀不變。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 問答頁空狀態加入 `ModeSwitch`

**Files:**
- Modify: `frontend/src/features/ask/AskEmptyState.tsx`
- Modify: `frontend/src/features/ask/AskEmptyState.module.css`
- Test: `frontend/src/features/ask/AskEmptyState.test.tsx`

**Interfaces:**
- Consumes: `ModeSwitch`（Task 2）。

- [ ] **Step 1: 更新測試（包 Router＋新增切換鈕斷言）**

Replace `frontend/src/features/ask/AskEmptyState.test.tsx` 全檔為：
```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, test } from 'vitest'
import { AskEmptyState } from './AskEmptyState'

function renderEmpty() {
  return render(
    <MemoryRouter initialEntries={['/ask']}>
      <AskEmptyState value="" onChange={() => {}} onSubmit={() => {}} />
    </MemoryRouter>,
  )
}

test('顯示標題/副標/輸入框，無範例膠囊', () => {
  renderEmpty()
  expect(screen.getByText('向廷豐智能體提問')).toBeInTheDocument()
  expect(screen.getByText(/以自然語言詢問研究主題/)).toBeInTheDocument()
  expect(screen.getByPlaceholderText('輸入你的問題…')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /台積電|AI 伺服器|Fed/ })).toBeNull()
})

test('含檢索↔問答切換鈕：問答 active、檢索連向 /search', () => {
  renderEmpty()
  expect(screen.getByText('智能問答').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /檢索研報/ })).toHaveAttribute('href', '/search')
})
```

- [ ] **Step 2: 跑測試確認新斷言失敗**

Run: `cd frontend && npm test -- src/features/ask/AskEmptyState.test.tsx`
Expected: FAIL（第二個測試找不到「檢索研報」連結／`aria-current`）。

- [ ] **Step 3: 在 `AskEmptyState.tsx` 插入 `ModeSwitch`**

Replace `frontend/src/features/ask/AskEmptyState.tsx` 全檔為：
```tsx
import { Composer } from './Composer'
import { BrandLogo } from '../../components/BrandLogo'
import { ModeSwitch } from '../../components/shell/ModeSwitch'
import styles from './AskEmptyState.module.css'

interface Props { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void }

export function AskEmptyState({ value, onChange, onSubmit }: Props) {
  return (
    <div className={`${styles.wrap} tf-reveal`}>
      <BrandLogo size={56} className={styles.glyph} />
      <div className={styles.title}>向廷豐智能體提問</div>
      <div className={styles.sub}>以自然語言詢問研究主題，回答將附上券商研報的引用來源。</div>
      <ModeSwitch className={styles.switch} />
      <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" />
    </div>
  )
}
```

- [ ] **Step 4: `AskEmptyState.module.css` 加間距**

Modify `frontend/src/features/ask/AskEmptyState.module.css` 末尾加：
```css
.switch { margin: 4px 0 8px; }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && npm test -- src/features/ask/AskEmptyState.test.tsx`
Expected: PASS（2 passed）。

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/features/ask/AskEmptyState.tsx src/features/ask/AskEmptyState.module.css src/features/ask/AskEmptyState.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): 空狀態加入檢索↔問答切換鈕

AskEmptyState 於副標與輸入框間放 ModeSwitch（問答 active），
鏡像檢索頁 hero，並作為共享元素轉場的問答端錨點。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 問答頁對話進行中頂端切換列

**Files:**
- Modify: `frontend/src/features/ask/AskPage.tsx`
- Modify: `frontend/src/features/ask/AskPage.module.css`
- Test: `frontend/src/features/ask/AskPage.test.tsx`

**Interfaces:**
- Consumes: `ModeSwitch`（Task 2）。

- [ ] **Step 1: 新增測試（對話態頂端切換列）**

在 `frontend/src/features/ask/AskPage.test.tsx` 末尾（第 76 行 `})` 之後）追加：
```tsx
test('對話進行中頂端顯示檢索↔問答切換鈕', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [] },
    { event: 'status', data: { stage: 'generating', thinking_ms: 1000 } },
    { event: 'token', data: '答案' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: [] } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '台積電' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByText('台積電')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /檢索研報/ })).toHaveAttribute('href', '/search')
  expect(screen.getByText('智能問答').closest('[aria-current="page"]')).not.toBeNull()
})
```

- [ ] **Step 2: 跑測試確認新斷言失敗**

Run: `cd frontend && npm test -- src/features/ask/AskPage.test.tsx`
Expected: 新測試 FAIL（對話態尚無「檢索研報」連結）；既有三測試仍 PASS。

- [ ] **Step 3: 在 `AskPage.tsx` 加對話態頂端列**

Modify `frontend/src/features/ask/AskPage.tsx`：

1. 在 import 區（現第 10 行 `ReportDetailModal` import 後）加：
```tsx
import { ModeSwitch } from '../../components/shell/ModeSwitch'
```
2. 把 `.column` 內容（現第 49–50 行）：
```tsx
      <div className={styles.column}>
        <div className={`${styles.flow} ${turns.length === 0 ? styles.flowCentered : ''} tf-scroll`} ref={flowRef}>
```
改為：
```tsx
      <div className={styles.column}>
        {turns.length > 0 && (
          <div className={styles.modeBar}><ModeSwitch size="sm" /></div>
        )}
        <div className={`${styles.flow} ${turns.length === 0 ? styles.flowCentered : ''} tf-scroll`} ref={flowRef}>
```
（其餘不動；空狀態的切換鈕由 `AskEmptyState` 內的 `ModeSwitch` 負責，避免同頁重複。）

- [ ] **Step 4: `AskPage.module.css` 加 `.modeBar`**

Modify `frontend/src/features/ask/AskPage.module.css`，在 `.composerBar` 之前加：
```css
.modeBar { flex: none; display: flex; justify-content: center; padding: 12px 20px 4px; }
```

- [ ] **Step 5: 跑測試確認全通過**

Run: `cd frontend && npm test -- src/features/ask/AskPage.test.tsx`
Expected: PASS（4 passed）。

- [ ] **Step 6: 型別與全測試**

Run: `cd frontend && npm run typecheck && npm test`
Expected: 全綠。

- [ ] **Step 7: Commit**

```bash
cd frontend && git add src/features/ask/AskPage.tsx src/features/ask/AskPage.module.css src/features/ask/AskPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): 對話進行中頂端常駐檢索↔問答切換鈕

turns > 0 時於訊息流上方（捲動區外）加 modeBar 放 ModeSwitch(sm)，
使問答頁在對話中仍可一鍵切回檢索。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 轉場調和、整合驗證與收尾

**Files:**
- Modify: `frontend/src/components/shell/AppShell.module.css`

**Interfaces:** 無新增。本任務收斂換頁淡入與 VT 的疊加，並做整合驗證。

- [ ] **Step 1: 收斂 `.routeReveal` 與 VT root crossfade 的雙淡入**

Modify `frontend/src/components/shell/AppShell.module.css`，在檔末加：
```css
/* 支援 View Transitions 的瀏覽器：換頁淡入由 VT root crossfade 主導，
   縮短 .routeReveal 以免雙淡入疊加；不支援者維持原 --tf-dur-2 淡入（fallback）。 */
@supports (view-transition-name: none) {
  .routeReveal { animation-duration: var(--tf-dur-1); }
}
```

- [ ] **Step 2: 型別、全測試、正式建置**

Run:
```bash
cd frontend && npm run typecheck && npm test && npm run build
```
Expected: typecheck 無錯；vitest 全綠；`vite build` 成功（`tsc --noEmit && vite build`）。

- [ ] **Step 3: 啟動 dev server 供人工／Playwright 驗證**

Run（背景）：`cd frontend && npm run dev`
記下輸出的本機網址（通常 `http://localhost:5173`）。註：dev server 掛在 `/app` basename，實際入口為 `http://localhost:5173/app/search`。

- [ ] **Step 4: 以 Chromium（支援 VT）煙霧驗證五情境**

用 Playwright 或手動於 Chromium 逐項確認：
1. `/app/search` hero → 點「智能問答」→ 白 thumb 由左滑到右、切換鈕平順移到問答頁，**無空白閃爍、無明顯雙淡入**。
2. `/app/ask` 空狀態 → 點「檢索研報」→ 反向 morph 回檢索 hero。
3. `/app/ask` 送出一題（有 turns）→ 頂端列可見切換鈕；點「檢索研報」可回檢索。
4. DevTools → Rendering → Emulate `prefers-reduced-motion: reduce` → 切換為**即時**、無 morph／crossfade。
5. 首次點擊（未預先 hover）仍**不空白**（驗證 App 閒置預載）。

若情境 1／2 出現無法接受的雙淡入或閃爍，於 `view-transitions.css` 追加 `::view-transition-group(root){ animation-duration: var(--tf-dur-2) }` 微調，並重跑本步驟。

- [ ] **Step 5: 關閉 dev server 並 commit 調和變更**

停掉 dev server。
```bash
cd frontend && git add src/components/shell/AppShell.module.css
git commit -m "$(cat <<'EOF'
fix(前端): 收斂換頁淡入與 View Transition 疊加

支援 VT 的瀏覽器縮短 .routeReveal 淡入，避免與 VT root crossfade 雙淡入；
不支援者維持原淡入作為 fallback。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: 開 Draft PR**

```bash
cd frontend && git push -u origin worktree-feat-ask-mode-switch
gh pr create --draft --base main --title "feat(問答): 常駐檢索↔問答切換鈕＋共享元素轉場" --body "$(cat <<'EOF'
## 摘要
- 問答頁常駐「檢索研報／智能問答」切換鈕（空狀態＋對話進行中）
- 抽共用 `ModeSwitch`，檢索頁 hero 一併改用
- 兩頁切換以 View Transitions API 做 active pill 共享滑動（morph）
- 對向路由 hover/focus＋閒置預載，消除 Suspense 空白閃爍
- reduced-motion 立即切換；不支援 VT 的瀏覽器自動退為即時導覽
- 檢索頁維持 hero-only；後端／資料流零改動

設計文件：`docs/superpowers/specs/2026-07-13-ask-mode-switch-design.md`
實作計畫：`docs/superpowers/plans/2026-07-13-ask-mode-switch.md`

## 驗證
- `npm run typecheck`／`npm test`／`npm run build` 全綠
- Chromium 煙霧五情境（含 reduced-motion）通過

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**1. Spec coverage**（對照 `2026-07-13-ask-mode-switch-design.md`）
- §4.1 ModeSwitch → Task 2 ✔
- §4.2 三處接入（檢索 hero／空狀態／對話頂端）→ Task 3／4／5 ✔
- §5 VT 機制（data-vt 命名、viewTransition、morph 時序）→ Task 2（`view-transitions.css`）＋各接入的 `<Link viewTransition>` ✔
- §5.3 `.routeReveal` 調和 → Task 6 Step 1 ✔
- §6 預載防閃爍 → Task 1（routePreload）＋Task 2（hover/focus 呼叫）＋App 閒置預載 ✔
- §7 reduced-motion＋a11y → Task 2（`@media` 歸零、`aria-current`、真 `<Link>`）✔
- §9 測試策略 → 各 Task 的 TDD 步驟 ✔
- §10 檔案清單 → File Structure 完全對應 ✔
- §2 非目標（檢索 hero-only、側欄不加 VT）→ Global Constraints 明列、未觸及 SideRail／MobileTabBar ✔

**2. Placeholder scan:** 無 TBD／TODO；每個程式步驟均含完整程式碼與可執行指令。Task 6 Step 4 的「若…則微調」為明確的條件式驗證步驟＋具體 CSS，非佔位。

**3. Type consistency:**
- `RouteKey`（`'search'|'ask'|'monitor'`）、`routeLoaders`、`preloadRoute`、`preloadIdle` 在 Task 1 定義，Task 2 `ModeItem` 的 `routeKey: RouteKey` 與 `preloadRoute(routeKey)` 一致；ModeSwitch 測試 mock 亦以 `preloadRoute` 對齊。
- `ModeSwitch` props `{ size?, className? }` 在 Task 2 定義，Task 3/4 用 `className`、Task 5 用 `size="sm"`，一致。
- `Icon`/`IconName` 來源 `components/primitives/Icon`，`name` 值 `search`/`messages` 皆存在。
- `data-vt` 值 `mode-switch`/`mode-thumb` 在 Task 2 元件與全域樣式選擇器兩處一字不差。

無不一致，計畫完成。
