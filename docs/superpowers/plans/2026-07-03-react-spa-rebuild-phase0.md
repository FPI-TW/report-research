# React SPA 重寫 — Phase 0（骨架 + App Shell 導覽 + 登入頁）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立全新 `frontend/` React 19 SPA 的地基——建置/測試工具鏈、設計 token、行為原語、可收合左側欄導覽、後端 `/app` 服務、以及對齊 `.dc.html` 的登入頁；四頁以 placeholder 佔位，`/app/search` 空殼可導覽，vanilla 全程不下線。

**Architecture:** Vite + React 19 + TypeScript，服務於 `/app` 子路徑（FastAPI catch-all shell + immutable 資產）。零元件庫：Popover/Menu/Modal 與 focus-trap 全自製。樣式＝`tokens.css`（CSS 變數）+ per-component CSS Modules，動態值走 inline。Server state 用 TanStack Query，UI state 用 react-router + local state。

**Tech Stack:** React 19.2 / react-router 8 / @tanstack/react-query 5 / zod 4 / Vite 8 / Vitest 4 / @testing-library/react / Playwright / TypeScript / ESLint。

## Global Constraints

（每個 Task 的要求都隱含包含本節；值逐字取自 spec）

- **設計權威**：`docs/design/廷豐智能研報.dc.html` 為唯一權威；衝突以 `.dc.html` 為準。
- **版本下限**：React `19.2.x`、react-router `8`、@tanstack/react-query `5`、zod `4`、Vite `8`、Vitest `4`、TypeScript `6`、Node `>=22.22`。
- **不引入**：`@mantine/*`、`recharts`、任何圖表庫、`tailwind`、`react-hook-form`。行為原語與圖示自製。
- **樣式**：`frontend/src/styles/tokens.css` 落 CSS 變數；per-component `*.module.css`；`.dc.html` 的 `style-hover`/`style-focus`/`@media` 一律落成 CSS `:hover`/`:focus-visible`/`@media`。
- **金色規則**：金色文字一律 `#8a5a0f`；`#ae7415` 僅 ≥18.66px 粗體或裝飾底。
- **a11y 硬規則**：焦點環 `outline:2px solid #8a5a0f;outline-offset:2px` 永不移除；導覽 active 帶 `aria-current="page"`；手機底欄與帳號格觸控目標 ≥44px；每頁唯一視覺隱藏 `<h1>廷豐智能研報`；Popover 觸發鈕 `aria-expanded`；`prefers-reduced-motion` 動效歸零。
- **市場代碼與語意色**（不得改，取自 `.dc.html` MARKETS）：TW `#34c759` 台股、US `#007aff` 美股、HK `#ff9500` 港股、CN `#ff3b30` 陸股、FX `#00c7be` 外匯、WTX `#af52de` 台指期、MACRO `#ff2d55` 總經、GLOBAL `#5856d6` 全球、CRYPTO `#a2845e` 加密、fallback `#8e8e93`。膠囊順序：ALL, TW, US, HK, CN, WTX, FX, MACRO, GLOBAL, CRYPTO。
- **後端**：`/api/*`、`/login`、`/logout` 契約與邏輯零改動；允許變更僅本 Phase 的 `/app` 服務（catch-all shell + `/app/assets` immutable）。middleware 不動：未登入 `/app/*` → 302 `/login`。
- **建置整合**：Vite `base:'/app/'`；dev proxy `/api`、`/login`、`/logout` → `http://localhost:8097`。
- **e2e**：一律對工作樹起的 `:8098` 跑（非正式 `:8097`）；殺埠前 `ss` 核對。
- **測試指令**：前端在 `frontend/` 下 `npm run test` / `npm run build` / `npm run lint`；後端 `uv run pytest`。RTK 會遮 vitest 非零 exit → 用 `rtk proxy npm run test` 或直接 `./node_modules/.bin/vitest run` 確認 exit code。

---

## File Structure

```
frontend/
  package.json                         # 相依與 scripts
  vite.config.ts                       # base:/app/、dev proxy
  vitest.config.ts                     # jsdom、setup、include
  tsconfig.json  tsconfig.node.json    # TS 設定
  eslint.config.js                     # flat config
  index.html                           # SPA 入口（#root）
  .gitignore                           # node_modules dist
  src/
    main.tsx                           # createRoot + QueryClientProvider + RouterProvider
    App.tsx                            # router（basename /app）+ RootLayout + 路由表
    vite-env.d.ts
    test/setup.ts                      # @testing-library/jest-dom + matchMedia polyfill
    styles/
      tokens.css                       # 設計 token（CSS 變數）+ reset + keyframes
    lib/
      meta.ts                          # 市場/商品語意色與標籤（純函式）
      api.ts                           # getJSON / ApiError / redirectToLogin
      schemas.ts                       # statsSchema / conversationSummarySchema
      useMediaQuery.ts                 # 響應式 hook
      useClickOutside.ts               # 點擊外部關閉
      useFocusTrap.ts                  # modal/popover 焦點鎖
      useSidebarCollapsed.ts           # 側欄收合狀態（localStorage）
      useStats.ts                      # /api/stats query（username 等）
      useConversations.ts              # /api/conversations query
    components/
      primitives/
        Icon.tsx  Icon.module.css      # Tabler 風 SVG 圖示集
        Popover.tsx  Popover.module.css
        Menu.tsx
      shell/
        AppShell.tsx  AppShell.module.css
        SideRail.tsx  SideRail.module.css
        NavItem.tsx  NavItem.module.css
        ConversationList.tsx  ConversationList.module.css
        AccountMenu.tsx  AccountMenu.module.css
        MobileTabBar.tsx  MobileTabBar.module.css
    features/
      search/SearchPage.tsx            # Phase 0 placeholder（Phase 1 實作）
      ask/AskPage.tsx                  # Phase 0 placeholder（Phase 2 實作）
      monitor/MonitorPage.tsx          # Phase 0 placeholder（Phase 3 實作）
  e2e/
    shell.spec.ts                      # Playwright 冒煙（:8098）
web/
  server.py                            # 加回 /app 服務（Task 14）
  static/login.html                    # 改寫對齊 .dc.html（Task 15）
```

---

## Task 1: Scaffold frontend（工具鏈 + 冒煙測試）

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/eslint.config.js`, `frontend/index.html`, `frontend/.gitignore`, `frontend/src/vite-env.d.ts`, `frontend/src/test/setup.ts`, `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `App` default export（Phase 0 暫為最小殼）；`npm run build|lint|test` 三指令。

- [ ] **Step 1: 建立 `frontend/package.json`**

```json
{
  "name": "report-mark-frontend",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22.22.0" },
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run"
  },
  "dependencies": {
    "@tanstack/react-query": "5.101.2",
    "react": "19.2.7",
    "react-dom": "19.2.7",
    "react-router": "8.0.1",
    "zod": "4.4.3"
  },
  "devDependencies": {
    "@eslint/js": "10.0.1",
    "@playwright/test": "1.61.1",
    "@testing-library/dom": "10.4.1",
    "@testing-library/jest-dom": "6.9.1",
    "@testing-library/react": "16.3.2",
    "@testing-library/user-event": "14.6.1",
    "@types/react": "19.2.2",
    "@types/react-dom": "19.2.1",
    "@vitejs/plugin-react": "6.0.3",
    "eslint": "10.6.0",
    "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-react-refresh": "0.5.3",
    "globals": "16.4.0",
    "jsdom": "29.1.1",
    "typescript": "6.0.3",
    "typescript-eslint": "8.62.0",
    "vite": "8.1.0",
    "vitest": "4.1.9"
  }
}
```

- [ ] **Step 2: 建立設定檔**

`frontend/vite.config.ts`:
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8097',
      '/login': 'http://localhost:8097',
      '/logout': 'http://localhost:8097',
    },
  },
})
```

`frontend/vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
  },
})
```

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

`frontend/eslint.config.js`:
```js
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
)
```

- [ ] **Step 3: 建立入口與型別**

`frontend/index.html`:
```html
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>廷豐智能研報</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/.gitignore`:
```
node_modules
dist
*.local
.vite
```

`frontend/src/vite-env.d.ts`:
```ts
/// <reference types="vite/client" />
```

`frontend/src/test/setup.ts`:
```ts
import '@testing-library/jest-dom/vitest'

