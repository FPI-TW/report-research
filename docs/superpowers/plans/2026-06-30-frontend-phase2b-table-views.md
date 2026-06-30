# 前端 Phase 2b 表格檢視/檢視切換/高亮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 React 檢索頁 `/app/search` 補到與現行 live `/` 平價——表格檢視（可排序）、檢視切換（列表⇄表格）、分組切換（月⇄市場接通索引/drill）、關鍵字高亮、2a 審查延後平價清單——但不 cutover。

**Architecture:** 沿用 2a 方案 A。檢視/分組為**呈現狀態**（URL + localStorage），**不進 `useSearchResults` 的 query key**，故切檢視只重畫不重抓；表格排序為 TableView 內部 ephemeral state。高亮以純函式切 segment、JSX `<mark>` 渲染（無 dangerouslySetInnerHTML）。後端不動。

**Tech Stack:** React 19.2.7 / Vite 8 / react-router 8 / Mantine 9 / @tanstack/react-query 5 / Zod 4 / Vitest 4 / TypeScript，Node 22.22+。

## Global Constraints

- 後端 `/api/*` 行為、`db/**`、`app/**` **一律不動**；無 schema 變更；不新增端點。
- 棧鎖定 Phase 0：**不引入 Zustand/Redux**；UI 狀態用 React state + react-router `searchParams` + `localStorage`。
- 後端 Optional 欄位 Zod 一律 `.nullish()`（不新增 schema，沿用既有）。
- **禁止 `dangerouslySetInnerHTML`**；所有文字（含高亮）以純節點渲染。
- **不 cutover**：舊 `/` 不動，`/app/search` 仍為平行頁（cutover 為 Phase 2c）。
- **檢視/分組不得進 `useSearchResults(filters)` 的 query key**——切檢視/分組/表格排序皆對已載入 `rows` 操作、**不重打 API**。
- 檢視範圍對齊現行 live：只做 `group`(預設) + `table` 兩檢視、`month`(預設)/`market` 兩分組；**不復活**卡片(grid)/列表(flat list)檢視與 report_type 分組。
- 視覺：表格/切換器/高亮 bespoke CSS 從 `web/static/index.html` inline `<style>` 萃取到 module CSS，對齊 live。
- 路徑前綴：新檔在 `frontend/src/features/search/`；route 不變（不新增 route）。
- 測試本分支用 `cd frontend && npx vitest run src`（避開 Playwright `e2e/` 收集）；最終 `npm run build`（tsc + vite）+ `node ./node_modules/eslint/bin/eslint.js .`（exit 0）。
- commit 用 Conventional Commits + 繁中 scope，結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## 既有型別（2a，供參照，勿改動定義）

```ts
// lib/normalize.ts
export interface Row extends ReportItem {     // ReportItem: report_id, file_name, market?, source?,
  rank?: number                               //   summary?, report_date?, report_type?, instrument_types?,
  bestScore?: number                          //   relates_stock?, relates_futures?, stock_targets?, futures_targets?
  matchCount?: number                         // （皆 nullish）
  passages?: Passage[]                        // Passage: { score, chunk_index, content }
}
// lib/filters.ts
export interface Filters { q; market; instrument; stock; futures; type; sort }  // 全 string/boolean
export const DEFAULT_FILTERS: Filters
export function filtersToSearchParams(f: Filters): URLSearchParams
export function searchParamsToFilters(sp, allow): Filters
// lib/grouping.ts
export type GroupView = 'grouped' | 'index' | 'drill'
export function groupViewMode(group: 'month'|'market', market: string): GroupView
export function groupRows(rows, group): { key: string; rows: Row[] }[]
// components/meta.ts
mLabel(c), mColor(c), iLabel(c), tLabel(t), fmtDate(d): string|null, SORT_LABELS
```

## File Structure

```
frontend/src/features/search/
  lib/
    terms.ts              # [改] buildTerms 升級 CJK bigram
    terms.test.ts         # [改] 補 CJK 測試
    highlight.ts          # [新] highlightSegments 純函式
    highlight.test.ts     # [新]
    tableSort.ts          # [新] sortedRows / nextTableSort
    tableSort.test.ts     # [新]
    filters.ts            # [改] 加 ViewState/VIEWS/GROUPS/viewState 序列化
    filters.test.ts       # [改] 補 viewState 測試
  hooks/
    useSearchParamsState.ts   # [改] 回 filters + viewState + setters（+ localStorage rm_view）
    useSearchParamsState.test.tsx  # [改]
  components/
    Highlight.tsx         # [新] 小元件：吃 text+terms 渲染 <mark> 純節點
    ViewSwitch.tsx        # [新] 列表/表格 radiogroup
    ViewSwitch.test.tsx   # [新]
    GroupBySelect.tsx     # [新] 月/市場 Mantine Select
    GroupBySelect.test.tsx# [新]
    TableView.tsx         # [新] 可排序表格
    TableView.test.tsx    # [新]
    targets.ts            # [新] targetsSummary 純函式（表格標的欄）
    targets.test.ts       # [新]
    ResultCard.tsx        # [改] 片段高亮 + 展開「其他 N 段」+ React.memo
    ResultCard.test.tsx   # [改]
    MarketIndex.tsx       # [改] 改吃 stats.markets 全語料 count
    MarketIndex.test.tsx  # [新]
    DrillView.tsx         # [改] 標頭加總篇數
    LoadMore.tsx          # [改] 「還有 N 篇」
    ResultsView.tsx       # [改] 加 table 分支 + 傳 terms/stats/total
  SearchPage.tsx          # [改] viewState/terms/ViewSwitch/GroupBySelect/memo rows
  SearchPage.module.css   # [改] 表格/切換器/mark bespoke CSS
frontend/e2e/search.spec.mjs   # [改] 加表格/切換/分組/高亮平價劇本
```

---

### Task 1: `lib/terms.ts` — buildTerms 升級 CJK bigram

**Files:**
- Modify: `frontend/src/features/search/lib/terms.ts`
- Test: `frontend/src/features/search/lib/terms.test.ts`

**Interfaces:**
- Produces: `buildTerms(q: string): string[]`（去重、依長度由長到短排序；CJK token 拆單字+相鄰 2-gram；非 CJK 且 length≥2 收整詞）

- [ ] **Step 1: 改寫測試** `terms.test.ts`

