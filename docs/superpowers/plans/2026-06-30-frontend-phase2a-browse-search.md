# 前端 Phase 2a 檢索頁遷移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把檢索頁的搜尋 + 分組預設檢視（含市場索引 drill-in）+ 載入更多分頁 + URL 可分享狀態 + 篩選側欄遷進 React SPA，掛 `/app/search`，達 live `/` 平價但不 cutover。

**Architecture:** 方案 A 忠實元件化移植。伺服器狀態用 TanStack Query（`useInfiniteQuery` offset 分頁）；UI/URL 狀態用 react-router `useSearchParams`；分組/排序/正規化邏輯抽成 typed 純函式 + Vitest；結果區沿用既有 bespoke CSS 求逐像素平價；外殼用 Mantine。後端 `/api/*` 完全不動。

**Tech Stack:** React 19.2.7 / Vite 8 / react-router 8 / Mantine 9 / @tanstack/react-query 5 / Zod 4 / Vitest 4 / TypeScript，Node 22.22+。

## Global Constraints

- 後端 `/api/*` 行為、`db/**`、`app/**` **一律不動**；無 schema 變更。
- 棧鎖定 Phase 0：**不引入 Zustand/Redux**；UI 狀態用 React state/context + react-router `searchParams`。
- 所有後端 Optional 欄位的 Zod 一律 `.nullish()`（記取 PR #40 M1：`.optional()` 拒 null 會炸頁）。
- **禁止** `dangerouslySetInnerHTML` 注入未轉義內容；文字以純節點渲染。
- **不 cutover**：舊 `/` 不動，`/app/search` 為平行頁。
- 路徑前綴：所有新檔在 `frontend/src/features/search/`，route 改 `frontend/src/App.tsx`。
- 測試指令本分支用 `cd frontend && npx vitest run src`（本分支由 main 開，暫無 Part A 的 vitest `include` 設定，須以 `src` 範圍避開 Playwright `e2e/` 收集）。每個 logic 任務結束跑該指令；最終跑 `npm run build`（tsc + vite）。
- commit 訊息用 Conventional Commits + 繁中 scope，結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 視覺：結果區（卡片/分組/索引/drill）**逐像素對齊** live；bespoke CSS 從 `web/static/index.html` inline `<style>` 萃取到 `SearchPage.module.css`。

---

## File Structure

```
frontend/src/features/search/
  schemas.ts            # Zod: stats/reports/search + 型別、統一 Row 型別
  api.ts                # typed fetchers: getStats/getReports/getSearch（用 lib/api 的 getJSON）
  lib/
    filters.ts          # Filters 型別、預設、URL<->Filters 映射、白名單驗證
    normalize.ts        # normalizeRow: ReportListItem|ReportResult -> Row
    grouping.ts         # groupViewMode / groupRows / groupKey / groupLabel
    terms.ts            # buildTerms（2a 建好供 2b 高亮用）
    *.test.ts           # 各純函式測試
  hooks/
    useSearchParamsState.ts   # 讀寫 URL searchParams <-> Filters/q
    useSearchResults.ts       # useInfiniteQuery 包 reports/search
    *.test.tsx
  components/
    SearchBar.tsx
    FilterSidebar.tsx          # 可複用（Phase 3 ask 共用）
    ResultsMeta.tsx
    ResultsView.tsx            # 依 groupViewMode 切 GroupedList/MarketIndex/DrillView
    GroupedList.tsx
    MarketIndex.tsx
    DrillView.tsx
    ResultCard.tsx
    LoadMore.tsx
    ReportDetailModal.tsx      # 最小唯讀詳情（/api/report/{id}/full）
    states.tsx                 # EmptyState / ErrorState
    *.test.tsx
  SearchPage.tsx               # route 元件，組合上述
  SearchPage.module.css        # 萃取自 index.html 的結果區 bespoke CSS
frontend/src/App.tsx           # 新增 /app/search route
frontend/e2e/search.spec.mjs   # Playwright 平價劇本
```

---

## Task 1: Zod schemas 與型別

**Files:**
- Create: `frontend/src/features/search/schemas.ts`
- Test: `frontend/src/features/search/schemas.test.ts`

**Interfaces:**
- Produces:
  - `statsSchema`, `StatsResponse`
  - `reportsSchema`, `ReportsResponse`, `reportItemSchema`, `ReportItem`
  - `searchSchema`, `SearchResponse`, `reportResultSchema`, `ReportResult`, `passageSchema`
  - 統一 `Row` 型別（見 normalize，Task 3 引用）：欄位 = ReportItem 全欄 + 選用 `rank?`/`bestScore?`/`matchCount?`/`passages?`

- [ ] **Step 1: 寫失敗測試** — `schemas.test.ts`