// jsdom 未實作 matchMedia：提供最小 polyfill 供 useMediaQuery 測試
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList
}
```

- [ ] **Step 4: 建立最小 `main.tsx` 與 `App.tsx`**

`frontend/src/main.tsx`:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

`frontend/src/App.tsx`:
```tsx
export default function App() {
  return <div>廷豐智能研報</div>
}
```

- [ ] **Step 5: 寫冒煙測試（先失敗）**

`frontend/src/App.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react'
import App from './App'

test('renders brand text', () => {
  render(<App />)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
})
```

- [ ] **Step 6: 安裝相依並跑測試**

Run: `cd frontend && npm install && ./node_modules/.bin/vitest run`
Expected: 1 passed（`App.test.tsx`）。

- [ ] **Step 7: 驗證 build / lint 綠**

Run: `cd frontend && npm run build && npm run lint`
Expected: build 產出 `dist/`（資產在 `dist/assets/`）；lint 0 error。

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/vitest.config.ts frontend/tsconfig.json frontend/tsconfig.node.json frontend/eslint.config.js frontend/index.html frontend/.gitignore frontend/src/vite-env.d.ts frontend/src/test/setup.ts frontend/src/main.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(frontend): scaffold Vite + React 19 + TS + Vitest 工具鏈"
```

---

## Task 2: 設計 token（`tokens.css`）

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/main.tsx`（import tokens.css）

**Interfaces:**
- Produces: CSS 變數（`--tf-*`）、全域 reset、keyframes（`tf-pulse`/`tf-indet`/`tf-spin`/`tf-up`）、`prefers-reduced-motion` 歸零。無單元測試（純 CSS 資產；視覺於 e2e/後續驗證）。

- [ ] **Step 1: 建立 `frontend/src/styles/tokens.css`**（值逐一取自 `.dc.html` 與 spec §3）

```css
:root {
  /* 介面基調 */
  --tf-canvas: #f6f7f9;
  --tf-surface: #ffffff;
  --tf-sidebar: #fbfbfc;
  --tf-border: #e4e7ec;
  --tf-border-weak: #f2f4f7;
  --tf-divider: #eceef1;
  --tf-text-1: #101828;
  --tf-text-2: #344054;
  --tf-text-3: #667085;
  --tf-text-4: #98a2b3;

  /* 品牌金 */
  --tf-gold-text: #8a5a0f;
  --tf-gold-strong: #ae7415;
  --tf-gold-hover: #7a4f0c;
  --tf-gold-tint: #faf3e3;
  --tf-on-gold: #ffffff;
  --tf-on-gold-muted: rgba(255, 255, 255, 0.75);

  /* 字型 */
  --tf-serif: 'Noto Serif TC', Georgia, 'Times New Roman', serif;
  --tf-sans: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI',
    'PingFang TC', 'Microsoft JhengHei', system-ui, sans-serif;
  --tf-mono: ui-monospace, Menlo, monospace;

  /* 圓角 */
  --tf-radius-card: 12px;
  --tf-radius-pill: 999px;
  --tf-radius-seg: 10px;
  --tf-radius-md: 8px;

  /* 陰影 */
  --tf-shadow-xs: 0 1px 3px rgba(16, 24, 40, 0.06);
  --tf-shadow-seg: 0 1px 3px rgba(16, 24, 40, 0.12);
  --tf-shadow-float: 0 4px 16px rgba(16, 24, 40, 0.08);
  --tf-shadow-viewtoggle: 0 6px 20px rgba(16, 24, 40, 0.14);
  --tf-shadow-pop: 0 12px 32px rgba(16, 24, 40, 0.16);
  --tf-shadow-modal: 0 20px 48px rgba(16, 24, 40, 0.24);

  /* 動效 */
  --tf-ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --tf-dur-1: 120ms;
  --tf-dur-2: 180ms;
  --tf-dur-3: 240ms;
  --tf-dur-4: 340ms;

  /* 語意色 — 市場 */
  --mkt-TW: #34c759;
  --mkt-US: #007aff;
  --mkt-HK: #ff9500;
  --mkt-CN: #ff3b30;
  --mkt-FX: #00c7be;
  --mkt-WTX: #af52de;
  --mkt-MACRO: #ff2d55;
  --mkt-GLOBAL: #5856d6;
  --mkt-CRYPTO: #a2845e;
  --mkt-fallback: #8e8e93;

  /* 狀態色 */
  --tf-success: #34c759;
  --tf-success-text: #248a3d;
  --tf-warn: #ff9f0a;
  --tf-error: #ff3b30;
  --tf-error-bg: #fef3f2;
  --tf-error-border: #fecdca;
  --tf-error-text: #b42318;
  --tf-mark: #fff3bf;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--tf-canvas);
  font-family: var(--tf-sans);
  color: var(--tf-text-1);
  -webkit-font-smoothing: antialiased;
}
input, button, textarea { font-family: inherit; }
::selection { background: var(--tf-gold-tint); }

:focus-visible {
  outline: 2px solid var(--tf-gold-text);
  outline-offset: 2px;
}

.tf-scroll { overflow-x: hidden; }
.tf-scroll::-webkit-scrollbar { width: 10px; height: 10px; }
.tf-scroll::-webkit-scrollbar-thumb {
  background: var(--tf-border);
  border-radius: 999px;
  border: 3px solid var(--tf-canvas);
}

@keyframes tf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
@keyframes tf-indet { 0% { left: -40%; } 100% { left: 100%; } }
@keyframes tf-spin { to { transform: rotate(360deg); } }
@keyframes tf-up { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }
}
```

- [ ] **Step 2: 在 `main.tsx` 匯入 token 與 Noto Serif TC**

`frontend/src/main.tsx`（在最上方加）:
```tsx
import './styles/tokens.css'
```

並在 `frontend/index.html` `<head>` 加字型（`display=swap`，不阻塞）:
```html
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@600;700;800&display=swap" rel="stylesheet" />
```

- [ ] **Step 3: 驗證 build 綠（CSS 被打包）**

Run: `cd frontend && npm run build`
Expected: build 成功；`dist/assets/*.css` 含 `--tf-canvas`。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/styles/tokens.css frontend/src/main.tsx frontend/index.html
git commit -m "feat(frontend): 設計 token（tokens.css）+ Noto Serif TC 載入"
```

---

## Task 3: `lib/meta.ts`（市場/商品語意色）

**Files:**
- Create: `frontend/src/lib/meta.ts`
- Test: `frontend/src/lib/meta.test.ts`

**Interfaces:**
- Produces:
  - `marketColor(code: string): string`
  - `marketLabel(code: string): string`
  - `ptypeColor(name: string): string`
  - `MARKET_ORDER: readonly string[]`（膠囊順序，不含 ALL）

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/meta.test.ts`:
```ts
import { marketColor, marketLabel, ptypeColor, MARKET_ORDER } from './meta'

test('市場色與標籤取自 .dc.html', () => {
  expect(marketColor('TW')).toBe('#34c759')
  expect(marketLabel('TW')).toBe('台股')
  expect(marketColor('WTX')).toBe('#af52de')
  expect(marketLabel('CRYPTO')).toBe('加密')
})

test('未知市場回 fallback 色、原字串標籤', () => {
  expect(marketColor('ZZ')).toBe('#8e8e93')
  expect(marketLabel('ZZ')).toBe('ZZ')
})

test('商品類型色與 fallback', () => {
  expect(ptypeColor('股票')).toBe('#0a84ff')
  expect(ptypeColor('不存在')).toBe('#8e8e93')
})

test('膠囊市場順序', () => {
  expect(MARKET_ORDER).toEqual(['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'])
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/meta.test.ts`
Expected: FAIL（`meta.ts` 不存在）。

- [ ] **Step 3: 實作 `frontend/src/lib/meta.ts`**

```ts
interface MarketMeta { label: string; color: string }

const MARKETS: Record<string, MarketMeta> = {
  TW: { label: '台股', color: '#34c759' },
  US: { label: '美股', color: '#007aff' },
  HK: { label: '港股', color: '#ff9500' },
  CN: { label: '陸股', color: '#ff3b30' },
  FX: { label: '外匯', color: '#00c7be' },
  WTX: { label: '台指期', color: '#af52de' },
  MACRO: { label: '總經', color: '#ff2d55' },
  GLOBAL: { label: '全球', color: '#5856d6' },
  CRYPTO: { label: '加密', color: '#a2845e' },
}

const PTYPE: Record<string, string> = {
  股票: '#0a84ff', 指數: '#5e5ce6', 期貨: '#ff9f0a', 選擇權: '#bf5af2',
  ETF: '#30d158', 債券: '#0bb8c4', 外匯: '#00c7be', 原物料: '#ac8e68', 加密: '#e0a400',
}

const FALLBACK = '#8e8e93'

export const MARKET_ORDER = ['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'] as const

export function marketColor(code: string): string {
  return MARKETS[code]?.color ?? FALLBACK
}
export function marketLabel(code: string): string {
  return MARKETS[code]?.label ?? code
}
export function ptypeColor(name: string): string {
  return PTYPE[name] ?? FALLBACK
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/meta.test.ts`
Expected: 4 passed。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/meta.ts frontend/src/lib/meta.test.ts
git commit -m "feat(frontend): 市場/商品語意色 meta.ts（取自 .dc.html）"
```

---

## Task 4: `lib/api.ts` + `lib/schemas.ts`（HTTP + Zod）

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/schemas.ts`
- Test: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces:
  - `class ApiError extends Error { status: number }`
  - `redirectToLogin(): void`
  - `getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T>`
  - `statsSchema` / `StatsResponse`；`conversationSummarySchema` / `ConversationSummary`

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/api.test.ts`:
```ts
import { z } from 'zod'
import { afterEach, expect, test, vi } from 'vitest'
import { ApiError, getJSON } from './api'

afterEach(() => vi.unstubAllGlobals())

const schema = z.object({ ok: z.boolean() })

test('200 回 parsed 物件', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })))
  await expect(getJSON('/x', schema)).resolves.toEqual({ ok: true })
})

test('401 導向登入並丟 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/app/search', search: '', assign } as unknown as Location)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })))
  await expect(getJSON('/x', schema)).rejects.toBeInstanceOf(ApiError)
  expect(assign).toHaveBeenCalledWith('/login?next=' + encodeURIComponent('/app/search'))
})

test('500 丟 ApiError 帶 status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 500 })))
  await expect(getJSON('/x', schema)).rejects.toMatchObject({ status: 500 })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/api.test.ts`
Expected: FAIL（`api.ts` 不存在）。

- [ ] **Step 3: 實作 `frontend/src/lib/api.ts`**

```ts
import type { ZodType } from 'zod'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** session 過期/未登入時導向登入頁，帶上目前 SPA 路徑供登入後返回。 */
export function redirectToLogin(): void {
  const next = location.pathname + location.search
  location.assign('/login?next=' + encodeURIComponent(next))
}