```ts
import { test, expect } from 'vitest'
import { buildTerms } from './terms'

test('英數整詞（length>=2）收錄、去重', () => {
  expect(buildTerms('AI server AI')).toEqual(['server', 'AI'])  // 長→短
})

test('單一英數字元（length<2）不收', () => {
  expect(buildTerms('a server')).toEqual(['server'])
})

test('CJK 單字 token 收單字', () => {
  expect(buildTerms('台')).toEqual(['台'])
})

test('CJK 多字 token 收所有相鄰 2-gram', () => {
  // '台積電' -> '台積','積電'（length 2，依長度排序穩定，原序保留）
  expect(buildTerms('台積電')).toEqual(['台積', '積電'])
})

test('混合 CJK 與英數', () => {
  const t = buildTerms('AI 散熱')
  expect(t).toContain('AI')
  expect(t).toContain('散熱')
})

test('空字串回空陣列', () => {
  expect(buildTerms('')).toEqual([])
  expect(buildTerms('   ')).toEqual([])
})

test('依長度由長到短排序（高亮最長優先）', () => {
  const t = buildTerms('半導體 AI')
  // '半導','導體'(2) 與 'AI'(2) 皆 length 2；長度相同維持穩定
  expect(t.every((x) => x.length === 2)).toBe(true)
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/lib/terms.test.ts`
Expected: FAIL（新斷言不符舊空白分詞版）

- [ ] **Step 3: 實作**（port `web/static/app/render.js:13-23`）

```ts
/**
 * 將查詢字串分詞為唯一 terms（供高亮）。
 * Port of web/static/app/render.js buildTerms：
 *  - CJK token：length===1 收單字；否則收所有相鄰 2-gram（bigram）
 *  - 非 CJK token：length>=2 收整詞
 *  - 去重後依長度由長到短排序（高亮時最長匹配優先）
 */
export function buildTerms(q: string): string[] {
  const terms = new Set<string>()
  for (const tok of q.split(/\s+/)) {
    const t = tok.trim()
    if (!t) continue
    if (/[一-鿿]/.test(t)) {
      if (t.length === 1) terms.add(t)
      for (let i = 0; i < t.length - 1; i++) terms.add(t.slice(i, i + 2))
    } else if (t.length >= 2) {
      terms.add(t)
    }
  }
  return [...terms].sort((a, b) => b.length - a.length)
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/lib/terms.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/lib/terms.ts frontend/src/features/search/lib/terms.test.ts
git commit -m "feat(frontend): Phase2b buildTerms 升級 CJK bigram 分詞"
```

---

### Task 2: `lib/highlight.ts` — highlightSegments 純函式

**Files:**
- Create: `frontend/src/features/search/lib/highlight.ts`
- Test: `frontend/src/features/search/lib/highlight.test.ts`

**Interfaces:**
- Produces: `highlightSegments(text: string, terms: string[]): { text: string; mark: boolean }[]`（XSS 安全：只回資料、不產 HTML 字串）

- [ ] **Step 1: 寫測試** `highlight.test.ts`

```ts
import { test, expect } from 'vitest'
import { highlightSegments } from './highlight'

test('無 terms 回單一未標記 segment', () => {
  expect(highlightSegments('hello', [])).toEqual([{ text: 'hello', mark: false }])
})

test('單一 term 切出標記段', () => {
  expect(highlightSegments('AI server', ['AI'])).toEqual([
    { text: 'AI', mark: true },
    { text: ' server', mark: false },
  ])
})

test('大小寫不敏感', () => {
  const segs = highlightSegments('ai SERVER', ['ai', 'server'])
  expect(segs.filter((s) => s.mark).map((s) => s.text)).toEqual(['ai', 'SERVER'])
})

test('CJK 命中', () => {
  const segs = highlightSegments('台積電法說', ['台積'])
  expect(segs.some((s) => s.mark && s.text === '台積')).toBe(true)
})

test('regex 特殊字元被跳脫（不當 regex 解讀）', () => {
  const segs = highlightSegments('a.b a+b', ['a.b'])
  // 只命中字面 'a.b'，不把 . 當任意字元
  expect(segs.filter((s) => s.mark).map((s) => s.text)).toEqual(['a.b'])
})

test('無命中時回單一未標記 segment', () => {
  expect(highlightSegments('hello', ['xyz'])).toEqual([{ text: 'hello', mark: false }])
})

test('不產生空字串 segment', () => {
  const segs = highlightSegments('AIAI', ['AI'])
  expect(segs.every((s) => s.text.length > 0)).toBe(true)
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/lib/highlight.test.ts`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 實作** `highlight.ts`

```ts
/**
 * 把 text 依 terms 切成 segment 陣列（mark 標示命中）。
 * XSS 安全：只回純資料，由元件以 <mark>{seg.text}</mark> 渲染純節點，
 * 永不產生 HTML 字串（取代 vanilla render.js highlight 的 esc()+字串注入）。
 */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function highlightSegments(
  text: string,
  terms: string[],
): { text: string; mark: boolean }[] {
  if (!terms.length || !text) return [{ text, mark: false }]
  const re = new RegExp('(' + terms.map(escapeRegExp).join('|') + ')', 'gi')
  const out: { text: string; mark: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0
    if (i > last) out.push({ text: text.slice(last, i), mark: false })
    out.push({ text: m[0], mark: true })
    last = i + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), mark: false })
  return out.length ? out : [{ text, mark: false }]
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/lib/highlight.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/lib/highlight.ts frontend/src/features/search/lib/highlight.test.ts
git commit -m "feat(frontend): Phase2b highlightSegments 純函式（XSS 安全高亮）"
```

---

### Task 3: `lib/tableSort.ts` — sortedRows / nextTableSort

**Files:**
- Create: `frontend/src/features/search/lib/tableSort.ts`
- Test: `frontend/src/features/search/lib/tableSort.test.ts`

**Interfaces:**
- Produces:
  - `export type TableSortKey = 'name'|'market'|'type'|'date'|'source'|'score'|'match'`
  - `export interface TableSort { key: TableSortKey | null; dir: 'asc'|'desc' }`
  - `sortedRows(rows: Row[], sort: TableSort): Row[]`（key=null 回原序、不變更輸入）
  - `nextTableSort(cur: TableSort, key: TableSortKey): TableSort`（同 key 翻 dir、異 key→{key, dir:'asc'}）