```ts
import { reportsSchema, searchSchema, statsSchema } from './schemas'

test('statsSchema 解析最小回應', () => {
  const out = statsSchema.parse({
    total_reports: 10, total_chunks: 100,
    markets: [{ market: 'TW', count: 5 }],
    instrument_types: [{ type: '個股', count: 3 }],
    report_types: [{ type: '法說會', count: 2 }],
    username: 'u',
  })
  expect(out.markets[0].market).toBe('TW')
})

test('reportsSchema 接受 null 欄位（nullish）', () => {
  const out = reportsSchema.parse({
    total: 1, offset: 0,
    items: [{
      report_id: 'r1', file_name: 'f', market: null, source: null, summary: null,
      report_date: null, report_type: null, instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    }],
  })
  expect(out.items[0].report_id).toBe('r1')
})

test('searchSchema 含 passages 與 rank/best_score', () => {
  const out = searchSchema.parse({
    query: 'q', market: null, total: 1,
    results: [{
      rank: 1, report_id: 'r1', file_name: 'f', market: 'TW', source: null, summary: null,
      report_date: null, report_type: null, instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
      best_score: 0.9, match_count: 2, passages: [{ score: 0.8, chunk_index: 0, content: 'x' }],
    }],
  })
  expect(out.results[0].passages[0].chunk_index).toBe(0)
})
```

- [ ] **Step 2: 跑測試確認失敗** — `cd frontend && npx vitest run src/features/search/schemas.test.ts` → FAIL（模組不存在）

- [ ] **Step 3: 實作 `schemas.ts`**

```ts
import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number(),
  total_chunks: z.number(),
  markets: z.array(z.object({ market: z.string(), count: z.number() })),
  instrument_types: z.array(z.object({ type: z.string(), count: z.number() })),
  report_types: z.array(z.object({ type: z.string(), count: z.number() })),
  username: z.string().nullish(),
})
export type StatsResponse = z.infer<typeof statsSchema>

export const reportItemSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  source: z.string().nullish(),
  summary: z.string().nullish(),
  report_date: z.string().nullish(),
  report_type: z.string().nullish(),
  instrument_types: z.array(z.string()).nullish(),
  relates_stock: z.boolean().nullish(),
  relates_futures: z.boolean().nullish(),
  stock_targets: z.array(z.string()).nullish(),
  futures_targets: z.array(z.string()).nullish(),
})
export type ReportItem = z.infer<typeof reportItemSchema>

export const reportsSchema = z.object({
  total: z.number(),
  offset: z.number(),
  items: z.array(reportItemSchema),
})
export type ReportsResponse = z.infer<typeof reportsSchema>

export const passageSchema = z.object({
  score: z.number(),
  chunk_index: z.number(),
  content: z.string(),
})

export const reportResultSchema = reportItemSchema.extend({
  rank: z.number(),
  best_score: z.number(),
  match_count: z.number(),
  passages: z.array(passageSchema),
})
export type ReportResult = z.infer<typeof reportResultSchema>

export const searchSchema = z.object({
  query: z.string(),
  market: z.string().nullish(),
  total: z.number(),
  results: z.array(reportResultSchema),
})
export type SearchResponse = z.infer<typeof searchSchema>
```

- [ ] **Step 4: 跑測試確認通過** — `cd frontend && npx vitest run src/features/search/schemas.test.ts` → PASS
- [ ] **Step 5: commit** — `git add frontend/src/features/search/schemas.ts frontend/src/features/search/schemas.test.ts && git commit`（`feat(frontend): Phase2a Zod schema 與型別`）

---

## Task 2: typed API fetchers

**Files:**
- Create: `frontend/src/features/search/api.ts`
- Test: `frontend/src/features/search/api.test.ts`
- Reference: `frontend/src/lib/api.ts`（既有 `getJSON(path, schema, init)` + `redirectToLogin` + `ApiError`）

**Interfaces:**
- Consumes: `getJSON` from `../../lib/api`; schemas from `./schemas`
- Produces:
  - `getStats(): Promise<StatsResponse>`
  - `getReports(params: ReportsParams): Promise<ReportsResponse>`
  - `getSearch(params: SearchParams): Promise<SearchResponse>`
  - 型別 `ReportsParams`（market?, instrument_type?, relates_stock?, relates_futures?, report_type?, sort?, limit, offset）、`SearchParams`（= ReportsParams + q, passages）
  - `buildQuery(params): string`（純函式：略過 undefined/null/「全部」）

- [ ] **Step 1: 寫失敗測試** — 測 `buildQuery` 略過空值、bool 轉 `1`、組出正確 querystring。