export async function getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { ...init, credentials: 'same-origin' })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok) throw new ApiError(resp.status, `HTTP ${resp.status}`)
  return schema.parse(await resp.json())
}
```

- [ ] **Step 4: 實作 `frontend/src/lib/schemas.ts`**（欄位對齊後端 `web/server.py` 回應）

```ts
import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number().int().nonnegative(),
  total_chunks: z.number().int().nonnegative(),
  markets: z.array(z.object({ market: z.string(), count: z.number().int().nonnegative() })),
  instrument_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  report_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  username: z.string().nullish(),
})
export type StatsResponse = z.infer<typeof statsSchema>

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/api.test.ts`
Expected: 3 passed。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/schemas.ts frontend/src/lib/api.test.ts
git commit -m "feat(frontend): getJSON/ApiError/redirectToLogin + stats/conversation schema"
```

---

## Task 5: `primitives/Icon`（Tabler 風 SVG 圖示集）

**Files:**
- Create: `frontend/src/components/primitives/Icon.tsx`
- Test: `frontend/src/components/primitives/Icon.test.tsx`

**Interfaces:**
- Produces: `Icon({ name, size, className, 'aria-hidden' }): JSX`；`name` 型別 `IconName = 'search'|'messages'|'activity'|'user'|'plus'|'panel'|'logout'|'chevronDown'|'x'`。`size` 預設 20、`stroke-width` 1.8。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/primitives/Icon.test.tsx`:
```tsx
import { render } from '@testing-library/react'
import { Icon } from './Icon'

test('渲染指定 name 的 svg，套用 size 與 stroke', () => {
  const { container } = render(<Icon name="search" size={18} />)
  const svg = container.querySelector('svg')!
  expect(svg).toBeInTheDocument()
  expect(svg.getAttribute('width')).toBe('18')
  expect(svg.getAttribute('stroke-width')).toBe('1.8')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Icon.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `frontend/src/components/primitives/Icon.tsx`**（path 取自 `.dc.html`）

```tsx
import type { ReactNode, SVGProps } from 'react'

export type IconName =
  | 'search' | 'messages' | 'activity' | 'user' | 'plus'
  | 'panel' | 'logout' | 'chevronDown' | 'x'

const PATHS: Record<IconName, ReactNode> = {
  search: (<><circle cx="10" cy="10" r="7" /><path d="M21 21l-6 -6" /></>),
  messages: (<><path d="M8 9h8" /><path d="M8 13h5" /><path d="M18 4a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-5l-5 3v-3h-2a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3z" /></>),
  activity: (<path d="M3 12h4l3 8l4 -16l3 8h4" />),
  user: (<><circle cx="12" cy="8" r="4" /><path d="M6 21v-1a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v1" /></>),
  plus: (<path d="M12 5v14M5 12h14" />),
  panel: (<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>),
  logout: (<><path d="M14 8V6a2 2 0 0 0 -2 -2H6a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h6a2 2 0 0 0 2 -2v-2" /><path d="M9 12h12l-3 -3M18 15l3 -3" /></>),
  chevronDown: (<path d="M6 9l6 6l6 -6" />),
  x: (<path d="M18 6l-12 12M6 6l12 12" />),
}

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 20, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}
```

> 註：圖示以 React 子節點渲染（非 `dangerouslySetInnerHTML`），路徑為本檔靜態常數。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Icon.test.tsx`
Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/primitives/Icon.tsx frontend/src/components/primitives/Icon.test.tsx
git commit -m "feat(frontend): Tabler 風 SVG Icon 原語"
```

---

## Task 6: hooks（`useMediaQuery` / `useClickOutside` / `useFocusTrap`）

**Files:**
- Create: `frontend/src/lib/useMediaQuery.ts`, `frontend/src/lib/useClickOutside.ts`, `frontend/src/lib/useFocusTrap.ts`
- Test: `frontend/src/lib/useMediaQuery.test.ts`, `frontend/src/lib/useClickOutside.test.tsx`

**Interfaces:**
- Produces:
  - `useMediaQuery(query: string): boolean`
  - `useClickOutside(ref: RefObject<HTMLElement | null>, onOutside: () => void): void`
  - `useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean): void`

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/useMediaQuery.test.ts`:
```ts
import { renderHook } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useMediaQuery } from './useMediaQuery'

afterEach(() => vi.unstubAllGlobals())

test('回傳 matchMedia 的 matches', () => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {},
  }))
  const { result } = renderHook(() => useMediaQuery('(max-width: 767px)'))
  expect(result.current).toBe(true)
})
```

`frontend/src/lib/useClickOutside.test.tsx`:
```tsx
import { useRef } from 'react'
import { fireEvent, render } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { useClickOutside } from './useClickOutside'

function Harness({ onOutside }: { onOutside: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onOutside)
  return (
    <div>
      <div ref={ref} data-testid="inside">inside</div>
      <button data-testid="outside">outside</button>
    </div>
  )
}