- [ ] **Step 1: 寫測試** `tableSort.test.ts`

```ts
import { test, expect } from 'vitest'
import { sortedRows, nextTableSort } from './tableSort'
import type { Row } from './normalize'

const mk = (over: Partial<Row>): Row => ({
  report_id: over.report_id ?? 'x',
  file_name: over.file_name ?? '',
  ...over,
}) as Row

test('key=null 回原序、不變更輸入', () => {
  const rows = [mk({ file_name: 'b' }), mk({ file_name: 'a' })]
  const out = sortedRows(rows, { key: null, dir: 'asc' })
  expect(out.map((r) => r.file_name)).toEqual(['b', 'a'])
  expect(out).not.toBe(rows)
})

test('依 name 升冪（小寫比較）', () => {
  const rows = [mk({ file_name: 'Banana' }), mk({ file_name: 'apple' })]
  expect(sortedRows(rows, { key: 'name', dir: 'asc' }).map((r) => r.file_name)).toEqual([
    'apple',
    'Banana',
  ])
})

test('依 date 降冪', () => {
  const rows = [mk({ report_date: '2026-01-01' }), mk({ report_date: '2026-06-01' })]
  expect(sortedRows(rows, { key: 'date', dir: 'desc' }).map((r) => r.report_date)).toEqual([
    '2026-06-01',
    '2026-01-01',
  ])
})

test('依 score（bestScore）數值升冪、null 視為 0', () => {
  const rows = [mk({ bestScore: 0.9 }), mk({ bestScore: undefined }), mk({ bestScore: 0.5 })]
  expect(sortedRows(rows, { key: 'score', dir: 'asc' }).map((r) => r.bestScore ?? 0)).toEqual([
    0, 0.5, 0.9,
  ])
})

test('依 match（matchCount）降冪', () => {
  const rows = [mk({ matchCount: 1 }), mk({ matchCount: 5 })]
  expect(sortedRows(rows, { key: 'match', dir: 'desc' }).map((r) => r.matchCount)).toEqual([5, 1])
})

test('nextTableSort：同 key 翻 dir', () => {
  expect(nextTableSort({ key: 'name', dir: 'asc' }, 'name')).toEqual({ key: 'name', dir: 'desc' })
})

test('nextTableSort：異 key 設 asc', () => {
  expect(nextTableSort({ key: 'name', dir: 'desc' }, 'date')).toEqual({ key: 'date', dir: 'asc' })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/lib/tableSort.test.ts`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 實作** `tableSort.ts`（port `render.js` sortedRows，欄位取 Row 正規化名）

```ts
import type { Row } from './normalize'
import { mLabel, tLabel } from '../components/meta'

export type TableSortKey = 'name' | 'market' | 'type' | 'date' | 'source' | 'score' | 'match'
export interface TableSort {
  key: TableSortKey | null
  dir: 'asc' | 'desc'
}

function value(r: Row, key: TableSortKey): string | number {
  switch (key) {
    case 'name':
      return (r.file_name || '').toLowerCase()
    case 'market':
      return mLabel(r.market ?? '')
    case 'type':
      return tLabel(r.report_type ?? '')
    case 'date':
      return r.report_date || ''
    case 'source':
      return r.source || ''
    case 'score':
      return r.bestScore || 0
    case 'match':
      return r.matchCount || 0
    default:
      return ''
  }
}

export function sortedRows(rows: Row[], sort: TableSort): Row[] {
  if (!sort.key) return [...rows]
  const key = sort.key
  const dir = sort.dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const va = value(a, key)
    const vb = value(b, key)
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
    return String(va).localeCompare(String(vb), 'zh-Hant') * dir
  })
}

export function nextTableSort(cur: TableSort, key: TableSortKey): TableSort {
  if (cur.key === key) return { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
  return { key, dir: 'asc' }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/lib/tableSort.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/lib/tableSort.ts frontend/src/features/search/lib/tableSort.test.ts
git commit -m "feat(frontend): Phase2b tableSort 純函式（用戶端表格排序）"
```

---

### Task 4: `lib/filters.ts` — ViewState 序列化

**Files:**
- Modify: `frontend/src/features/search/lib/filters.ts`
- Test: `frontend/src/features/search/lib/filters.test.ts`

**Interfaces:**
- Produces:
  - `export const VIEWS = ['group', 'table'] as const`
  - `export const GROUPS = ['month', 'market'] as const`
  - `export interface ViewState { view: 'group'|'table'; group: 'month'|'market' }`
  - `export const DEFAULT_VIEW_STATE: ViewState = { view: 'group', group: 'month' }`
  - `export function viewStateToParams(vs: ViewState): [string, string][]`（view≠group 才出 `view`；view=group 且 group≠month 才出 `group`）
  - `export function paramsToViewState(sp: URLSearchParams): ViewState`（白名單、預設）

- [ ] **Step 1: 補測試** `filters.test.ts`（保留既有；新增）

```ts
import { test, expect } from 'vitest'
import {
  viewStateToParams,
  paramsToViewState,
  DEFAULT_VIEW_STATE,
} from './filters'

test('預設 view/group 不輸出參數', () => {
  expect(viewStateToParams({ view: 'group', group: 'month' })).toEqual([])
})

test('table 檢視輸出 view，group 省略（非 group 檢視）', () => {
  expect(viewStateToParams({ view: 'table', group: 'month' })).toEqual([['view', 'table']])
})

test('group 檢視 + market 分組輸出 group', () => {
  expect(viewStateToParams({ view: 'group', group: 'market' })).toEqual([['group', 'market']])
})

test('table 檢視時 group 不輸出（即使非 month）', () => {
  expect(viewStateToParams({ view: 'table', group: 'market' })).toEqual([['view', 'table']])
})

test('paramsToViewState 還原合法值', () => {
  const sp = new URLSearchParams('view=table&group=market')
  // table 檢視 group 仍解析（記憶使用者偏好），但 viewStateToParams 在 table 時不輸出
  expect(paramsToViewState(sp)).toEqual({ view: 'table', group: 'market' })
})

test('paramsToViewState 非法值回預設', () => {
  const sp = new URLSearchParams('view=bogus&group=bogus')
  expect(paramsToViewState(sp)).toEqual(DEFAULT_VIEW_STATE)
})

test('paramsToViewState 空回預設', () => {
  expect(paramsToViewState(new URLSearchParams())).toEqual(DEFAULT_VIEW_STATE)
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/lib/filters.test.ts`
Expected: FAIL（新函式不存在）