```ts
import { buildQuery } from './api'

test('buildQuery 略過 undefined/全部，bool→1', () => {
  const qs = buildQuery({ q: 'AI', market: '全部', relates_stock: true, sort: 'relevance', limit: 50, offset: 0, instrument_type: undefined })
  const p = new URLSearchParams(qs)
  expect(p.get('q')).toBe('AI')
  expect(p.has('market')).toBe(false)      // 「全部」略過
  expect(p.get('stock')).toBe('1')
  expect(p.get('sort')).toBe('relevance')
  expect(p.get('limit')).toBe('50')
  expect(p.has('instrument_type')).toBe(false)
})
```

> 注意：後端搜尋 query 參數名是 `relates_stock`/`relates_futures`（見 server.py），但 URL 分享參數用 `stock`/`futures`。**API 層用後端參數名**；URL 層（Task 3）用分享參數名。修正上測：API querystring 用 `relates_stock=true`。改測為：

```ts
  expect(p.get('relates_stock')).toBe('true')
```

- [ ] **Step 2: 跑測試確認失敗** — `npx vitest run src/features/search/api.test.ts` → FAIL
- [ ] **Step 3: 實作 `api.ts`**

```ts
import { getJSON } from '../../lib/api'
import { reportsSchema, searchSchema, statsSchema, type ReportsResponse, type SearchResponse, type StatsResponse } from './schemas'

export interface ReportsParams {
  market?: string; instrument_type?: string; relates_stock?: boolean
  relates_futures?: boolean; report_type?: string
  sort?: string; limit: number; offset: number
}
export interface SearchParams extends ReportsParams { q: string; passages: number }

const SKIP = (v: unknown) => v == null || v === '' || v === '全部'

export function buildQuery(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (SKIP(v)) continue
    u.set(k, String(v))
  }
  return u.toString()
}

export function getStats(): Promise<StatsResponse> {
  return getJSON('/api/stats', statsSchema, { cache: 'no-store' })
}
export function getReports(p: ReportsParams): Promise<ReportsResponse> {
  return getJSON(`/api/reports?${buildQuery({ ...p })}`, reportsSchema)
}
export function getSearch(p: SearchParams): Promise<SearchResponse> {
  return getJSON(`/api/search?${buildQuery({ ...p })}`, searchSchema)
}
```

- [ ] **Step 4: 跑測試確認通過** → PASS
- [ ] **Step 5: commit**（`feat(frontend): Phase2a typed API fetchers`）

---

## Task 3: 純函式 — filters 與 normalize

**Files:**
- Create: `frontend/src/features/search/lib/filters.ts`, `frontend/src/features/search/lib/normalize.ts`
- Test: `.../filters.test.ts`, `.../normalize.test.ts`
- Reference: `web/static/app/url.js`（參數集合與白名單驗證）、`web/static/app/state.js`

**Interfaces:**
- Produces (`filters.ts`):
  - `interface Filters { q: string; market: string; instrument: string; stock: boolean; futures: boolean; type: string; sort: string }`
  - `DEFAULT_FILTERS: Filters`（market/instrument/type = `'全部'`、stock/futures=false、sort=''、q=''）
  - `filtersToSearchParams(f: Filters): URLSearchParams`（對齊 url.js：q/market(≠全部)/instrument(≠全部)/stock=1/futures=1/type(≠全部)/sort）
  - `searchParamsToFilters(sp: URLSearchParams, allow: Allowlists): Filters`（白名單驗證，不合法回 `'全部'`；sort 依有無 q 取 SEARCH_SORTS/BROWSE_SORTS 預設）
  - `interface Allowlists { markets: string[]; instruments: string[]; types: string[] }`
  - `SEARCH_SORTS = ['relevance','date_desc','date_asc']`、`BROWSE_SORTS = ['date_desc','date_asc']`
- Produces (`normalize.ts`):
  - `interface Row extends ReportItem { rank?: number; bestScore?: number; matchCount?: number; passages?: Passage[] }`
  - `normalizeItem(i: ReportItem): Row`、`normalizeResult(r: ReportResult): Row`

- [ ] **Step 1: 寫失敗測試**（filters）— round-trip + 白名單

```ts
import { DEFAULT_FILTERS, filtersToSearchParams, searchParamsToFilters } from './filters'

const allow = { markets: ['TW','US'], instruments: ['個股'], types: ['法說會'] }

test('filters round-trip 略過預設值', () => {
  const sp = filtersToSearchParams({ ...DEFAULT_FILTERS, q: 'AI', market: 'TW', stock: true, sort: 'relevance' })
  expect(sp.get('q')).toBe('AI'); expect(sp.get('market')).toBe('TW')
  expect(sp.get('stock')).toBe('1'); expect(sp.has('instrument')).toBe(false)
})

test('searchParamsToFilters 白名單擋非法市場', () => {
  const f = searchParamsToFilters(new URLSearchParams('market=ZZ&q=AI'), allow)
  expect(f.market).toBe('全部')      // 非白名單 → 全部
  expect(f.q).toBe('AI')
  expect(f.sort).toBe('relevance')   // 有 q → SEARCH 預設
})

test('無 q 時 sort 預設 date_desc', () => {
  const f = searchParamsToFilters(new URLSearchParams(''), allow)
  expect(f.sort).toBe('date_desc')
})
```