test('點 ref 外觸發、點內不觸發', () => {
  const cb = vi.fn()
  const { getByTestId } = render(<Harness onOutside={cb} />)
  fireEvent.mouseDown(getByTestId('inside'))
  expect(cb).not.toHaveBeenCalled()
  fireEvent.mouseDown(getByTestId('outside'))
  expect(cb).toHaveBeenCalledTimes(1)
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/useMediaQuery.test.ts src/lib/useClickOutside.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作三個 hook**

`frontend/src/lib/useMediaQuery.ts`:
```ts
import { useEffect, useState } from 'react'

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia(query).matches
      : false,
  )
  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])
  return matches
}
```

`frontend/src/lib/useClickOutside.ts`:
```ts
import { useEffect, type RefObject } from 'react'

export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onOutside: () => void,
): void {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const el = ref.current
      if (el && !el.contains(e.target as Node)) onOutside()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [ref, onOutside])
}
```

`frontend/src/lib/useFocusTrap.ts`:
```ts
import { useEffect, type RefObject } from 'react'

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean): void {
  useEffect(() => {
    if (!active) return
    const el = ref.current
    if (!el) return
    const nodes = () => Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE))
    nodes()[0]?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const items = nodes()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus()
      }
    }
    el.addEventListener('keydown', onKey)
    return () => el.removeEventListener('keydown', onKey)
  }, [ref, active])
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/useMediaQuery.test.ts src/lib/useClickOutside.test.tsx`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/useMediaQuery.ts frontend/src/lib/useClickOutside.ts frontend/src/lib/useFocusTrap.ts frontend/src/lib/useMediaQuery.test.ts frontend/src/lib/useClickOutside.test.tsx
git commit -m "feat(frontend): useMediaQuery/useClickOutside/useFocusTrap hooks"
```

---

## Task 7: `primitives/Popover` + `Menu`

**Files:**
- Create: `frontend/src/components/primitives/Popover.tsx`, `frontend/src/components/primitives/Popover.module.css`, `frontend/src/components/primitives/Menu.tsx`
- Test: `frontend/src/components/primitives/Popover.test.tsx`

**Interfaces:**
- Consumes: `useClickOutside`, `useFocusTrap`。
- Produces:
  - `Popover({ open, onClose, children, className }): JSX`（open 時渲染浮層 + 全屏遮罩層攔截外部點擊 + Esc 關閉 + focus trap）
  - `Menu` / `MenuItem`（語意包裝，供帳號選單用）

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/primitives/Popover.test.tsx`:
```tsx
import { useRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Popover } from './Popover'

function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
  const anchor = useRef<HTMLDivElement>(null)
  return (
    <div ref={anchor}>
      <Popover open={open} onClose={onClose}>
        <button>登出</button>
      </Popover>
    </div>
  )
}

test('open=false 不渲染內容', () => {
  render(<Harness open={false} onClose={() => {}} />)
  expect(screen.queryByText('登出')).not.toBeInTheDocument()
})

test('open=true 渲染內容；Esc 觸發 onClose', () => {
  const onClose = vi.fn()
  render(<Harness open onClose={onClose} />)
  expect(screen.getByText('登出')).toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Popover.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `Popover.tsx`**

```tsx
import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import styles from './Popover.module.css'

interface PopoverProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  className?: string
}

export function Popover({ open, onClose, children, className }: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFocusTrap(panelRef, open)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} className={`${styles.panel} ${className ?? ''}`} role="menu">
        {children}
      </div>
    </>
  )
}
```

`frontend/src/components/primitives/Popover.module.css`:
```css
.scrim { position: fixed; inset: 0; z-index: 35; }
.panel {
  position: absolute;
  z-index: 40;
  background: var(--tf-surface);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-card);
  box-shadow: var(--tf-shadow-pop);
  padding: 8px;
}
```

- [ ] **Step 4: 實作 `Menu.tsx`**

```tsx
import type { ReactNode } from 'react'

export function MenuItem({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      style={{
        width: '100%', textAlign: 'left', border: 'none', background: 'none',
        fontSize: 13, color: 'var(--tf-text-2)', padding: '8px 10px',
        borderRadius: 8, cursor: 'pointer', fontFamily: 'inherit',
      }}
    >
      {children}
    </button>
  )
}
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Popover.test.tsx`
Expected: 2 passed。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/primitives/Popover.tsx frontend/src/components/primitives/Popover.module.css frontend/src/components/primitives/Menu.tsx frontend/src/components/primitives/Popover.test.tsx
git commit -m "feat(frontend): Popover/Menu 原語（遮罩+Esc+focus trap）"
```

---

## Task 8: `shell/NavItem`

**Files:**
- Create: `frontend/src/components/shell/NavItem.tsx`, `frontend/src/components/shell/NavItem.module.css`
- Test: `frontend/src/components/shell/NavItem.test.tsx`

**Interfaces:**
- Consumes: `Icon`（`IconName`）、react-router `useLocation`。
- Produces: `NavItem({ to, icon, label, variant }): JSX`，`variant: 'mini' | 'row' | 'mobile'`；active 判定＝`location.pathname === to`（含 basename 後為 `/search` 等）；active 帶 `aria-current="page"`。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/shell/NavItem.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { NavItem } from './NavItem'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <NavItem to="/search" icon="search" label="檢索" variant="row" />
    </MemoryRouter>,
  )
}

test('在對應路徑時標記 aria-current=page', () => {
  renderAt('/search')
  expect(screen.getByRole('link', { name: /檢索/ })).toHaveAttribute('aria-current', 'page')
})