- [ ] **Step 3: 實作**（append 至 `filters.ts`，**勿動**既有 Filters 區段）

```ts
// ── ViewState（呈現狀態，與資料維度 Filters 分層；不進 query key）─────────────
export const VIEWS = ['group', 'table'] as const
export const GROUPS = ['month', 'market'] as const
export type ViewName = (typeof VIEWS)[number]
export type GroupName = (typeof GROUPS)[number]

export interface ViewState {
  view: ViewName
  group: GroupName
}

export const DEFAULT_VIEW_STATE: ViewState = { view: 'group', group: 'month' }

// 對齊 url.js：view≠group 才寫 view；view=group 且 group≠month 才寫 group
export function viewStateToParams(vs: ViewState): [string, string][] {
  const out: [string, string][] = []
  if (vs.view !== 'group') out.push(['view', vs.view])
  if (vs.view === 'group' && vs.group !== 'month') out.push(['group', vs.group])
  return out
}

export function paramsToViewState(sp: URLSearchParams): ViewState {
  const rawView = sp.get('view')
  const rawGroup = sp.get('group')
  const view: ViewName = (VIEWS as readonly string[]).includes(rawView ?? '')
    ? (rawView as ViewName)
    : 'group'
  const group: GroupName = (GROUPS as readonly string[]).includes(rawGroup ?? '')
    ? (rawGroup as GroupName)
    : 'month'
  return { view, group }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/lib/filters.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/lib/filters.ts frontend/src/features/search/lib/filters.test.ts
git commit -m "feat(frontend): Phase2b ViewState 序列化（view/group URL 分層）"
```

---

### Task 5: `hooks/useSearchParamsState.ts` — 加 viewState + localStorage

**Files:**
- Modify: `frontend/src/features/search/hooks/useSearchParamsState.ts`
- Test: `frontend/src/features/search/hooks/useSearchParamsState.test.tsx`

**Interfaces:**
- Consumes: `filtersToSearchParams`/`searchParamsToFilters`（既有）、`viewStateToParams`/`paramsToViewState`/`DEFAULT_VIEW_STATE`（Task 4）
- Produces: `useSearchParamsState(allow)` → `{ filters, viewState, setFilters, setViewState }`
  - `setFilters(next)`：寫 filters，**保留**目前 view/group
  - `setViewState(next)`：寫 view/group，**保留**目前 filters；view 同步寫 `localStorage rm_view`
  - 初始 view 優先序：URL > localStorage(`rm_view`) > 'group'

- [ ] **Step 1: 改測試** `useSearchParamsState.test.tsx`（保留既有 filters 測試，新增）

```tsx
import { test, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useSearchParamsState } from './useSearchParamsState'
import type { Allowlists } from '../lib/filters'

const allow: Allowlists = { markets: ['TW'], instruments: ['equity'], types: [] }
const wrapper =
  (initial = '/search') =>
  ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  )

beforeEach(() => localStorage.clear())

test('預設 viewState 為 group/month', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), { wrapper: wrapper() })
  expect(result.current.viewState).toEqual({ view: 'group', group: 'month' })
})

test('URL view=table 還原', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrapper('/search?view=table'),
  })
  expect(result.current.viewState.view).toBe('table')
})

test('setViewState 保留既有 filters（q）', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrapper('/search?q=ai'),
  })
  act(() => result.current.setViewState({ view: 'table', group: 'month' }))
  expect(result.current.filters.q).toBe('ai')
  expect(result.current.viewState.view).toBe('table')
})

test('setFilters 保留既有 view', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrapper('/search?view=table'),
  })
  act(() => result.current.setFilters({ ...result.current.filters, q: 'x' }))
  expect(result.current.viewState.view).toBe('table')
})

test('setViewState 寫 localStorage rm_view', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), { wrapper: wrapper() })
  act(() => result.current.setViewState({ view: 'table', group: 'month' }))
  expect(localStorage.getItem('rm_view')).toBe('table')
})

test('URL 無 view 時用 localStorage', () => {
  localStorage.setItem('rm_view', 'table')
  const { result } = renderHook(() => useSearchParamsState(allow), { wrapper: wrapper() })
  expect(result.current.viewState.view).toBe('table')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/hooks/useSearchParamsState.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作**（合併序列化 filters + viewState）

```tsx
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import {
  type Allowlists,
  type Filters,
  type ViewState,
  filtersToSearchParams,
  searchParamsToFilters,
  viewStateToParams,
  paramsToViewState,
} from '../lib/filters'

const VIEW_LS_KEY = 'rm_view'

function readStoredView(): 'group' | 'table' | null {
  try {
    const v = localStorage.getItem(VIEW_LS_KEY)
    return v === 'group' || v === 'table' ? v : null
  } catch {
    return null
  }
}

function merge(filters: Filters, viewState: ViewState): URLSearchParams {
  const sp = filtersToSearchParams(filters)
  for (const [k, v] of viewStateToParams(viewState)) sp.set(k, v)
  return sp
}