- [ ] **Step 2: 跑測試確認失敗** → FAIL
- [ ] **Step 3: 實作 `filters.ts`**（依 url.js 的 `pickAllowed`/sort 預設語意；完整碼）

```ts
export interface Filters {
  q: string; market: string; instrument: string
  stock: boolean; futures: boolean; type: string; sort: string
}
export interface Allowlists { markets: string[]; instruments: string[]; types: string[] }

export const SEARCH_SORTS = ['relevance', 'date_desc', 'date_asc']
export const BROWSE_SORTS = ['date_desc', 'date_asc']
export const DEFAULT_FILTERS: Filters = {
  q: '', market: '全部', instrument: '全部', stock: false, futures: false, type: '全部', sort: '',
}

export function filtersToSearchParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams()
  if (f.q.trim()) sp.set('q', f.q.trim())
  if (f.market !== '全部') sp.set('market', f.market)
  if (f.instrument !== '全部') sp.set('instrument', f.instrument)
  if (f.stock) sp.set('stock', '1')
  if (f.futures) sp.set('futures', '1')
  if (f.type !== '全部') sp.set('type', f.type)
  if (f.sort) sp.set('sort', f.sort)
  return sp
}

const pick = (v: string | null, allow: string[]) => (v && allow.includes(v) ? v : '全部')

export function searchParamsToFilters(sp: URLSearchParams, allow: Allowlists): Filters {
  const q = (sp.get('q') ?? '').trim()
  const sorts = q ? SEARCH_SORTS : BROWSE_SORTS
  const rawSort = sp.get('sort')
  const sort = rawSort && sorts.includes(rawSort) ? rawSort : sorts[0]
  return {
    q,
    market: pick(sp.get('market'), allow.markets),
    instrument: pick(sp.get('instrument'), allow.instruments),
    stock: sp.get('stock') === '1',
    futures: sp.get('futures') === '1',
    type: pick(sp.get('type'), allow.types),
    sort,
  }
}
```

- [ ] **Step 4: 寫失敗測試（normalize）+ 實作 `normalize.ts`**

```ts
// normalize.test.ts
import { normalizeItem, normalizeResult } from './normalize'
test('normalizeItem 無 rank/score', () => {
  const r = normalizeItem({ report_id: 'r', file_name: 'f', market: 'TW', source: null, summary: null, report_date: null, report_type: null, instrument_types: null, relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null })
  expect(r.rank).toBeUndefined(); expect(r.report_id).toBe('r')
})
test('normalizeResult 帶 rank/bestScore/matchCount/passages', () => {
  const r = normalizeResult({ rank: 3, best_score: 0.7, match_count: 2, passages: [{ score: 0.5, chunk_index: 1, content: 'c' }], report_id: 'r', file_name: 'f', market: 'TW', source: null, summary: null, report_date: null, report_type: null, instrument_types: null, relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null })
  expect(r.rank).toBe(3); expect(r.bestScore).toBe(0.7); expect(r.matchCount).toBe(2); expect(r.passages?.length).toBe(1)
})
```

```ts
// normalize.ts
import type { Passage } from './../schemas' // 若未匯出 Passage 型別，於 schemas 補 export type Passage = z.infer<typeof passageSchema>
import type { ReportItem, ReportResult } from '../schemas'

export interface Row extends ReportItem {
  rank?: number; bestScore?: number; matchCount?: number; passages?: Passage[]
}
export const normalizeItem = (i: ReportItem): Row => ({ ...i })
export const normalizeResult = (r: ReportResult): Row => {
  const { rank, best_score, match_count, passages, ...rest } = r
  return { ...rest, rank, bestScore: best_score, matchCount: match_count, passages }
}
```

> 補：在 `schemas.ts` 加 `export type Passage = z.infer<typeof passageSchema>`（Task 1 已建 passageSchema）。

- [ ] **Step 5: 跑測試確認通過 + commit**（`feat(frontend): Phase2a filters/normalize 純函式`）

---

## Task 4: 純函式 — grouping 與 terms