test('不在對應路徑時無 aria-current', () => {
  renderAt('/ask')
  expect(screen.getByRole('link', { name: /檢索/ })).not.toHaveAttribute('aria-current')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/NavItem.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `NavItem.tsx`**

```tsx
import { Link, useLocation } from 'react-router'
import { Icon, type IconName } from '../primitives/Icon'
import styles from './NavItem.module.css'

interface NavItemProps {
  to: string
  icon: IconName
  label: string
  variant: 'mini' | 'row' | 'mobile'
}

export function NavItem({ to, icon, label, variant }: NavItemProps) {
  const { pathname } = useLocation()
  const active = pathname === to
  return (
    <Link
      to={to}
      title={variant === 'mini' ? label : undefined}
      aria-current={active ? 'page' : undefined}
      className={`${styles[variant]} ${active ? styles.active : ''}`}
    >
      <Icon name={icon} size={variant === 'mobile' ? 21 : variant === 'row' ? 19 : 20} />
      {variant !== 'mini' && <span className={styles.label}>{label}</span>}
    </Link>
  )
}
```

`frontend/src/components/shell/NavItem.module.css`:
```css
.mini, .row, .mobile {
  display: flex; align-items: center; cursor: pointer;
  border: none; background: none; text-decoration: none;
  color: var(--tf-text-3); font-family: inherit;
}
.mini { flex-direction: column; gap: 3px; padding: 8px 9px; border-radius: var(--tf-radius-seg); min-width: 46px; justify-content: center; }
.row { gap: 10px; padding: 8px 10px; border-radius: 8px; width: 100%; font-size: 13.5px; color: var(--tf-text-2); }
.mobile { flex: 1; flex-direction: column; gap: 3px; padding: 6px 0; justify-content: center; }
.label { font-size: 10px; line-height: 1; }
.row .label { font-size: 13.5px; }

.mini:hover, .row:hover { background: var(--tf-border-weak); }

.active { color: var(--tf-gold-text); font-weight: 700; }
.mini.active, .row.active { background: var(--tf-gold-tint); }
.mobile.active { background: none; }
.row.active { font-weight: 600; }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/NavItem.test.tsx`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shell/NavItem.tsx frontend/src/components/shell/NavItem.module.css frontend/src/components/shell/NavItem.test.tsx
git commit -m "feat(frontend): NavItem（mini/row/mobile 三型，active 金膠囊+aria-current）"
```

---

## Task 9: `shell/SideRail` + `useSidebarCollapsed`

**Files:**
- Create: `frontend/src/lib/useSidebarCollapsed.ts`, `frontend/src/components/shell/SideRail.tsx`, `frontend/src/components/shell/SideRail.module.css`
- Test: `frontend/src/lib/useSidebarCollapsed.test.tsx`, `frontend/src/components/shell/SideRail.test.tsx`

**Interfaces:**
- Consumes: `NavItem`, `Icon`, `AccountMenu`（Task 11）, `ConversationList`（Task 10）。
- Produces:
  - `useSidebarCollapsed(): { collapsed: boolean; toggle: () => void }`（localStorage key `tf.sidebar.collapsed`）
  - `SideRail({ collapsed, onToggle }): JSX`（collapsed→迷你 60；否則→完整 272）

> **前置（執行順序）**：本 Task 須在 **Task 10（ConversationList）與 Task 11（AccountMenu）之後**執行——`SideRail` 直接 import 兩者的真實實作，故其測試需掛 `QueryClientProvider` 並 stub `fetch`。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/useSidebarCollapsed.test.tsx`:
```tsx
import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'
import { useSidebarCollapsed } from './useSidebarCollapsed'

beforeEach(() => localStorage.clear())

test('預設展開；toggle 後收合並寫入 localStorage', () => {
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(false)
  act(() => result.current.toggle())
  expect(result.current.collapsed).toBe(true)
  expect(localStorage.getItem('tf.sidebar.collapsed')).toBe('1')
})
```

`frontend/src/components/shell/SideRail.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { SideRail } from './SideRail'

afterEach(() => vi.unstubAllGlobals())

function renderRail(collapsed: boolean) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/conversations')
      ? new Response(JSON.stringify([]), { status: 200 })
      : new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 }),
  ))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}>
        <SideRail collapsed={collapsed} onToggle={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('展開態顯示站名與三導覽 label', () => {
  renderRail(false)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})

test('收合態不顯示站名文字（僅 glyph）', () => {
  renderRail(true)
  expect(screen.queryByText('廷豐智能研報')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/useSidebarCollapsed.test.tsx src/components/shell/SideRail.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `useSidebarCollapsed.ts`**

```ts
import { useCallback, useState } from 'react'

const KEY = 'tf.sidebar.collapsed'

export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY) === '1' } catch { return false }
  })
  const toggle = useCallback(() => {
    setCollapsed((c) => {
      const next = !c
      try { localStorage.setItem(KEY, next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }, [])
  return { collapsed, toggle }
}
```

- [ ] **Step 4: 實作 `SideRail.tsx`**（先接佔位子元件）

```tsx
import { Link } from 'react-router'
import { Icon } from '../primitives/Icon'
import { NavItem } from './NavItem'
import { ConversationList } from './ConversationList'
import { AccountMenu } from './AccountMenu'
import styles from './SideRail.module.css'

interface SideRailProps {
  collapsed: boolean
  onToggle: () => void
}

export function SideRail({ collapsed, onToggle }: SideRailProps) {
  if (collapsed) {
    return (
      <div className={styles.mini}>
        <Link to="/search" title="廷豐智能研報" className={styles.glyph}>廷</Link>
        <button type="button" onClick={onToggle} title="展開側欄" className={styles.toggleMini}>
          <Icon name="panel" size={17} />
        </button>
        <nav className={styles.miniNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="mini" />
          <NavItem to="/ask" icon="messages" label="問答" variant="mini" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="mini" />
        </nav>
        <div className={styles.spacer} />
        <AccountMenu variant="mini" />
      </div>
    )
  }
  return (
    <div className={styles.full}>
      <div className={styles.header}>
        <span className={styles.glyphSmall}>廷</span>
        <span className={styles.title}>廷豐智能研報</span>
        <button type="button" onClick={onToggle} title="收合側欄" className={styles.toggleFull}>
          <Icon name="panel" size={17} />
        </button>
      </div>
      <nav className={styles.fullNav}>
        <NavItem to="/search" icon="search" label="檢索" variant="row" />
        <NavItem to="/ask" icon="messages" label="問答" variant="row" />
        <NavItem to="/monitor" icon="activity" label="監控" variant="row" />
      </nav>
      <div className={styles.divider} />
      <ConversationList />
      <AccountMenu variant="row" />
    </div>
  )
}
```

`frontend/src/components/shell/SideRail.module.css`:
```css
.mini {
  width: 60px; flex: none; height: 100%;
  background: var(--tf-sidebar); border-right: 1px solid var(--tf-border);
  display: flex; flex-direction: column; align-items: center; padding: 14px 0;
}
.full {
  width: 272px; flex: none; height: 100%;
  background: var(--tf-sidebar); border-right: 1px solid var(--tf-border);
  display: flex; flex-direction: column;
}
.glyph { font-family: var(--tf-serif); font-weight: 800; font-size: 22px; color: var(--tf-gold-text); line-height: 1; padding: 2px 6px; text-decoration: none; }
.glyphSmall { font-family: var(--tf-serif); font-weight: 800; font-size: 22px; color: var(--tf-gold-text); line-height: 1; padding-left: 4px; }
.toggleMini { margin-top: 14px; border: 1px solid var(--tf-border); background: #fff; border-radius: 8px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--tf-text-3); padding: 0; }
.toggleMini:hover { border-color: var(--tf-gold-text); color: var(--tf-gold-text); }
.miniNav { display: flex; flex-direction: column; gap: 6px; margin-top: 18px; }
.spacer { flex: 1; }
.header { padding: 16px 10px 12px; display: flex; align-items: center; gap: 8px; }
.title { flex: 1; font-family: var(--tf-serif); font-weight: 700; font-size: 15px; color: var(--tf-text-1); }
.toggleFull { border: none; background: none; border-radius: 8px; width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--tf-text-4); padding: 0; flex: none; }
.toggleFull:hover { background: var(--tf-border-weak); color: var(--tf-text-3); }
.fullNav { padding: 0 10px; display: flex; flex-direction: column; gap: 2px; }
.divider { height: 1px; background: var(--tf-divider); margin: 12px 12px 8px; }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/useSidebarCollapsed.test.tsx src/components/shell/SideRail.test.tsx`
Expected: 3 passed。（前置：Task 10、11 已完成，`SideRail` import 其真實實作。）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/useSidebarCollapsed.ts frontend/src/lib/useSidebarCollapsed.test.tsx frontend/src/components/shell/SideRail.tsx frontend/src/components/shell/SideRail.module.css frontend/src/components/shell/SideRail.test.tsx
git commit -m "feat(frontend): SideRail（迷你60↔完整272）+ 收合狀態持久化"
```

---

## Task 10: `shell/ConversationList` + `useConversations`

**Files:**
- Create: `frontend/src/lib/useConversations.ts`, `frontend/src/components/shell/ConversationList.tsx`, `frontend/src/components/shell/ConversationList.module.css`
- Test: `frontend/src/components/shell/ConversationList.test.tsx`

**Interfaces:**
- Consumes: `getJSON`, `conversationSummarySchema`, `Icon`, react-router `Link`, `@tanstack/react-query`。
- Produces:
  - `useConversations(): UseQueryResult<ConversationSummary[]>`（`GET /api/conversations?limit=50`）
  - `ConversationList(): JSX`（`新對話`→`/ask`；每筆→`/ask?c=<id>`）

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/shell/ConversationList.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { ConversationList } from './ConversationList'

afterEach(() => vi.unstubAllGlobals())

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

test('渲染新對話鈕與對話清單', async () => {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify([
      { conversation_id: 'c1', title: 'AI 伺服器供應鏈' },
    ]), { status: 200 }),
  ))
  wrap(<ConversationList />)
  expect(screen.getByRole('link', { name: '新對話' })).toHaveAttribute('href', '/ask')
  expect(await screen.findByText('AI 伺服器供應鏈')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/ConversationList.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `useConversations.ts`**

```ts
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { getJSON } from './api'
import { conversationSummarySchema, type ConversationSummary } from './schemas'

export function useConversations() {
  return useQuery<ConversationSummary[]>({
    queryKey: ['conversations'],
    queryFn: () =>
      getJSON('/api/conversations?limit=50', z.array(conversationSummarySchema), { cache: 'no-store' }),
  })
}
```

- [ ] **Step 4: 實作 `ConversationList.tsx`**

```tsx
import { Link } from 'react-router'
import { Icon } from '../primitives/Icon'
import { useConversations } from '../../lib/useConversations'
import styles from './ConversationList.module.css'

export function ConversationList() {
  const { data } = useConversations()
  return (
    <>
      <div className={styles.newWrap}>
        <Link to="/ask" className={styles.newBtn}>
          <Icon name="plus" size={17} /> 新對話
        </Link>
      </div>
      <div className={styles.heading}>歷史對話</div>
      <div className={`${styles.list} tf-scroll`}>
        {(data ?? []).map((c) => (
          <Link key={c.conversation_id} to={`/ask?c=${encodeURIComponent(c.conversation_id)}`} className={styles.item}>
            {c.title}
          </Link>
        ))}
      </div>
    </>
  )
}
```

`frontend/src/components/shell/ConversationList.module.css`:
```css
.newWrap { padding: 0 10px 8px; }
.newBtn {
  width: 100%; display: flex; align-items: center; gap: 8px;
  border: 1px solid var(--tf-border); background: #fff; border-radius: 8px;
  color: var(--tf-gold-text); font-weight: 600; font-size: 13px; padding: 9px 12px;
  cursor: pointer; text-decoration: none;
}
.newBtn:hover { border-color: var(--tf-gold-text); }
.heading { font-size: 11px; font-weight: 600; color: var(--tf-text-4); padding: 6px 16px; }
.list { flex: 1; overflow-y: auto; padding: 0 10px; }
.item {
  display: block; padding: 8px 10px; border-radius: 8px;
  font-size: 13px; color: var(--tf-text-2); text-decoration: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.item:hover { background: var(--tf-border-weak); }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/ConversationList.test.tsx`
Expected: 1 passed。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/useConversations.ts frontend/src/components/shell/ConversationList.tsx frontend/src/components/shell/ConversationList.module.css frontend/src/components/shell/ConversationList.test.tsx
git commit -m "feat(frontend): 側欄歷史對話清單 + 新對話（/api/conversations）"
```

---

## Task 11: `shell/AccountMenu` + `useStats`

**Files:**
- Create: `frontend/src/lib/useStats.ts`, `frontend/src/components/shell/AccountMenu.tsx`, `frontend/src/components/shell/AccountMenu.module.css`
- Test: `frontend/src/components/shell/AccountMenu.test.tsx`

**Interfaces:**
- Consumes: `getJSON`, `statsSchema`, `Popover`, `MenuItem`, `Icon`, `@tanstack/react-query`。
- Produces:
  - `useStats(): UseQueryResult<StatsResponse>`（`GET /api/stats`）
  - `AccountMenu({ variant }): JSX`，`variant: 'mini' | 'row' | 'mobile'`；名稱＝`stats.username ?? '分析師'`；登出＝原生 `<form method="post" action="/logout">`。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/shell/AccountMenu.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'

afterEach(() => vi.unstubAllGlobals())

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

test('點帳號鈕開選單，顯示登出（原生 form action=/logout）', async () => {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify({
      total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
    }), { status: 200 }),
  ))
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const logout = screen.getByRole('button', { name: '登出' })
  expect(logout).toHaveAttribute('type', 'submit')
  expect(logout.closest('form')).toHaveAttribute('action', '/logout')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/AccountMenu.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `useStats.ts`**

```ts
import { useQuery } from '@tanstack/react-query'
import { getJSON } from './api'
import { statsSchema, type StatsResponse } from './schemas'

export function useStats() {
  return useQuery<StatsResponse>({
    queryKey: ['stats'],
    queryFn: () => getJSON('/api/stats', statsSchema, { cache: 'no-store' }),
    staleTime: 60_000,
  })
}
```

- [ ] **Step 4: 實作 `AccountMenu.tsx`**

```tsx
import { useRef, useState } from 'react'
import { Popover } from '../primitives/Popover'
import { Icon } from '../primitives/Icon'
import { useStats } from '../../lib/useStats'
import styles from './AccountMenu.module.css'

export function AccountMenu({ variant }: { variant: 'mini' | 'row' | 'mobile' }) {
  const { data } = useStats()
  const name = data?.username ?? '分析師'
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button
        type="button"
        aria-expanded={open}
        title={name}
        onClick={() => setOpen((o) => !o)}
        className={variant === 'row' ? styles.rowTrigger : styles.avatarBtn}
      >
        <span className={styles.avatar}><Icon name="user" size={17} /></span>
        {variant === 'row' && (
          <span className={styles.rowText}>
            <span className={styles.name}>{name}</span>
            <span className={styles.sub}>研究部 · 分析師</span>
          </span>
        )}
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.pop}>
        <div className={styles.popHead}>
          <div className={styles.name}>{name}</div>
          <div className={styles.sub}>研究部 · 分析師</div>
        </div>
        <form method="post" action="/logout">
          <button type="submit" className={styles.logout}>登出</button>
        </form>
      </Popover>
    </div>
  )
}
```

`frontend/src/components/shell/AccountMenu.module.css`:
```css
.wrap { position: relative; }
.avatarBtn { width: 34px; height: 34px; border-radius: 999px; border: 1px solid var(--tf-border); background: var(--tf-gold-tint); color: var(--tf-gold-text); display: flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
.avatarBtn:hover { border-color: var(--tf-gold-text); }
.rowTrigger { width: 100%; display: flex; align-items: center; gap: 10px; border: none; background: none; cursor: pointer; padding: 7px 8px; border-radius: 8px; font-family: inherit; }
.rowTrigger:hover { background: var(--tf-border-weak); }
.avatar { width: 30px; height: 30px; border-radius: 999px; border: 1px solid var(--tf-border); background: var(--tf-gold-tint); color: var(--tf-gold-text); display: flex; align-items: center; justify-content: center; flex: none; }
.rowText { flex: 1; text-align: left; min-width: 0; }
.name { display: block; font-size: 13px; font-weight: 600; color: var(--tf-text-1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { display: block; font-size: 11.5px; color: var(--tf-text-4); }
.pop { left: 10px; right: 10px; bottom: calc(100% - 4px); }
.popHead { padding: 8px 10px 10px; border-bottom: 1px solid var(--tf-border-weak); margin-bottom: 4px; }
.logout { width: 100%; text-align: left; border: none; background: none; font-size: 13px; color: var(--tf-text-2); padding: 8px 10px; border-radius: 8px; cursor: pointer; font-family: inherit; }
.logout:hover { background: var(--tf-border-weak); }
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/AccountMenu.test.tsx`
Expected: 1 passed。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/useStats.ts frontend/src/components/shell/AccountMenu.tsx frontend/src/components/shell/AccountMenu.module.css frontend/src/components/shell/AccountMenu.test.tsx
git commit -m "feat(frontend): 帳號選單（/api/stats 名稱 + 原生 form 登出）"
```

---

## Task 12: `shell/MobileTabBar`

**Files:**
- Create: `frontend/src/components/shell/MobileTabBar.tsx`, `frontend/src/components/shell/MobileTabBar.module.css`
- Test: `frontend/src/components/shell/MobileTabBar.test.tsx`

**Interfaces:**
- Consumes: `NavItem`（`variant="mobile"`）、`AccountMenu`（`variant="mobile"`）。
- Produces: `MobileTabBar(): JSX`——四格（檢索/問答/監控/帳號），固定底部，觸控目標 ≥44px。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/components/shell/MobileTabBar.test.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { MobileTabBar } from './MobileTabBar'

afterEach(() => vi.unstubAllGlobals())

test('四格導覽含帳號', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
  }), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}><MobileTabBar /></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/MobileTabBar.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 實作 `MobileTabBar.tsx`**

```tsx
import { NavItem } from './NavItem'
import { AccountMenu } from './AccountMenu'
import styles from './MobileTabBar.module.css'

export function MobileTabBar() {
  return (
    <div className={styles.bar}>
      <NavItem to="/search" icon="search" label="檢索" variant="mobile" />
      <NavItem to="/ask" icon="messages" label="問答" variant="mobile" />
      <NavItem to="/monitor" icon="activity" label="監控" variant="mobile" />
      <div className={styles.account}><AccountMenu variant="mobile" /></div>
    </div>
  )
}
```

`frontend/src/components/shell/MobileTabBar.module.css`:
```css
.bar {
  position: fixed; left: 0; right: 0; bottom: 0;
  height: calc(56px + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
  background: var(--tf-surface); border-top: 1px solid var(--tf-border);
  display: flex; z-index: 50;
}
.account { flex: 1; display: flex; align-items: center; justify-content: center; }
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && ./node_modules/.bin/vitest run src/components/shell/MobileTabBar.test.tsx`
Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/shell/MobileTabBar.tsx frontend/src/components/shell/MobileTabBar.module.css frontend/src/components/shell/MobileTabBar.test.tsx
git commit -m "feat(frontend): 手機底部分頁列（四格 + 帳號）"
```

---

## Task 13: `AppShell` + 路由 + placeholder 頁 + providers

**Files:**
- Create: `frontend/src/components/shell/AppShell.tsx`, `frontend/src/components/shell/AppShell.module.css`, `frontend/src/features/search/SearchPage.tsx`, `frontend/src/features/ask/AskPage.tsx`, `frontend/src/features/monitor/MonitorPage.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`
- Test: `frontend/src/App.test.tsx`（改寫）

**Interfaces:**
- Consumes: `SideRail`, `MobileTabBar`, `useMediaQuery`, `useSidebarCollapsed`, react-router `Outlet`。
- Produces: `App`（`RouterProvider`，basename `/app`）；`AppShell`（layout route，含視覺隱藏 `<h1>`）；三 placeholder 頁 default export。

- [ ] **Step 1: 實作 placeholder 頁**

`frontend/src/features/search/SearchPage.tsx`:
```tsx
export default function SearchPage() {
  return <div style={{ padding: 20 }}>檢索頁（Phase 1 實作）</div>
}
```
`frontend/src/features/ask/AskPage.tsx`:
```tsx
export default function AskPage() {
  return <div style={{ padding: 20 }}>問答頁（Phase 2 實作）</div>
}
```
`frontend/src/features/monitor/MonitorPage.tsx`:
```tsx
export default function MonitorPage() {
  return <div style={{ padding: 20 }}>監控頁（Phase 3 實作）</div>
}
```

- [ ] **Step 2: 實作 `AppShell.tsx`**

```tsx
import { Outlet } from 'react-router'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { useSidebarCollapsed } from '../../lib/useSidebarCollapsed'
import { SideRail } from './SideRail'
import { MobileTabBar } from './MobileTabBar'
import styles from './AppShell.module.css'

export function AppShell() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { collapsed, toggle } = useSidebarCollapsed()
  return (
    <div className={styles.shell}>
      <h1 className={styles.srOnly}>廷豐智能研報</h1>
      {!isMobile && <SideRail collapsed={collapsed} onToggle={toggle} />}
      <div className={styles.main}>
        <Outlet />
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  )
}
```

`frontend/src/components/shell/AppShell.module.css`:
```css
.shell { display: flex; height: 100vh; overflow: hidden; background: var(--tf-canvas); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; height: 100%; }
.srOnly { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
```

- [ ] **Step 3: 改寫 `App.tsx`（路由表，basename /app）**

```tsx
import { Suspense, lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { AppShell } from './components/shell/AppShell'

const SearchPage = lazy(() => import('./features/search/SearchPage'))
const AskPage = lazy(() => import('./features/ask/AskPage'))
const MonitorPage = lazy(() => import('./features/monitor/MonitorPage'))

function NotFound() {
  return <div style={{ padding: 20 }}>找不到頁面</div>
}

// eslint-disable-next-line react-refresh/only-export-components
export const routes = [
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <Navigate to="/search" replace /> },
      { path: '/search', element: <Suspense><SearchPage /></Suspense> },
      { path: '/ask', element: <Suspense><AskPage /></Suspense> },
      { path: '/monitor', element: <Suspense><MonitorPage /></Suspense> },
      { path: '*', element: <NotFound /> },
    ],
  },
]

const router = createBrowserRouter(routes, { basename: '/app' })

export default function App() {
  return <RouterProvider router={router} />
}
```

- [ ] **Step 4: 改寫 `main.tsx`（掛 QueryClientProvider）**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './styles/tokens.css'
import App from './App'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
```

- [ ] **Step 5: 改寫 `App.test.tsx`（整合渲染）**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { routes } from './App'

afterEach(() => vi.unstubAllGlobals())

test('/search 落在檢索 placeholder 且側欄可見', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/conversations')
      ? new Response(JSON.stringify([]), { status: 200 })
      : new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 }),
  ))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/search'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByText('檢索頁（Phase 1 實作）')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '廷豐智能研報' })).toBeInTheDocument()
})
```

> 註：`react-router` 的 jsdom 測試須有寬度＞767 才走 SideRail 分支；jsdom 預設 `innerWidth=1024`，且 Task 1 的 matchMedia polyfill 回 `matches:false`，故 `(max-width:767px)` 為 false → 渲染 SideRail，符合預期。

- [ ] **Step 6: 跑測試 + build + lint**

Run: `cd frontend && ./node_modules/.bin/vitest run && npm run build && npm run lint`
Expected: 全部 passed；build 綠；lint 0 error。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/shell/AppShell.tsx frontend/src/components/shell/AppShell.module.css frontend/src/features/search/SearchPage.tsx frontend/src/features/ask/AskPage.tsx frontend/src/features/monitor/MonitorPage.tsx frontend/src/App.tsx frontend/src/main.tsx frontend/src/App.test.tsx
git commit -m "feat(frontend): AppShell + 路由（basename /app）+ placeholder 三頁"
```