export function useSearchParamsState(allow: Allowlists) {
  const [sp, setSp] = useSearchParams()

  const filters = useMemo(() => searchParamsToFilters(sp, allow), [sp, allow])

  const viewState = useMemo<ViewState>(() => {
    const fromUrl = paramsToViewState(sp)
    // URL 無 view 時退回 localStorage（URL > localStorage > 預設）
    if (!sp.get('view')) {
      const stored = readStoredView()
      if (stored) return { ...fromUrl, view: stored }
    }
    return fromUrl
  }, [sp])

  const setFilters = useCallback(
    (next: Filters) => setSp(merge(next, paramsToViewState(sp)), { replace: true }),
    [setSp, sp],
  )

  const setViewState = useCallback(
    (next: ViewState) => {
      try {
        localStorage.setItem(VIEW_LS_KEY, next.view)
      } catch {
        /* ignore */
      }
      setSp(merge(searchParamsToFilters(sp, allow), next), { replace: true })
    },
    [setSp, sp, allow],
  )

  return { filters, viewState, setFilters, setViewState }
}
```

> 注意：`setViewState` 重建 filters 時用 `paramsToViewState(sp)` 取目前 view 不適用（會循環）；此處用傳入 `next`。`setFilters` 取目前 viewState 用 `paramsToViewState(sp)`（不含 localStorage 退回，因 URL 已是真實來源）——可接受：setFilters 後若 URL 原本無 view，仍維持預設 group，與使用者當下畫面一致（畫面 view 來自 viewState memo，但 setFilters 不應改 view）。**若測試「setFilters 保留 view」失敗**（URL 原無 view 但畫面因 localStorage 顯示 table），改為：`setFilters` 也帶入目前 `viewState`（從外部傳入或由 hook 內 viewState 閉包提供）。實作者請以該測試為準，必要時把 `viewState` 納入 `setFilters` 的合併來源。

- [ ] **Step 4: 跑測試確認通過**（若「setFilters 保留 view」因 localStorage 來源不一致而失敗，依 Step 3 註記改用 hook 內 `viewState` 作為合併來源）

Run: `cd frontend && npx vitest run src/features/search/hooks/useSearchParamsState.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/hooks/useSearchParamsState.ts frontend/src/features/search/hooks/useSearchParamsState.test.tsx
git commit -m "feat(frontend): Phase2b useSearchParamsState 納入 viewState 與 localStorage"
```

---

### Task 6: `components/Highlight.tsx` — 高亮渲染小元件

**Files:**
- Create: `frontend/src/features/search/components/Highlight.tsx`
- Test: `frontend/src/features/search/components/Highlight.test.tsx`

**Interfaces:**
- Consumes: `highlightSegments`（Task 2）
- Produces: `<Highlight text={string} terms={string[]} />`（命中段以 `<mark>` 純節點渲染）

- [ ] **Step 1: 寫測試** `Highlight.test.tsx`

```tsx
import { test, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Highlight } from './Highlight'

test('命中段渲染為 <mark>', () => {
  const { container } = render(<Highlight text="AI server" terms={['AI']} />)
  const marks = container.querySelectorAll('mark')
  expect(marks).toHaveLength(1)
  expect(marks[0].textContent).toBe('AI')
})

test('無 terms 不產生 mark', () => {
  const { container } = render(<Highlight text="hello" terms={[]} />)
  expect(container.querySelectorAll('mark')).toHaveLength(0)
  expect(container.textContent).toBe('hello')
})