**Files:**
- Create: `frontend/src/features/search/lib/grouping.ts`, `frontend/src/features/search/lib/terms.ts`
- Test: `.../grouping.test.ts`, `.../terms.test.ts`
- Reference: `web/static/app/render.js`（groupKey/groupLabel/grouped/index/drill）、`web/static/app/state.js`（groupViewMode）、既有 `web/static/app/state.test.mjs`

**Interfaces:**
- Produces (`grouping.ts`):
  - `type GroupView = 'grouped' | 'index' | 'drill'`
  - `groupViewMode(group: 'month'|'market', market: string): GroupView`（market≠全部 & group=market → drill；group=market & market=全部 → index；否則 grouped）
  - `groupKey(row: Row, group: 'month'|'market'): string`、`groupLabel(key, group): string`
  - `groupRows(rows: Row[], group): { key: string; label: string; rows: Row[] }[]`（保序）
- Produces (`terms.ts`): `buildTerms(q: string): string[]`（對齊 render.js buildTerms：去空白/標點分詞、去重、長度過濾）

- [ ] **Step 1: 寫失敗測試（grouping）** — 對齊 state.test.mjs 三案 + groupRows 保序

```ts
import { groupViewMode, groupRows } from './grouping'
test('groupViewMode 路由（對齊 state.test.mjs）', () => {
  expect(groupViewMode('market', '全部')).toBe('index')
  expect(groupViewMode('market', 'TW')).toBe('drill')
  expect(groupViewMode('month', '全部')).toBe('grouped')
})
test('groupRows 依 market 分組保序', () => {
  const rows = [{ report_id: '1', market: 'TW' }, { report_id: '2', market: 'US' }, { report_id: '3', market: 'TW' }] as any
  const g = groupRows(rows, 'market')
  expect(g.map((x) => x.key)).toEqual(['TW', 'US'])
  expect(g[0].rows.length).toBe(2)
})
```

- [ ] **Step 2–4: 確認失敗 → 實作 → 通過**（grouping.ts 依 render.js 邏輯：month groupKey 取 `report_date` 前 7 碼 `YYYY-MM`，null→「未分類」；market groupKey 取 `market`，null→「未分類」；groupLabel 對 month 轉「YYYY 年 MM 月」、對 market 用 meta 標籤——meta 對照表於 Task 7 引入，grouping 只回原始 key，label 對照延後到元件。**因此 groupRows 回 `key` 即可，label 由元件用 meta 算**，避免 grouping 依賴 meta。簡化：`groupRows` 只回 `{ key, rows }`，label 留元件。據此調整上方 interface 與測試移除 label 斷言。）

> 決策：`groupRows(rows, group) => { key: string; rows: Row[] }[]`（不含 label）。月份 key=`report_date?.slice(0,7) ?? ''`（空→末尾「未分類」群）；市場 key=`market ?? ''`。元件層再用 meta 把 key 轉顯示 label。terms.ts 的 `buildTerms` 比照 render.js。

- [ ] **Step 5: terms 測試 + 實作 + commit**（`feat(frontend): Phase2a grouping/terms 純函式`）

```ts
// terms.test.ts
import { buildTerms } from './terms'
test('buildTerms 去重分詞', () => {
  expect(buildTerms('AI 伺服器 AI')).toEqual(['AI', '伺服器'])
  expect(buildTerms('')).toEqual([])
})
```

---

## Task 5: hooks — URL 狀態與資料抓取

**Files:**
- Create: `frontend/src/features/search/hooks/useSearchParamsState.ts`, `.../hooks/useSearchResults.ts`
- Test: `.../hooks/useSearchParamsState.test.tsx`, `.../hooks/useSearchResults.test.tsx`

**Interfaces:**
- Consumes: `filters.ts`、`api.ts`、`normalize.ts`、react-router `useSearchParams`、`@tanstack/react-query` `useInfiniteQuery`
- Produces:
  - `useSearchParamsState(allow: Allowlists): { filters: Filters; setFilters(next: Filters): void }`（`setFilters` 以 `filtersToSearchParams` 寫回 URL，`replace: true`）
  - `useSearchResults(filters: Filters): { rows: Row[]; total: number; mode: 'browse'|'search'; isLoading; isError; hasMore; fetchNextPage; refetch }`
    - `mode = filters.q ? 'search' : 'browse'`
    - `useInfiniteQuery`：`queryKey=['search-results', filters]`；`queryFn` 依 mode 呼 getSearch/getReports（limit=50, offset=pageParam, passages=4 for search）；`initialPageParam: 0`；`getNextPageParam`：`loaded < total ? loaded : undefined`
    - `rows`：攤平各頁 + normalize（search→normalizeResult，browse→normalizeItem）

- [ ] **Step 1: 寫失敗測試（useSearchParamsState）** — wrap in `MemoryRouter`，初值由 URL 還原、setFilters 改 URL。