---

## Task 14: 後端加回 `/app` 服務

**Files:**
- Modify: `web/server.py`（新增 SPA 服務區塊；`index()` 保持服務 vanilla `index.html`——**本 Phase 不 cutover**）
- Test: `tests/test_spa_serving.py`

**Interfaces:**
- Consumes: 既有 `_static_page`, `STATIC_DIR`, `auth` middleware。
- Produces: `GET /app`、`GET /app/{spa_path:path}` → `frontend/dist/index.html`（no-cache）；`/app/assets/*` immutable 掛載。

- [ ] **Step 1: 寫失敗測試**

`tests/test_spa_serving.py`:
```python
import time

from fastapi.testclient import TestClient

from web import auth
from web.server import app


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def test_unauthed_app_redirects_to_login():
    client = TestClient(app)
    resp = client.get("/app/search", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_authed_app_deeplink_serves_shell():
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/app/ask", follow_redirects=False)
    # dist 若尚未 build，回 503；已 build 回 200 且為 HTML shell
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert "text/html" in resp.headers["content-type"]
        assert resp.headers.get("cache-control") == "no-cache"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_spa_serving.py -v`
Expected: FAIL（`/app/search` 目前回 404 → middleware 對非 `/api/` 未登入本已 302，但已登入的 `/app/ask` 尚無 route → 404）。