test('XSS：注入字串以純文字呈現、不產生 script', () => {
  const { container } = render(
    <Highlight text="<script>alert(1)</script>x" terms={['x']} />,
  )
  expect(container.querySelector('script')).toBeNull()
  expect(container.textContent).toContain('<script>alert(1)</script>')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/Highlight.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作** `Highlight.tsx`

```tsx
import { Fragment } from 'react'
import { highlightSegments } from '../lib/highlight'

interface HighlightProps {
  text: string
  terms: string[]
}

export function Highlight({ text, terms }: HighlightProps) {
  const segs = highlightSegments(text, terms)
  return (
    <>
      {segs.map((s, i) =>
        s.mark ? <mark key={i}>{s.text}</mark> : <Fragment key={i}>{s.text}</Fragment>,
      )}
    </>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components/Highlight.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/Highlight.tsx frontend/src/features/search/components/Highlight.test.tsx
git commit -m "feat(frontend): Phase2b Highlight 元件（mark 純節點）"
```

---

### Task 7: `components/targets.ts` — targetsSummary 純函式

**Files:**
- Create: `frontend/src/features/search/components/targets.ts`
- Test: `frontend/src/features/search/components/targets.test.ts`

**Interfaces:**
- Produces: `targetsSummary(row: Row): string`（表格「標的」欄純文字；無標的回 `'—'`）

- [ ] **Step 1: 寫測試** `targets.test.ts`

```ts
import { test, expect } from 'vitest'
import { targetsSummary } from './targets'
import type { Row } from '../lib/normalize'

const mk = (over: Partial<Row>): Row =>
  ({ report_id: 'x', file_name: 'f', ...over }) as Row

test('無標的回 —', () => {
  expect(targetsSummary(mk({}))).toBe('—')
})

test('個股列前三檔、超過加 …', () => {
  expect(
    targetsSummary(mk({ relates_stock: true, stock_targets: ['2330', '2317', '2454', '3008'] })),
  ).toBe('個股 2330、2317、2454…')
})

test('個股無清單回「個股」', () => {
  expect(targetsSummary(mk({ relates_stock: true, stock_targets: null }))).toBe('個股')
})

test('個股 + 期貨以全形空格相接', () => {
  expect(
    targetsSummary(mk({ relates_stock: true, stock_targets: ['2330'], relates_futures: true, futures_targets: ['TX'] })),
  ).toBe('個股 2330　期貨 TX')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/targets.test.ts`
Expected: FAIL

- [ ] **Step 3: 實作**（port `render.js` targetsSummary）

```ts
import type { Row } from '../lib/normalize'

// 表格「標的」欄摘要（純文字；個股/期貨各列前三、超過加 …；無標的回 —）
export function targetsSummary(r: Row): string {
  const parts: string[] = []
  if (r.relates_stock) {
    const t = (r.stock_targets ?? []).filter(Boolean)
    parts.push(t.length ? `個股 ${t.slice(0, 3).join('、')}${t.length > 3 ? '…' : ''}` : '個股')
  }
  if (r.relates_futures) {
    const t = (r.futures_targets ?? []).filter(Boolean)
    parts.push(t.length ? `期貨 ${t.slice(0, 3).join('、')}${t.length > 3 ? '…' : ''}` : '期貨')
  }
  return parts.length ? parts.join('　') : '—'
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components/targets.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/targets.ts frontend/src/features/search/components/targets.test.ts
git commit -m "feat(frontend): Phase2b targetsSummary 純函式（表格標的欄）"
```

---

### Task 8: `components/TableView.tsx` — 可排序表格

**Files:**
- Create: `frontend/src/features/search/components/TableView.tsx`
- Test: `frontend/src/features/search/components/TableView.test.tsx`

**Interfaces:**
- Consumes: `sortedRows`/`nextTableSort`/`TableSort`/`TableSortKey`（Task 3）、`targetsSummary`（Task 7）、`mLabel`/`mColor`/`tLabel`/`fmtDate`（meta）
- Produces: `<TableView rows={Row[]} mode={'browse'|'search'} onOpen={(id)=>void} />`

**設計要點：**
- 欄位：`name 報告名稱 / market 市場 / type 類型 / date 日期 / source 來源 / (null) 標的`；mode==='search' 多 `score 相關度 / match 命中`。
- 內部 `const [sort, setSort] = useState<TableSort>({ key: null, dir: 'asc' })`；`useEffect(() => setSort({ key: null, dir: 'asc' }), [rows])`（rows 參照變更時重置，對齊 vanilla）。
- 可排序 `<th>`：`role="button"` `tabIndex={0}` `aria-sort={...}`，onClick/onKeyDown(Enter/Space) → `setSort((s) => nextTableSort(s, key))`。
- 列 `<tr data-report-id role="button" tabIndex={0}>` onClick/onKeyDown → `onOpen(report_id)`。
- 標的欄 `targetsSummary(r)`；空值欄顯 `—`。

- [ ] **Step 1: 寫測試** `TableView.test.tsx`

```tsx
import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { TableView } from './TableView'
import type { Row } from '../lib/normalize'

const rows: Row[] = [
  { report_id: 'a', file_name: 'Zebra', market: 'TW', report_date: '2026-01-01' } as Row,
  { report_id: 'b', file_name: 'Apple', market: 'US', report_date: '2026-06-01' } as Row,
]
const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('渲染表頭與列', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('報告名稱')).toBeInTheDocument()
  expect(screen.getAllByRole('row')).toHaveLength(3) // 表頭 + 2 列
})

test('點報告名稱表頭升冪排序', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  fireEvent.click(screen.getByText('報告名稱'))
  const bodyRows = screen.getAllByRole('row').slice(1)
  expect(within(bodyRows[0]).getByText('Apple')).toBeInTheDocument()
})

test('再點同表頭翻為降冪', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  const th = screen.getByText('報告名稱')
  fireEvent.click(th)
  fireEvent.click(th)
  const bodyRows = screen.getAllByRole('row').slice(1)
  expect(within(bodyRows[0]).getByText('Zebra')).toBeInTheDocument()
})

test('search 模式多相關度/命中欄', () => {
  wrap(<TableView rows={rows} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByText('相關度')).toBeInTheDocument()
  expect(screen.getByText('命中')).toBeInTheDocument()
})

test('點列觸發 onOpen 帶 report_id', () => {
  const onOpen = vi.fn()
  wrap(<TableView rows={rows} mode="browse" onOpen={onOpen} />)
  fireEvent.click(within(screen.getAllByRole('row')[1]).getByText('Zebra'))
  expect(onOpen).toHaveBeenCalledWith('a')
})

test('表頭有 aria-sort', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  fireEvent.click(screen.getByText('報告名稱'))
  expect(screen.getByText(/報告名稱/).closest('th')).toHaveAttribute('aria-sort', 'ascending')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/TableView.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作** `TableView.tsx`（依設計要點；以 `className` 掛 `rtable` 供 CSS；完整欄位/排序/可點/鍵盤；標的欄用 `targetsSummary`）。所有儲存格純文字節點；空值顯 `—`。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components/TableView.test.tsx`
Expected: PASS（6/6）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/TableView.tsx frontend/src/features/search/components/TableView.test.tsx
git commit -m "feat(frontend): Phase2b TableView 可排序表格檢視"
```

---

### Task 9: `components/ViewSwitch.tsx` + `GroupBySelect.tsx`

**Files:**
- Create: `frontend/src/features/search/components/ViewSwitch.tsx`、`ViewSwitch.test.tsx`
- Create: `frontend/src/features/search/components/GroupBySelect.tsx`、`GroupBySelect.test.tsx`

**Interfaces:**
- `<ViewSwitch value={'group'|'table'} onChange={(v)=>void} />`（radiogroup：兩鈕「列表」「表格」；`role="radio"` `aria-checked`；roving tabIndex；←/→ 或 Enter/Space 切換）
- `<GroupBySelect value={'month'|'market'} onChange={(g)=>void} />`（Mantine `Select`，options 月/市場；`aria-label="分組依據"`）

- [ ] **Step 1: 寫測試**

`ViewSwitch.test.tsx`:
```tsx
import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { ViewSwitch } from './ViewSwitch'

const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('目前檢視 aria-checked=true', () => {
  wrap(<ViewSwitch value="group" onChange={vi.fn()} />)
  expect(screen.getByRole('radio', { name: '列表' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: '表格' })).toHaveAttribute('aria-checked', 'false')
})

test('點表格觸發 onChange("table")', () => {
  const onChange = vi.fn()
  wrap(<ViewSwitch value="group" onChange={onChange} />)
  fireEvent.click(screen.getByRole('radio', { name: '表格' }))
  expect(onChange).toHaveBeenCalledWith('table')
})
```

`GroupBySelect.test.tsx`:
```tsx
import { test, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { GroupBySelect } from './GroupBySelect'

const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('顯示目前分組值', () => {
  wrap(<GroupBySelect value="month" onChange={vi.fn()} />)
  expect(screen.getByLabelText('分組依據')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/ViewSwitch.test.tsx src/features/search/components/GroupBySelect.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作** `ViewSwitch.tsx`（radiogroup + roving tabIndex + 鍵盤；`className="view-switch"` 供 CSS）與 `GroupBySelect.tsx`（Mantine `Select` data=月/市場、`aria-label="分組依據"`、`allowDeselect={false}`）。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components/ViewSwitch.test.tsx src/features/search/components/GroupBySelect.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/ViewSwitch.tsx frontend/src/features/search/components/ViewSwitch.test.tsx frontend/src/features/search/components/GroupBySelect.tsx frontend/src/features/search/components/GroupBySelect.test.tsx
git commit -m "feat(frontend): Phase2b 檢視/分組切換器"
```

---

### Task 10: ResultCard 高亮 + 片段展開 + React.memo

**Files:**
- Modify: `frontend/src/features/search/components/ResultCard.tsx`
- Modify: `frontend/src/features/search/components/ResultCard.test.tsx`

**Interfaces:**
- Consumes: `<Highlight>`（Task 6）；新增 prop `terms?: string[]`（預設 `[]`）
- Produces: `ResultCard` 改為 `React.memo` 包裝；search 片段以 `<Highlight text={passage} terms={terms} />` 渲染；多段時首段顯示、其餘收合 + 「顯示其他 N 段片段」按鈕展開（state `expanded`）

**注意：**`onOpen` 整卡可點（2a 已修）；片段展開按鈕 `onClick` 須 `e.stopPropagation()` 避免觸發整卡開啟。`terms` prop 由 `ResultsView`→`GroupedList`/`DrillView` 透傳（見 Task 12）。

- [ ] **Step 1: 改測試**（保留既有；新增）

```tsx
test('search: 片段關鍵詞以 <mark> 高亮', () => {
  const row: Row = {
    ...baseRow, matchCount: 1, rank: 1, bestScore: 0.9,
    passages: [{ score: 0.9, chunk_index: 0, content: '台積電 2nm 量產' }],
  }
  const { container } = wrap(<ResultCard row={row} mode="search" terms={['台積']} onOpen={vi.fn()} />)
  expect(container.querySelector('[data-testid="passage-text"] mark')?.textContent).toBe('台積')
})

test('search: 多段顯示「顯示其他 N 段」並可展開', () => {
  const row: Row = {
    ...baseRow, matchCount: 2, rank: 1, bestScore: 0.9,
    passages: [
      { score: 0.9, chunk_index: 0, content: '第一段' },
      { score: 0.7, chunk_index: 1, content: '第二段' },
    ],
  }
  wrap(<ResultCard row={row} mode="search" terms={[]} onOpen={vi.fn()} />)
  const more = screen.getByText(/顯示其他 1 段/)
  more.click()
  expect(screen.getByText('第二段')).toBeInTheDocument()
})

test('展開按鈕不觸發整卡 onOpen', () => {
  const onOpen = vi.fn()
  const row: Row = {
    ...baseRow, matchCount: 2, rank: 1, bestScore: 0.9,
    passages: [
      { score: 0.9, chunk_index: 0, content: '第一段' },
      { score: 0.7, chunk_index: 1, content: '第二段' },
    ],
  }
  wrap(<ResultCard row={row} mode="search" terms={[]} onOpen={onOpen} />)
  screen.getByText(/顯示其他 1 段/).click()
  expect(onOpen).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/ResultCard.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作**——新增 `terms?: string[]` prop（預設 `[]`）；search 片段段落改用 `<Highlight>`；多段以 `useState(false)` 控制展開、按鈕 `onClick={(e)=>{e.stopPropagation(); setExpanded(true)}}`；首段恆顯、其餘 `expanded` 才顯；`export const ResultCard = React.memo(function ResultCard(...) {...})`（保留具名以利除錯）。其餘 2a 行為不變。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components/ResultCard.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/ResultCard.tsx frontend/src/features/search/components/ResultCard.test.tsx
git commit -m "feat(frontend): Phase2b ResultCard 片段高亮/展開 + React.memo"
```

---

### Task 11: 平價清單修正（MarketIndex / DrillView / LoadMore / GroupedList sentinel）

**Files:**
- Modify: `MarketIndex.tsx`（+ 新 `MarketIndex.test.tsx`）、`DrillView.tsx`、`LoadMore.tsx`、`GroupedList.tsx`

**Interfaces（改動後）:**
- `<MarketIndex markets={{market:string;count:number}[]} onPickMarket={(m)=>void} />`（吃 stats 全語料 count；不再用 rows 聚合）
- `<DrillView rows market mode onOpen total={number} />`（標頭加 `total` 篇數）
- `<LoadMore hasMore loading onMore remaining={number} />`（顯「載入更多（還有 N 篇）」）
- `GroupedList`：未分類群顯示維持「未分類」（已正確；確認 `key===''` label 仍為「未分類」）

- [ ] **Step 1: 寫/改測試**

`MarketIndex.test.tsx`（新）:
```tsx
import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MarketIndex } from './MarketIndex'

test('用 markets 全語料 count 顯示、點擊回傳 market', () => {
  const onPick = vi.fn()
  render(
    <MarketIndex markets={[{ market: 'TW', count: 1003 }, { market: 'US', count: 50 }]} onPickMarket={onPick} />,
  )
  expect(screen.getByText('1,003')).toBeInTheDocument()
  fireEvent.click(screen.getByText('台股'))
  expect(onPick).toHaveBeenCalledWith('TW')
})
```

`LoadMore` 既有測試補：顯示剩餘篇數
```tsx
test('顯示剩餘篇數', () => {
  render(<LoadMore hasMore loading={false} remaining={42} onMore={() => {}} />)
  expect(screen.getByTestId('load-more-btn').textContent).toContain('還有 42')
})
```

`DrillView` 既有結構補：標頭顯 total
```tsx
test('drill 標頭顯總篇數', () => {
  render(<MantineProvider><DrillView rows={[]} market="TW" mode="search" total={123} onOpen={() => {}} /></MantineProvider>)
  expect(screen.getByTestId('drill-header').textContent).toContain('123')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/MarketIndex.test.tsx src/features/search/components/LoadMore.test.tsx src/features/search/components/DrillView.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作改動**
- `MarketIndex`：props 改 `markets: {market;count}[]`；移除 rows 聚合；`markets.filter(m=>m.market)` 直接 map（已依後端 count desc）。
- `DrillView`：props 加 `total: number`；標頭在市場名後加 `<span>{total.toLocaleString()}</span>`（`data-testid="drill-header"` 內）。
- `LoadMore`：props 加 `remaining: number`；按鈕文字 `{loading ? '載入中…' : `載入更多（還有 ${remaining.toLocaleString()} 篇）`}`。
- `GroupedList`：確認 `groupLabel('','...')` 回「未分類」（已是）；無需改（sentinel 對齊以「未分類」呈現即達語意平價；保留 `key||'__empty__'` 當 React key）。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/search/components`
Expected: PASS（全元件測試綠）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/components/MarketIndex.tsx frontend/src/features/search/components/MarketIndex.test.tsx frontend/src/features/search/components/DrillView.tsx frontend/src/features/search/components/DrillView.test.tsx frontend/src/features/search/components/LoadMore.tsx frontend/src/features/search/components/LoadMore.test.tsx
git commit -m "feat(frontend): Phase2b 平價清單修正（MarketIndex/DrillView/LoadMore）"
```

---

### Task 12: ResultsView + SearchPage 整合 + e2e + 最終驗證

**Files:**
- Modify: `ResultsView.tsx`（+ `ResultsView.test.tsx`）、`SearchPage.tsx`、`SearchPage.module.css`
- Modify: `frontend/e2e/search.spec.mjs`
- Modify: `docs/REFACTOR_TODO.md`

**ResultsView 擴充：**
- props 改：`view: 'group'|'table'`（頂層）、`group`、`rows`、`mode`、`terms: string[]`、`total: number`、`markets: {market;count}[]`、`onOpen`、`onPickMarket`、`market?`
- 邏輯：`view==='table'` → `<TableView rows mode onOpen />`；否則依 `groupViewMode(group, market)`：`index`→`<MarketIndex markets onPickMarket />`、`drill`→`<DrillView rows market mode onOpen total />`、`grouped`→`<GroupedList rows group mode onOpen terms />`。`terms` 透傳給 GroupedList→ResultCard（GroupedList 加 `terms` prop 透傳）。

**SearchPage 擴充：**
- 以 `useSearchParamsState(allow)` 取 `{ filters, viewState, setFilters, setViewState }`。
- `const topView = viewState.view`；`const subView = topView === 'table' ? 'table' : groupViewMode(viewState.group, filters.market)`。
- `const terms = useMemo(() => buildTerms(filters.q), [filters.q])`。
- `const markets = stats.markets`（傳給 ResultsView 供 MarketIndex 全語料 count）。
- 版面：結果區上方放 `<ViewSwitch value={viewState.view} onChange={(v)=>setViewState({ ...viewState, view: v })} />`；`viewState.view==='group'` 時顯 `<GroupBySelect value={viewState.group} onChange={(g)=>setViewState({ ...viewState, group: g })} />`。
- `<ResultsView view={topView} group={viewState.group} rows={rows} mode={mode} terms={terms} total={total} markets={markets} onOpen={setModalId} onPickMarket={(m)=>setFilters({ ...filters, market: m })} market={filters.market} />`
- LoadMore 傳 `remaining={Math.max(0, total - rows.length)}`；index 子檢視（市場索引）**不顯 LoadMore**（對齊 vanilla：`if (gMode!=='index') appendLoadMore()`）→ `{subView !== 'index' && <LoadMore ... />}`。
- 切 table/group 不重抓：`useSearchResults(filters)` 不含 viewState（保持）。

- [ ] **Step 1: 改 `ResultsView.test.tsx`** — 加 table 分支與 terms 透傳測試

```tsx
test('view=table 渲染 TableView', () => {
  render(<MantineProvider><ResultsView view="table" group="month" rows={rows} mode="browse" terms={[]} total={2} markets={[]} onOpen={vi.fn()} onPickMarket={vi.fn()} /></MantineProvider>)
  expect(screen.getByText('報告名稱')).toBeInTheDocument()
})
```
（保留既有 grouped/index/drill 測試，調整 props 簽名。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/search/components/ResultsView.test.tsx`
Expected: FAIL

- [ ] **Step 3: 實作** ResultsView/SearchPage 擴充 + 萃取表格/切換器/`<mark>` 的 bespoke CSS 到 `SearchPage.module.css`（`.rtable`、`.view-switch`、`mark{background:#fff3bf}` 等，對齊 `index.html`）。GroupedList 加 `terms?: string[]` 透傳給 ResultCard。

- [ ] **Step 4: 跑全套單元 + build + eslint**

Run:
```bash
cd frontend && npx vitest run src
npm run build
node ./node_modules/eslint/bin/eslint.js . --report-unused-disable-directives
```
Expected: vitest 全綠、build 綠、eslint exit 0。

- [ ] **Step 5: 擴充 e2e** `frontend/e2e/search.spec.mjs`——在既有平價劇本後加（或新 test）：登入→切「表格」(`getByRole('radio',{name:'表格'})`)→點表頭排序→切「列表」→分組下拉選「市場」→市場索引可見→點一市場 drill→（搜尋）片段見 `mark`→0 console error。對 `:8098`（現行後端）跑：`SEARCH_BASE_URL=http://127.0.0.1:8098 npx playwright test frontend/e2e/search.spec.mjs`（live 由控制端跑；子代理只需確保 `--list` 收集且語法正確）。

- [ ] **Step 6: 更新 `docs/REFACTOR_TODO.md`** Phase 2 區塊：標記 2b 完成（表格/切換/高亮/平價清單），cutover 留 2c。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/search/components/ResultsView.tsx frontend/src/features/search/components/ResultsView.test.tsx frontend/src/features/search/components/GroupedList.tsx frontend/src/features/search/SearchPage.tsx frontend/src/features/search/SearchPage.module.css frontend/e2e/search.spec.mjs docs/REFACTOR_TODO.md
git commit -m "feat(frontend): Phase2b 整合檢視切換/表格/高亮 + e2e + 文件"
```

---

## Self-Review（plan vs spec）

- **Spec coverage**：表格(T3,T8)/檢視切換(T4,T5,T9,T12)/分組切換+索引/drill(T9,T11,T12)/高亮(T1,T2,T6,T10)/平價清單(T10,T11)/不 cutover(全程不改 route)/XSS(T2,T6,T10)/不重抓(T5,T12 分層) — 皆有對應任務。
- **Placeholder scan**：純函式任務(T1-T7)含完整碼；元件任務(T8-T12)給介面 + 設計要點 + 代表性測試（JSX 細節依 spec/vanilla render.js 對齊，屬實作非 placeholder）。
- **Type consistency**：`Row` 用正規化名（bestScore/matchCount/passages）+ ReportItem snake（file_name/report_date…），tableSort/targets/TableView 一致引用；`ViewState`/`TableSort` 於 T3/T4 定義、T5/T8/T12 一致；MarketIndex props 由 rows→markets 的改動在 T11 定義、T12 一致傳 `stats.markets`。
- **已知取捨**：T5 setFilters/viewState 來源一致性以測試為準（Step 3 註記）；GroupBySelect 用 Mantine Select（可及性換簡潔）；GroupedList sentinel 以「未分類」呈現達語意平價（不強對 vanilla `'—'` key）。
- **依賴順序**：T1-T4 純函式 → T5 hook → T6/T7 葉元件 → T8/T9 元件 → T10/T11 改元件 → T12 整合。每任務獨立可測、可 commit。