```tsx
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useSearchParamsState } from './useSearchParamsState'
const allow = { markets: ['TW'], instruments: [], types: [] }
const wrap = (initial: string) => ({ children }: { children: React.ReactNode }) => (
  <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
)
test('由 URL 還原並可寫回', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), { wrapper: wrap('/app/search?q=AI&market=TW') })
  expect(result.current.filters.q).toBe('AI')
  expect(result.current.filters.market).toBe('TW')
  act(() => result.current.setFilters({ ...result.current.filters, market: '全部' }))
  expect(result.current.filters.market).toBe('全部')
})
```

- [ ] **Step 2–4: 確認失敗 → 實作 → 通過**

```ts
// useSearchParamsState.ts
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { type Allowlists, type Filters, filtersToSearchParams, searchParamsToFilters } from '../lib/filters'

export function useSearchParamsState(allow: Allowlists) {
  const [sp, setSp] = useSearchParams()
  const filters = useMemo(() => searchParamsToFilters(sp, allow), [sp, allow])
  const setFilters = useCallback(
    (next: Filters) => setSp(filtersToSearchParams(next), { replace: true }),
    [setSp],
  )
  return { filters, setFilters }
}
```

- [ ] **Step 5: useSearchResults 測試 + 實作 + commit**

```tsx
// useSearchResults.test.tsx — mock api，驗 browse vs search 切換、攤平、hasMore
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as api from '../api'
import { useSearchResults } from './useSearchResults'
import { DEFAULT_FILTERS } from '../lib/filters'
// 注：以 vi.spyOn(api,'getReports')/('getSearch') mock，wrapper 包 QueryClientProvider(retry:false)
test('browse 模式攤平 items 並算 hasMore', async () => {
  vi.spyOn(api, 'getReports').mockResolvedValue({ total: 80, offset: 0, items: [{ report_id: 'r1', file_name: 'f', market: 'TW', source: null, summary: null, report_date: null, report_type: null, instrument_types: null, relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null }] })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { result } = renderHook(() => useSearchResults(DEFAULT_FILTERS), { wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider> })
  await waitFor(() => expect(result.current.rows.length).toBe(1))
  expect(result.current.mode).toBe('browse')
  expect(result.current.hasMore).toBe(true) // 1 < 80
})
```

```ts
// useSearchResults.ts
import { useInfiniteQuery } from '@tanstack/react-query'
import { getReports, getSearch } from '../api'
import { normalizeItem, normalizeResult, type Row } from '../lib/normalize'
import type { Filters } from '../lib/filters'

const PAGE = 50
export function useSearchResults(filters: Filters) {
  const mode: 'browse' | 'search' = filters.q ? 'search' : 'browse'
  const q = useInfiniteQuery({
    queryKey: ['search-results', filters],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const base = {
        market: filters.market, instrument_type: filters.instrument,
        relates_stock: filters.stock || undefined, relates_futures: filters.futures || undefined,
        report_type: filters.type, sort: filters.sort, limit: PAGE, offset: pageParam as number,
      }
      return mode === 'search'
        ? { ...(await getSearch({ ...base, q: filters.q, passages: 4 })), kind: 'search' as const }
        : { ...(await getReports(base)), kind: 'browse' as const }
    },
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + (p.kind === 'search' ? p.results.length : p.items.length), 0)
      return loaded < last.total ? loaded : undefined
    },
    retry: false, refetchOnWindowFocus: false,
  })
  const rows: Row[] = (q.data?.pages ?? []).flatMap((p) =>
    p.kind === 'search' ? p.results.map(normalizeResult) : p.items.map(normalizeItem),
  )
  const total = q.data?.pages[0]?.total ?? 0
  return {
    rows, total, mode,
    isLoading: q.isLoading, isError: q.isError,
    hasMore: Boolean(q.hasNextPage), fetchNextPage: q.fetchNextPage, refetch: q.refetch,
  }
}
```

- [ ] commit（`feat(frontend): Phase2a URL 狀態與資料抓取 hooks`）

---

## Task 6: FilterSidebar（可複用篩選側欄）

**Files:**
- Create: `frontend/src/features/search/components/FilterSidebar.tsx`, `frontend/src/features/search/components/meta.ts`
- Test: `.../components/FilterSidebar.test.tsx`
- Reference: `web/static/app/chips.js`、`web/static/app/meta.js`、`index.html` 側欄區（#chips/#instrChips/#subjToggles/#typeChips/#sortChips）

**Interfaces:**
- Consumes: `Filters`、`StatsResponse`（提供 chip 來源）、`SEARCH_SORTS`/`BROWSE_SORTS`
- Produces: `<FilterSidebar stats={StatsResponse} filters={Filters} onChange={(f: Filters) => void} />`；`meta.ts`（移植 `meta.js`：`mLabel/mColor/iLabel/tLabel/fmtDate`）；`activeFilterCount(filters): number`