- [ ] **Step 3: 在 `web/server.py` 新增 SPA 服務**（放在檔案末端 `app.mount("/static", ...)` 之前；沿用舊實作精神）

在 import 區確認有：
```python
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```
在既有常數區（`STATIC_DIR` 附近）加：
```python
SPA_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
```
在既有 `_NoCacheStatic` 類別附近加：
```python
class _ImmutableStatic(StaticFiles):
    """Vite 內容雜湊資產（/app/assets/*）長快取：hash 變則 URL 變，故可 immutable。"""

    async def get_response(self, path, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
```
在 `@app.get("/")` 之後、`app.mount("/static", ...)` 之前加：
```python
# ───── SPA（/app 子路徑；shell + 雜湊資產，純服務無業務邏輯）─────
if (SPA_DIST / "assets").is_dir():
    app.mount(
        "/app/assets",
        _ImmutableStatic(directory=SPA_DIST / "assets", check_dir=False),
        name="spa-assets",
    )


@app.get("/app")
@app.get("/app/{spa_path:path}")
async def spa_shell(spa_path: str = ""):
    """SPA shell：所有 /app/* 深連結回同一份 index.html，交給 client 端路由。"""
    index = SPA_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="SPA 尚未 build（frontend/dist 不存在）")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
```

> 註：`index()` 維持回 `_static_page("index.html")`（vanilla）；本 Phase **不** cutover。middleware 已對未登入非 `/api/` 一律 302 `/login`，故 `/app/*` 未登入自動導登入，無需改 middleware。

- [ ] **Step 4: build 前端讓 dist 存在，再跑測試**

Run: `cd frontend && npm run build && cd .. && uv run pytest tests/test_spa_serving.py -v`
Expected: 2 passed（deeplink 回 200 HTML no-cache）。

- [ ] **Step 5: 確認既有後端測試未回歸**

Run: `uv run pytest tests/test_auth.py tests/test_auth_next.py -v`
Expected: 全 passed（vanilla `/`、未登入 302 `/login` 行為不變）。

- [ ] **Step 6: Commit**

```bash
git add web/server.py tests/test_spa_serving.py
git commit -m "feat(web): 加回 /app SPA 服務（shell + immutable 資產；不 cutover）"
```

---

## Task 15: 改寫登入頁對齊 `.dc.html`

**Files:**
- Modify: `web/static/login.html`（只換視覺，保留表單語意）
- Test: `tests/test_login_page.py`

**Interfaces:**
- Consumes: 既有 `POST /login`（`username`/`password`/`next`）、error query（`1`/`locked`/`insecure`）。
- Produces: 對齊 `.dc.html` 登入卡的 HTML（平色畫布、襯線品牌字、金鈕、金 focus ring、紅字錯誤框）。

- [ ] **Step 1: 寫失敗測試**

`tests/test_login_page.py`:
```python
from fastapi.testclient import TestClient

from web.server import app


def test_login_page_matches_new_design_and_keeps_form():
    client = TestClient(app)
    html = client.get("/login").text
    # 保留表單語意
    assert 'action="/login"' in html
    assert 'name="username"' in html and 'name="password"' in html
    assert 'name="next"' in html
    # 新設計 token（金色主色與平色畫布）
    assert "#8a5a0f" in html
    assert "#f6f7f9" in html
    # 襯線品牌字
    assert "Noto Serif TC" in html
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_login_page.py -v`
Expected: FAIL（現行 login.html 用 `--brand #ae7415`、漸層底，無 `#f6f7f9`/`#8a5a0f`/Noto Serif TC）。