- [ ] **Step 1: 寫失敗測試** — 點市場 chip 觸發 onChange、sort 集合隨 q 切換、重置清空。

```tsx
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { FilterSidebar } from './FilterSidebar'
import { DEFAULT_FILTERS } from '../lib/filters'
const stats = { total_reports: 1, total_chunks: 1, markets: [{ market: 'TW', count: 5 }], instrument_types: [], report_types: [], username: 'u' }
test('點市場 chip 觸發 onChange', async () => {
  const onChange = vi.fn()
  render(<MantineProvider><FilterSidebar stats={stats as any} filters={DEFAULT_FILTERS} onChange={onChange} /></MantineProvider>)
  ;(await screen.findByRole('button', { name: /TW/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ market: 'TW' }))
})
```

- [ ] **Step 2–4:** 失敗 → 實作（chips 用 Mantine `Chip`/`Button`，群組：市場/商品/個股·期貨 toggle/類型/排序；sort 選項依 `filters.q ? SEARCH_SORTS : BROWSE_SORTS`；顏色/標籤用 `meta.ts`）→ 通過
- [ ] **Step 5: commit**（`feat(frontend): Phase2a 可複用篩選側欄`）

> 視覺：chip 樣式對齊 `index.html` 既有 `.chip`/`.chip[aria-pressed]`；萃取對應規則進 `SearchPage.module.css`（Task 9 統一萃取，本任務可先用 Mantine 預設、Task 9 收斂視覺）。

---

## Task 7: 結果區元件（卡片 / 分組 / 索引 / drill / loadmore / meta / states）

**Files:**
- Create: `frontend/src/features/search/components/ResultCard.tsx`, `GroupedList.tsx`, `MarketIndex.tsx`, `DrillView.tsx`, `LoadMore.tsx`, `ResultsMeta.tsx`, `ResultsView.tsx`, `states.tsx`
- Test: `.../components/ResultsView.test.tsx`, `.../components/ResultCard.test.tsx`
- Reference: `render.js`（gridCard/listRow/groupedHtml/marketIndexHtml/drillHtml/appendLoadMore/updateViewBar）

**Interfaces:**
- Consumes: `Row`、`GroupView`、`groupRows`、`meta.ts`
- Produces:
  - `<ResultCard row={Row} mode={'browse'|'search'} onOpen={(id: string) => void} />`
  - `<GroupedList rows group onOpen />`、`<MarketIndex rows onPickMarket={(m)=>void} />`、`<DrillView rows market group onOpen />`
  - `<ResultsView view={GroupView} rows group mode onOpen onPickMarket />`（依 view 切分支）
  - `<LoadMore hasMore loading onMore />`、`<ResultsMeta mode q market total />`、`<EmptyState onReset />`、`<ErrorState onRetry />`

- [ ] **Step 1: 寫失敗測試（ResultsView）** — grouped 渲染群組標題；index 渲染市場清單；drill 渲染單市場；search 模式 ResultCard 顯示命中數。

```tsx
test('ResultsView grouped 顯示分組標題與卡片', () => {
  const rows = [{ report_id: '1', file_name: 'f1', market: 'TW', report_date: '2026-06-01' }, { report_id: '2', file_name: 'f2', market: 'TW', report_date: '2026-05-01' }] as any
  render(<MantineProvider><ResultsView view="grouped" group="month" rows={rows} mode="browse" onOpen={() => {}} onPickMarket={() => {}} /></MantineProvider>)
  expect(screen.getByText('f1')).toBeInTheDocument()
})
```

- [ ] **Step 2–4:** 失敗 → 實作各元件（薄殼，分組用 `groupRows`，label 用 `meta.ts`；卡片 browse 整列可點、search 顯示 `matchCount`/`bestScore` 與 passages 純文字）→ 通過
- [ ] **Step 5: commit**（`feat(frontend): Phase2a 結果區元件（分組/索引/drill/卡片）`）

---

## Task 8: SearchBar 與最小報告詳情 modal

**Files:**
- Create: `frontend/src/features/search/components/SearchBar.tsx`, `ReportDetailModal.tsx`
- Test: `.../components/SearchBar.test.tsx`
- Reference: `search.js`（debounce 450ms/Enter/clear）、`/api/report/{id}/full`

**Interfaces:**
- Produces:
  - `<SearchBar value={string} onSubmit={(q: string) => void} onClear={() => void} />`（debounce 450ms：值變 → debounce 後 onSubmit；Enter 立即 onSubmit；clear → onClear）
  - `<ReportDetailModal reportId={string|null} onClose={() => void} />`（開啟時 fetch `/api/report/{id}/full`，純文字渲染，Mantine `Modal`）

- [ ] **Step 1: 寫失敗測試** — fake timers 驗 debounce 觸發一次 onSubmit；Enter 立即觸發。
- [ ] **Step 2–4:** 失敗 → 實作 → 通過（debounce 用 `useRef` + `setTimeout`，卸載清除）
- [ ] **Step 5: commit**（`feat(frontend): Phase2a 搜尋列與最小詳情 modal`）

---

## Task 9: SearchPage 組合、route 掛載、bespoke CSS 萃取

**Files:**
- Create: `frontend/src/features/search/SearchPage.tsx`, `frontend/src/features/search/SearchPage.module.css`
- Modify: `frontend/src/App.tsx`（加 `/app/search` route）
- Test: `.../SearchPage.test.tsx`
- Reference: `index.html` inline `<style>`（萃取結果區/側欄/卡片/分組/索引/drill 的 CSS）

**Interfaces:**
- Consumes: 全部前述 hooks/元件
- Produces: `<SearchPage />`（route 元件）；`App.tsx` route `{ path: '/search', element: <Layout><SearchPage /></Layout> }`

- [ ] **Step 1: 寫失敗測試（SearchPage）** — mock api stats+reports，渲染後顯示結果、URL 同步、載入更多呼叫 fetchNextPage。
- [ ] **Step 2: 實作 SearchPage**（流程：`useQuery(['stats'])` → 由 stats 算 Allowlists → `useSearchParamsState(allow)` → `useSearchResults(filters)` → 組 SearchBar/FilterSidebar/ResultsMeta/ResultsView/LoadMore/states；`groupViewMode('month'|'market' 預設 month, filters.market)` 決定 view；ResultCard onOpen → ReportDetailModal）
- [ ] **Step 3: 改 `App.tsx`** 加 import 與 route：

```tsx
import SearchPage from './features/search/SearchPage'
// routes 陣列加：
{ path: '/search', element: <Layout><SearchPage /></Layout> },
```

- [ ] **Step 4: 萃取 bespoke CSS** → `SearchPage.module.css`，逐區對照 `index.html` 規則套上元件 className，跑本機 `npm run dev` 目視對齊（或留 Task 10 Playwright 對照）。
- [ ] **Step 5: 跑全部單元測試 + build** — `cd frontend && npx vitest run src && npm run build` → 全綠
- [ ] **Step 6: commit**（`feat(frontend): Phase2a SearchPage 組合與 /app/search 路由`）

---

## Task 10: Playwright 平價 e2e 與最終驗證

**Files:**
- Create: `frontend/e2e/search.spec.mjs`
- Reference: `frontend/e2e/monitor.spec.mjs`（登入 + 平價劇本骨架）

- [ ] **Step 1: 寫 e2e 劇本** — 登入 → `/app/search` → 預設分組渲染 → 點市場 chip → drill → 搜尋關鍵字 → 結果更新 → 載入更多 → 0 console error；URL 帶 q/market 可分享（reload 還原）。對照舊 `/` 視覺/行為。跑 `:8098`（不擾 `:8097`）。
- [ ] **Step 2: 本機起分支 server + build SPA**，跑 Playwright（`MONITOR_BASE_URL`/對應 env 指 `:8098`）→ 0 error、平價。
- [ ] **Step 3: 最終驗證** — `npx vitest run src` 全綠、`npm run build` 綠、eslint 0（`node ./node_modules/eslint/bin/eslint.js .`）。
- [ ] **Step 4: commit**（`test(frontend): Phase2a 檢索頁平價 e2e`）+ 更新 `docs/REFACTOR_TODO.md` Phase 2 進度註記。

---

## Self-Review（plan vs spec）

- **Spec coverage**：搜尋(T2,5,8)/分組預設+drill(T4,7,9)/分頁(T5,7)/URL 狀態(T3,5)/篩選側欄(T6)/Zod(T1)/端點契約(T1,2)/平價 e2e(T10)/錯誤·401(T2,7)/XSS 純文字(T7,8) — 皆有對應任務。
- **Placeholder scan**：logic 任務(T1-5)含完整碼；元件任務(T6-9)給介面+關鍵測試+視覺萃取參照（JSX 細節由 subagent 依 spec/render.js 對齊，屬實作而非 placeholder）。
- **Type consistency**：`Filters`/`Row`/`GroupView`/`Allowlists` 於 T3/T4 定義，T5-9 一致引用；`buildQuery` 用後端參數名、URL 層用分享參數名（T2/T3 已明標分界）。
- **已知取捨**：grouping 不依賴 meta（只回 key，label 由元件算）；report 詳情用最小 modal，完整遷移留 Phase 4。