- [ ] **Step 3: 改寫 `web/static/login.html`**（完整檔內容；保留 favicon data URI 與 error/next 腳本）

```html
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登入 · 廷豐智能研報</title>
<meta name="description" content="廷豐智能研報登入" />
<meta name="theme-color" content="#f6f7f9" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --gold: #8a5a0f; --gold-hover: #7a4f0c; --gold-tint: #faf3e3;
    --canvas: #f6f7f9; --surface: #fff; --border: #e4e7ec;
    --text-1: #101828; --text-2: #344054; --text-3: #667085; --text-4: #98a2b3;
    --serif: 'Noto Serif TC', Georgia, serif;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
    background: var(--canvas); color: var(--text-1); padding: 24px;
  }
  .card {
    width: 360px; max-width: 100%;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; box-shadow: 0 1px 3px rgba(16,24,40,.06);
    padding: 36px 32px;
  }
  .brand { display: flex; flex-direction: column; align-items: center; gap: 5px; margin-bottom: 26px; }
  .glyph { font-family: var(--serif); font-weight: 800; font-size: 44px; color: var(--gold); line-height: 1; }
  .name { font-family: var(--serif); font-weight: 700; font-size: 21px; color: var(--text-1); }
  .tagline { font-size: 12.5px; color: var(--text-3); }
  label { display: block; font-size: 12.5px; font-weight: 600; color: var(--text-2); margin: 0 0 6px; }
  input {
    width: 100%; padding: 9px 12px; font-size: 14px; color: var(--text-1);
    border: 1px solid var(--border); border-radius: 8px; background: #fff; outline: none;
  }
  input#username { margin-bottom: 14px; }
  input:focus { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(138,90,15,.12); }
  button {
    width: 100%; margin-top: 18px; padding: 10px 0; font-size: 14px; font-weight: 600;
    color: #fff; background: var(--gold); border: none; border-radius: 999px; cursor: pointer;
  }
  button:hover { background: var(--gold-hover); }
  .error {
    display: none; margin-top: 12px; padding: 8px 11px; font-size: 12.5px;
    color: #b42318; background: #fef3f2; border: 1px solid #fecdca; border-radius: 8px;
  }
  .hint { margin-top: 16px; text-align: center; font-size: 11.5px; color: var(--text-4); }
</style>
</head>
<body>
  <main class="card">
    <div class="brand">
      <div class="glyph">廷</div>
      <div class="name">廷豐智能研報</div>
      <div class="tagline">券商研究報告 · 智能檢索平台</div>
    </div>
    <form method="post" action="/login" autocomplete="on">
      <label for="username">帳號</label>
      <input id="username" name="username" type="text" autocomplete="username" autofocus required>
      <label for="password">密碼</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <input type="hidden" name="next" id="next-field" />
      <p class="error" id="error"></p>
      <button type="submit">登入</button>
    </form>
    <div class="hint">研究部內部系統 · 請以配發帳號登入</div>
  </main>
  <script>
    var p = new URLSearchParams(location.search);
    if (p.has("error")) {
      var el = document.getElementById("error");
      el.textContent = p.get("error") === "locked"
        ? "嘗試次數過多,請稍後再試。"
        : p.get("error") === "insecure"
        ? "此登入只接受 HTTPS 或本機 localhost，請改走受保護入口。"
        : "帳號或密碼錯誤。";
      el.style.display = "block";
    }
    var nx = p.get("next");
    if (nx) document.getElementById("next-field").value = nx;
  </script>
</body>
</html>
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_login_page.py -v`
Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
git add web/static/login.html tests/test_login_page.py
git commit -m "feat(web): 登入頁改版對齊 .dc.html（襯線品牌字+金鈕+平色畫布）"
```

---

## Task 16: Playwright 冒煙（`:8098`）

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/e2e/shell.spec.ts`
- Modify: `frontend/package.json`（加 `e2e` script）

**Interfaces:**
- Consumes: 工作樹起的後端 `:8098`（載入 `frontend/dist`）+ 已登入 cookie。
- Produces: 冒煙——登入頁渲染、`/app/search` 殼與側欄導覽可見。

- [ ] **Step 1: 加 `e2e` script 到 `frontend/package.json`**

在 `scripts` 加：
```json
    "e2e": "playwright test"
```

- [ ] **Step 2: 建立 `frontend/playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:8098' },
})
```

- [ ] **Step 3: 建立 `frontend/e2e/shell.spec.ts`**

```ts
import { test, expect } from '@playwright/test'

test('登入頁對齊新設計', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByText('廷豐智能研報')).toBeVisible()
  await expect(page.getByRole('button', { name: '登入' })).toBeVisible()
})

test('登入後 /app/search 殼與側欄導覽可見', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL('**/app/search')
  await expect(page.getByText('檢索頁（Phase 1 實作）')).toBeVisible()
  await expect(page.getByRole('link', { name: /問答/ })).toBeVisible()
})
```

- [ ] **Step 4: 起工作樹後端到 :8098（載入剛 build 的 dist），跑 e2e**

Run:
```bash
cd frontend && npm run build && cd ..
# 以工作樹碼起後端於 8098（帳密用 .env 或環境變數）
REPORT_MARK_ACCESS_USERNAME=analyst REPORT_MARK_ACCESS_PASSWORD=test \
  uv run uvicorn web.server:app --port 8098 &
# 等就緒
until curl -sf http://127.0.0.1:8098/login >/dev/null; do sleep 0.5; done
cd frontend && TF_USER=analyst TF_PW=test npm run e2e
```
Expected: 2 passed。

- [ ] **Step 5: 殺 :8098（先 `ss` 核對，勿誤殺正式 :8097）**

Run:
```bash
ss -ltnp | grep ':8098' && kill "$(ss -ltnp | grep ':8098' | grep -oP 'pid=\K[0-9]+' | head -1)"
```

- [ ] **Step 6: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/shell.spec.ts frontend/package.json
git commit -m "test(frontend): Phase 0 Playwright 冒煙（登入 + /app/search 殼導覽）"
```

---

## Phase 0 完成驗收

- `cd frontend && npm run build && npm run lint && ./node_modules/.bin/vitest run` 全綠。
- `uv run pytest tests/test_spa_serving.py tests/test_login_page.py tests/test_auth.py tests/test_auth_next.py` 全綠。
- 手動：起 `:8098` → 登入頁對齊 `.dc.html` → `/app/search` 顯 placeholder + 可收合左側欄 + 三導覽切換 + 手機底欄（縮視窗 <768）+ 帳號選單登出。
- vanilla `/`、`/monitor` 仍正常（未 cutover）。

---

## Self-Review（對照 spec）

- **Spec §2.1/§2.2 基座**：Task 1（工具鏈）、Task 2（tokens.css）、樣式機制 CSS Modules 貫穿各元件 Task。✓
- **Spec §3 token**：Task 2 逐一落值。✓
- **Spec §4 App Shell**：SideRail（Task 9）、ConversationList（Task 10）、AccountMenu（Task 11）、MobileTabBar（Task 12）、AppShell+h1（Task 13）；`aria-current`（NavItem Task 8）、`aria-expanded`（AccountMenu Task 11）、focus trap（Task 6/7）。✓
- **Spec §5 登入頁**：Task 15（standalone 改寫、保留表單語意）。✓
- **Spec §2.3 整合**：Vite base/proxy（Task 1）、後端 /app 服務 + middleware 不動 + 不 cutover（Task 14）。✓
- **Spec §10 API**：/api/stats（useStats）、/api/conversations（useConversations）；schema 對齊後端欄位（Task 4）。✓
- **Spec §12 測試**：每元件 TDD；e2e :8098 + ss 核對（Task 16）。✓
- **Global Constraints**：無 Mantine/無圖表庫（本 Phase 未引入任何）；金色 `#8a5a0f`（tokens + 元件）；市場順序/色（meta.ts）。✓
- **佔位符掃描**：無 TBD/TODO；每步含實際程式碼與指令。✓
- **型別一致**：`getJSON`/`ApiError`/`marketColor`/`useStats`/`useConversations`/`useSidebarCollapsed`/`NavItem variant`/`AccountMenu variant`/`Icon name` 在定義與使用處一致。✓
- **範圍**：Phase 0 只做地基＋殼＋登入；search/ask/monitor 實作在 Phase 1–3（各自獨立 plan）。✓

（Phase 1 檢索頁、Phase 2 問答+研報、Phase 3 監控頁、Phase 4 cutover 各於 Phase 0 落地後另出獨立 plan。）
