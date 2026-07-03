# Phase 1：檢索頁 `/app/search` React 遷移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 vanilla 檢索頁遷移為 React SPA 的 `/app/search`，逐像素對齊 `docs/design/廷豐智能研報.dc.html`，功能對 vanilla 平價，後端零改動、未 cutover。

**Architecture:** 純函式（`lib/`，TDD）先行 → 共用 `Modal` 原語與 `ReportDetailModal` → 檢索子元件（`features/search/`）→ `SearchPage` 整合（react-query 抓取 + 手動分頁累積 + URL 同步）→ e2e 冒煙。資料層 react-query：browse→`/api/reports`、search→`/api/search`；`view`/`tableSort` 為呈現態不進 query key。

**Tech Stack:** React 19.2 / TypeScript / react-router 8 / @tanstack/react-query 5 / Zod 4 / Vitest 4 / @testing-library/react / Playwright（Phase 0 已裝）。

**Spec:** `docs/superpowers/specs/2026-07-03-react-spa-phase1-search-design.md`（已過 3 輪 Codex review）。

## Global Constraints

- **後端零改動**：只消費 `/api/reports`、`/api/search`、`/api/report/{id}/full`、`/api/report/{id}/file`、`/api/stats`。不改 `web/`、`app/`。
- **未 cutover**：`/app/search` 與 vanilla 共存；不動 `index()`。
- **零元件庫**：無 Mantine／無圖表庫；用 Phase 0 `Popover`/`Menu`/`Icon`/hooks，本階段新增 `Modal` 原語。
- **樣式**：`tokens.css` 變數 + per-component CSS Modules；動態值（市場色、相關度條寬）走 inline style。**無裸 hex**（除非 token 無對應且註明）。
- **XSS 安全**：命中高亮回 React 節點，**不使用 `dangerouslySetInnerHTML`**；外部/下載連結 scheme 僅允許相對 `/` 或 `https?:`。
- **篩選皆單值**（對齊後端）：`market`/`instrument_type`/`report_type` 單選；`relates_stock`/`relates_futures` 布林。無多值/標的值篩選。
- **分頁**：初始 `limit=50`、每次 `+50`（`SEARCH_PAGE_SIZE=50`，對齊 vanilla）。
- **檢視**：卡片（月分組 sticky pill）＋表格（**flat，不分組**）。`view`/`tableSort` 切換**不重抓**；`sort` 改變**會重抓**。
- **`最新` 徽章前端計算**：鏡像 `app/services/answer.py`（已載入結果集中 `report_date` 嚴格最大者，同日保留第一筆，無日期不參與）。
- **市場色/順序**：用 `lib/meta.ts` `marketColor`/`marketLabel`/`ptypeColor`/`MARKET_ORDER`。
- **測試執行**：`cd frontend && ./node_modules/.bin/vitest run <path>`（**直接 binary**，npm-script 殼層遮 exit code）；`./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/eslint .`、`./node_modules/.bin/vite build`。完整套件 WSL 偶發 fork worker flake → 用焦點檔＋build 收斂。
- **提交**：Conventional Commits＋Traditional-Chinese scope；每則 commit 結尾加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`；共用工作樹**只 stage 明確路徑**（`git add <file>`，非 `-A`），`git diff --staged --stat` 驗範圍。

## Phase 0 既有介面（本計畫消費，勿改）

- `lib/api.ts`：`getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T>`、`class ApiError extends Error { status: number }`、`redirectToLogin(): void`。
- `lib/schemas.ts`：`statsSchema`（含 `markets[]`、`instrument_types[]`、`report_types[]`、`username`）、`conversationSummarySchema`。**本計畫在此檔追加 search 相關 schema**。
- `lib/meta.ts`：`marketColor(code): string`、`marketLabel(code): string`、`ptypeColor(name): string`、`MARKET_ORDER: readonly string[]`（`['TW','US','HK','CN','WTX','FX','MACRO','GLOBAL','CRYPTO']`，**不含 ALL**）。
- `lib/useStats.ts`：`useStats()` → react-query `useQuery(['stats'], …)`，回 `statsSchema` 型別（`markets/instrument_types/report_types` 各 `{market|type, count}`）。
- `components/primitives/`：`Popover`（`{open, onClose, children}`）、`Menu`/`MenuItem`（`role=menu/menuitem`）、`Icon`（`{name: IconName, size?}`）。
- `lib/`（hooks）：`useMediaQuery(query)`、`useClickOutside(ref, handler, active?)`、`useFocusTrap(ref, active)`。
- 路由：`App.tsx` `createBrowserRouter(routes, { basename: '/app' })`，`/search` → `features/search/SearchPage`（Phase 0 為 placeholder，本計畫 Task 16 換掉）。

## 檔案結構（本計畫建立/修改）

| 檔案 | 責任 |
|------|------|
| `frontend/src/lib/schemas.ts`（改） | 追加 `passageSchema`/`reportResultSchema`/`searchResponseSchema`/`reportListItemSchema`/`reportListResponseSchema`/`reportFullSchema` + 型別 |
| `frontend/src/lib/searchApi.ts` | `browseReports`/`searchReports`/`getReportFull`（包 `getJSON`） |
| `frontend/src/lib/searchFilters.ts` | `SearchState`/`SortValue`、`defaultState`/`parseParams`/`buildParams`/`toApiParams`/`modeOf`/`activeAdvancedCount`/`clearFilters` |
| `frontend/src/lib/grouping.ts` | `monthGroups(rows)`（月分組、無日期置底） |
| `frontend/src/lib/sortForMode.ts` | `sortOptions(mode)`/`normalizeSort(mode, sort)` |
| `frontend/src/lib/resultsMeta.ts` | `resultsMetaText(...)`、`emptyState(...)` 文案 |
| `frontend/src/lib/terms.ts` | `queryTerms(q)` 切詞 |
| `frontend/src/lib/highlight.tsx` | `highlight(text, terms): ReactNode[]`（`<mark>`，無 dangerouslySetInnerHTML） |
| `frontend/src/lib/isLatest.ts` | `latestId(rows): string | null` |
| `frontend/src/lib/tableSort.ts` | `TableSortKey`/`sortRows(rows, sort)` |
| `frontend/src/lib/useSearchResults.ts` | react-query hook：依 state 抓 browse/search + 手動分頁累積 |
| `frontend/src/components/primitives/Modal.tsx`(+css) | 通用 modal（fixed 遮罩＋Esc＋click-outside＋focus-trap） |
| `frontend/src/components/ReportDetailModal.tsx`(+css) | 詳情 modal（打 `/full`＋四分支） |
| `frontend/src/features/search/*` | `SearchPage`、`SearchBar`、`MarketChipBar`、`SortMenu`、`MoreFiltersPopover`、`ActiveChips`、`ResultsMeta`、`MonthGroup`(卡片)、`ResultCard`、`TableView`、`EmptyState`、`LoadMore`、`ViewSwitch`（各含 `.module.css`） |
| `frontend/e2e/search.spec.ts` | e2e 冒煙 |

## 共用型別（跨任務一致）

```ts
// searchFilters.ts
export type SortValue = 'relevance' | 'date_desc' | 'date_asc'
export type ViewMode = 'cards' | 'table'
export interface SearchState {
  q: string
  market: string            // 'ALL' | 市場代碼
  instrument_type: string   // '' = 全部
  report_type: string       // '' = 全部
  relates_stock: boolean
  relates_futures: boolean
  sort: SortValue
  view: ViewMode
}
export type SearchMode = 'search' | 'browse'
```

`ReportRow`（統一 browse item 與 search result 的共用欄位，卡片/表格/分組/isLatest 都吃它）：
```ts
// schemas.ts 匯出（reportResultSchema 為 reportListItemSchema 的超集）
export type ReportListItem = z.infer<typeof reportListItemSchema>
export type ReportResult = z.infer<typeof reportResultSchema>   // = ReportListItem + best_score/match_count/passages
export type ReportRow = ReportResult                            // browse 時 best_score/match_count/passages 為 undefined
```

---

### Task 1: Search schemas + API helpers

**Files:**
- Modify: `frontend/src/lib/schemas.ts`（在檔尾追加）
- Create: `frontend/src/lib/searchApi.ts`
- Test: `frontend/src/lib/schemas.test.ts`（新建）、`frontend/src/lib/searchApi.test.ts`（新建）

**Interfaces:**
- Consumes: `getJSON<T>(path, schema, init?)` from `./api`。
- Produces（後續任務全都吃這些型別）：
  - `passageSchema`、`reportListItemSchema`、`reportResultSchema`、`reportListResponseSchema`、`searchResponseSchema`、`reportFullSchema`
  - 型別 `Passage`、`ReportListItem`、`ReportResult`、`ReportListResponse`、`SearchResponse`、`ReportFull`、`ReportRow`
  - `browseReports(params: URLSearchParams): Promise<ReportListResponse>`、`searchReports(params: URLSearchParams): Promise<SearchResponse>`、`getReportFull(id: string): Promise<ReportFull>`

- [ ] **Step 1: Write the failing tests**

`frontend/src/lib/schemas.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { reportListItemSchema, reportResultSchema, searchResponseSchema, reportFullSchema } from './schemas'

const baseItem = {
  report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大', summary: null,
  report_date: '2026-06-25', report_type: null, instrument_types: ['股票'],
  relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null,
}

describe('schemas', () => {
  it('parses a browse list item', () => {
    expect(reportListItemSchema.parse(baseItem).report_id).toBe('r1')
  })
  it('parses a search result (superset with passages)', () => {
    const r = reportResultSchema.parse({
      ...baseItem, rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 2, content: '片段' }],
    })
    expect(r.best_score).toBe(0.83)
    expect(r.passages[0].content).toBe('片段')
  })
  it('parses a search response envelope', () => {
    const s = searchResponseSchema.parse({
      query: 'AI', market: null, total: 1,
      results: [{ ...baseItem, rank: 1, best_score: 0.5, match_count: 1,
        passages: [{ score: 0.5, chunk_index: 0, content: 'x' }] }],
    })
    expect(s.total).toBe(1)
  })
  it('parses a report full (has_file bool)', () => {
    const f = reportFullSchema.parse({
      report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大',
      summary: null, report_date: null, report_type: null, has_file: true,
    })
    expect(f.has_file).toBe(true)
  })
})
```

`frontend/src/lib/searchApi.test.ts`：
```ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import { browseReports, searchReports, getReportFull } from './searchApi'

function mockFetchOnce(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => body,
  })))
}
afterEach(() => vi.unstubAllGlobals())

describe('searchApi', () => {
  it('browseReports hits /api/reports with params and parses', async () => {
    mockFetchOnce({ total: 0, offset: 0, items: [] })
    const r = await browseReports(new URLSearchParams({ limit: '50', offset: '0' }))
    expect(r.total).toBe(0)
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain('/api/reports?')
    expect(url).toContain('limit=50')
  })
  it('searchReports hits /api/search and parses', async () => {
    mockFetchOnce({ query: 'AI', market: null, total: 0, results: [] })
    const r = await searchReports(new URLSearchParams({ q: 'AI' }))
    expect(r.query).toBe('AI')
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/search?')
  })
  it('getReportFull encodes id and parses has_file', async () => {
    mockFetchOnce({ report_id: 'a/b', file_name: 'x.pdf', market: null, source: null,
      summary: null, report_date: null, report_type: null, has_file: false })
    const f = await getReportFull('a/b')
    expect(f.has_file).toBe(false)
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/report/a%2Fb/full')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/schemas.test.ts src/lib/searchApi.test.ts`
Expected: FAIL（`reportResultSchema` / `browseReports` 未定義，或 `searchApi` 檔案不存在）。

- [ ] **Step 3: Append schemas to `schemas.ts`**

在 `frontend/src/lib/schemas.ts` 檔尾追加：
```ts
export const passageSchema = z.object({
  score: z.number(),
  chunk_index: z.number().int(),
  content: z.string(),
})
export type Passage = z.infer<typeof passageSchema>

export const reportListItemSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullable(),
  source: z.string().nullable(),
  summary: z.string().nullable(),
  report_date: z.string().nullable(),
  report_type: z.string().nullable(),
  instrument_types: z.array(z.string()).nullable(),
  relates_stock: z.boolean().nullable(),
  relates_futures: z.boolean().nullable(),
  stock_targets: z.array(z.string()).nullable(),
  futures_targets: z.array(z.string()).nullable(),
})
export type ReportListItem = z.infer<typeof reportListItemSchema>

export const reportResultSchema = reportListItemSchema.extend({
  rank: z.number().int(),
  best_score: z.number(),
  match_count: z.number().int(),
  passages: z.array(passageSchema),
})
export type ReportResult = z.infer<typeof reportResultSchema>

export const reportListResponseSchema = z.object({
  total: z.number().int(),
  offset: z.number().int(),
  items: z.array(reportListItemSchema),
})
export type ReportListResponse = z.infer<typeof reportListResponseSchema>

export const searchResponseSchema = z.object({
  query: z.string(),
  market: z.string().nullable(),
  total: z.number().int(),
  results: z.array(reportResultSchema),
})
export type SearchResponse = z.infer<typeof searchResponseSchema>

export const reportFullSchema = z.object({
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullable(),
  source: z.string().nullable(),
  summary: z.string().nullable(),
  report_date: z.string().nullable(),
  report_type: z.string().nullable(),
  has_file: z.boolean(),
})
export type ReportFull = z.infer<typeof reportFullSchema>

/** browse item 與 search result 的統一列型別：browse 時 rank/best_score/match_count/passages 為 undefined。 */
export type ReportRow = ReportListItem & {
  rank?: number
  best_score?: number
  match_count?: number
  passages?: Passage[]
}
```

- [ ] **Step 4: Create `searchApi.ts`**

```ts
import { getJSON } from './api'
import {
  reportListResponseSchema, searchResponseSchema, reportFullSchema,
  type ReportListResponse, type SearchResponse, type ReportFull,
} from './schemas'

/** browse 模式：無關鍵字列出報告。params 由 searchFilters.toApiParams 產出。 */
export function browseReports(params: URLSearchParams): Promise<ReportListResponse> {
  return getJSON(`/api/reports?${params.toString()}`, reportListResponseSchema, { cache: 'no-store' })
}

/** search 模式：語意檢索。params 需含 q。 */
export function searchReports(params: URLSearchParams): Promise<SearchResponse> {
  return getJSON(`/api/search?${params.toString()}`, searchResponseSchema, { cache: 'no-store' })
}

/** 詳情 modal：取單篇 metadata + has_file。 */
export function getReportFull(id: string): Promise<ReportFull> {
  return getJSON(`/api/report/${encodeURIComponent(id)}/full`, reportFullSchema, { cache: 'no-store' })
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/schemas.test.ts src/lib/searchApi.test.ts`
Expected: PASS（7 tests）。

- [ ] **Step 6: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（Expected: 0 errors）
```bash
git add frontend/src/lib/schemas.ts frontend/src/lib/searchApi.ts frontend/src/lib/schemas.test.ts frontend/src/lib/searchApi.test.ts
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 search/browse/full 的 Zod schema 與 API helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `searchFilters.ts`（狀態↔URL↔API 參數）

**Files:**
- Create: `frontend/src/lib/searchFilters.ts`
- Test: `frontend/src/lib/searchFilters.test.ts`

**Interfaces:**
- Consumes: 無（純函式）。
- Produces:
  - 型別 `SortValue`（`'relevance'|'date_desc'|'date_asc'`）、`ViewMode`（`'cards'|'table'`）、`SearchState`、`SearchMode`（`'search'|'browse'`）
  - `defaultState(): SearchState`
  - `modeOf(s: Pick<SearchState,'q'>): SearchMode`
  - `parseParams(search: string): SearchState`（吃 `location.search`）
  - `buildParams(s: SearchState): URLSearchParams`（→ URL，省略預設值）
  - `toApiParams(s: SearchState, page: { limit: number; offset: number }): URLSearchParams`（→ API）
  - `activeAdvancedCount(s: SearchState): number`（進階篩選：instrument_type/report_type/relates_stock/relates_futures）
  - `hasAnyFilter(s: SearchState): boolean`（含 market）
  - `clearFilters(s: SearchState): SearchState`（清 market+進階，留 q/sort/view）
  - `browseAll(s: SearchState): SearchState`（清 q+篩選，sort→date_desc）

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, it, expect } from 'vitest'
import {
  defaultState, modeOf, parseParams, buildParams, toApiParams,
  activeAdvancedCount, hasAnyFilter, clearFilters, browseAll,
} from './searchFilters'

describe('searchFilters', () => {
  it('modeOf: q 有值→search，空白→browse', () => {
    expect(modeOf({ q: 'AI' })).toBe('search')
    expect(modeOf({ q: '   ' })).toBe('browse')
  })
  it('parseParams reads url', () => {
    const s = parseParams('?q=AI&market=TW&instrument_type=股票&relates_stock=1&sort=date_asc&view=table')
    expect(s).toMatchObject({ q: 'AI', market: 'TW', instrument_type: '股票',
      relates_stock: true, sort: 'date_asc', view: 'table' })
  })
  it('parseParams: 無 sort 時 search 預設 relevance、browse 預設 date_desc', () => {
    expect(parseParams('?q=AI').sort).toBe('relevance')
    expect(parseParams('').sort).toBe('date_desc')
  })
  it('buildParams omits defaults (ALL/空/false/預設 sort/cards)', () => {
    expect(buildParams(defaultState()).toString()).toBe('')
    const p = buildParams({ ...defaultState(), q: 'AI', market: 'TW', view: 'table', sort: 'relevance' as const })
    expect(p.get('q')).toBe('AI'); expect(p.get('market')).toBe('TW')
    expect(p.get('view')).toBe('table'); expect(p.has('sort')).toBe(false)
  })
  it('toApiParams: search 帶 q；relates 只在 true 時帶；market ALL 不帶；含 limit/offset', () => {
    const p = toApiParams({ ...defaultState(), q: 'AI', sort: 'relevance', relates_stock: true },
      { limit: 50, offset: 0 })
    expect(p.get('q')).toBe('AI'); expect(p.get('relates_stock')).toBe('true')
    expect(p.has('market')).toBe(false); expect(p.get('limit')).toBe('50')
  })
  it('toApiParams: browse 不帶 q', () => {
    expect(toApiParams(defaultState(), { limit: 50, offset: 0 }).has('q')).toBe(false)
  })
  it('activeAdvancedCount + hasAnyFilter', () => {
    const s = { ...defaultState(), report_type: '個股報告', relates_futures: true }
    expect(activeAdvancedCount(s)).toBe(2)
    expect(hasAnyFilter(s)).toBe(true)
    expect(hasAnyFilter({ ...defaultState(), market: 'US' })).toBe(true)
    expect(hasAnyFilter(defaultState())).toBe(false)
  })
  it('clearFilters keeps q, browseAll clears q', () => {
    const s = { ...defaultState(), q: 'AI', market: 'TW', relates_stock: true, sort: 'relevance' as const }
    expect(clearFilters(s)).toMatchObject({ q: 'AI', market: 'ALL', relates_stock: false })
    expect(browseAll(s)).toMatchObject({ q: '', market: 'ALL', sort: 'date_desc' })
  })
})
```

- [ ] **Step 2: Run to verify FAIL**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/searchFilters.test.ts`
Expected: FAIL（module not found）。

- [ ] **Step 3: Implement `searchFilters.ts`**

```ts
export type SortValue = 'relevance' | 'date_desc' | 'date_asc'
export type ViewMode = 'cards' | 'table'
export type SearchMode = 'search' | 'browse'

export interface SearchState {
  q: string
  market: string            // 'ALL' | 市場代碼
  instrument_type: string   // '' = 全部
  report_type: string       // '' = 全部
  relates_stock: boolean
  relates_futures: boolean
  sort: SortValue
  view: ViewMode
}

export function defaultState(): SearchState {
  return {
    q: '', market: 'ALL', instrument_type: '', report_type: '',
    relates_stock: false, relates_futures: false, sort: 'date_desc', view: 'cards',
  }
}

export function modeOf(s: Pick<SearchState, 'q'>): SearchMode {
  return s.q.trim() ? 'search' : 'browse'
}

function isSort(v: string | null): v is SortValue {
  return v === 'relevance' || v === 'date_desc' || v === 'date_asc'
}

export function parseParams(search: string): SearchState {
  const p = new URLSearchParams(search)
  const q = p.get('q') ?? ''
  const sortRaw = p.get('sort')
  const defSort: SortValue = q.trim() ? 'relevance' : 'date_desc'
  return {
    q,
    market: p.get('market') || 'ALL',
    instrument_type: p.get('instrument_type') ?? '',
    report_type: p.get('report_type') ?? '',
    relates_stock: p.get('relates_stock') === '1',
    relates_futures: p.get('relates_futures') === '1',
    sort: isSort(sortRaw) ? sortRaw : defSort,
    view: p.get('view') === 'table' ? 'table' : 'cards',
  }
}

export function buildParams(s: SearchState): URLSearchParams {
  const p = new URLSearchParams()
  if (s.q.trim()) p.set('q', s.q.trim())
  if (s.market !== 'ALL') p.set('market', s.market)
  if (s.instrument_type) p.set('instrument_type', s.instrument_type)
  if (s.report_type) p.set('report_type', s.report_type)
  if (s.relates_stock) p.set('relates_stock', '1')
  if (s.relates_futures) p.set('relates_futures', '1')
  const defSort: SortValue = s.q.trim() ? 'relevance' : 'date_desc'
  if (s.sort !== defSort) p.set('sort', s.sort)
  if (s.view !== 'cards') p.set('view', s.view)
  return p
}

export function toApiParams(s: SearchState, page: { limit: number; offset: number }): URLSearchParams {
  const p = new URLSearchParams()
  if (modeOf(s) === 'search') p.set('q', s.q.trim())
  if (s.market !== 'ALL') p.set('market', s.market)
  if (s.instrument_type) p.set('instrument_type', s.instrument_type)
  if (s.report_type) p.set('report_type', s.report_type)
  if (s.relates_stock) p.set('relates_stock', 'true')
  if (s.relates_futures) p.set('relates_futures', 'true')
  p.set('sort', s.sort)
  p.set('limit', String(page.limit))
  p.set('offset', String(page.offset))
  return p
}

export function activeAdvancedCount(s: SearchState): number {
  let n = 0
  if (s.instrument_type) n++
  if (s.report_type) n++
  if (s.relates_stock) n++
  if (s.relates_futures) n++
  return n
}

export function hasAnyFilter(s: SearchState): boolean {
  return s.market !== 'ALL' || activeAdvancedCount(s) > 0
}

export function clearFilters(s: SearchState): SearchState {
  return {
    ...s, market: 'ALL', instrument_type: '', report_type: '',
    relates_stock: false, relates_futures: false,
  }
}

export function browseAll(s: SearchState): SearchState {
  return { ...clearFilters(s), q: '', sort: 'date_desc' }
}
```

- [ ] **Step 4: Run to verify PASS**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/searchFilters.test.ts`
Expected: PASS（8 tests）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/searchFilters.ts frontend/src/lib/searchFilters.test.ts
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 searchFilters 狀態↔URL↔API 參數純函式

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `sortForMode.ts` + `resultsMeta.ts`

**Files:**
- Create: `frontend/src/lib/sortForMode.ts`、`frontend/src/lib/resultsMeta.ts`
- Test: `frontend/src/lib/sortForMode.test.ts`、`frontend/src/lib/resultsMeta.test.ts`

**Interfaces:**
- Consumes: `SearchMode`/`SortValue`/`SearchState`/`hasAnyFilter` from `./searchFilters`；`marketLabel` from `./meta`。
- Produces:
  - `SortOption { value: SortValue; label: string }`、`sortOptions(mode): SortOption[]`、`normalizeSort(mode, sort): SortValue`
  - `resultsMetaText(s: SearchState, total: number): string`
  - `EmptyStateCopy { title; hint; showClear; showBrowseAll }`、`emptyState(s: SearchState, hasFilter: boolean): EmptyStateCopy`

- [ ] **Step 1: Write the failing tests**

`sortForMode.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { sortOptions, normalizeSort } from './sortForMode'

describe('sortForMode', () => {
  it('search 有 3 選項含 relevance；browse 只有 2（無 relevance）', () => {
    expect(sortOptions('search').map(o => o.value)).toEqual(['relevance', 'date_desc', 'date_asc'])
    expect(sortOptions('browse').map(o => o.value)).toEqual(['date_desc', 'date_asc'])
  })
  it('normalizeSort: browse 下 relevance 非法→回退 date_desc', () => {
    expect(normalizeSort('browse', 'relevance')).toBe('date_desc')
  })
  it('normalizeSort: 合法值原樣', () => {
    expect(normalizeSort('search', 'relevance')).toBe('relevance')
    expect(normalizeSort('browse', 'date_asc')).toBe('date_asc')
  })
})
```

`resultsMeta.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { resultsMetaText, emptyState } from './resultsMeta'
import { defaultState } from './searchFilters'

describe('resultsMetaText', () => {
  it('search + 市場', () => {
    expect(resultsMetaText({ ...defaultState(), q: 'AI', market: 'TW' }, 12))
      .toBe('「AI」 · 台股 — 找到 12 篇研報')
  })
  it('search 無市場（不帶市場片段）', () => {
    expect(resultsMetaText({ ...defaultState(), q: 'AI' }, 3)).toBe('「AI」 — 找到 3 篇研報')
  })
  it('browse 有市場篩選', () => {
    expect(resultsMetaText({ ...defaultState(), market: 'US' }, 8)).toBe('美股 — 8 篇')
  })
  it('browse 有進階篩選但無市場→全部研報', () => {
    expect(resultsMetaText({ ...defaultState(), report_type: '個股' }, 5)).toBe('全部研報 — 5 篇')
  })
  it('browse 皆無篩選', () => {
    expect(resultsMetaText(defaultState(), 100)).toBe('全部研報 — 共 100 篇')
  })
})

describe('emptyState', () => {
  it('search：標題含 q、showBrowseAll、showClear 依 hasFilter', () => {
    const c = emptyState({ ...defaultState(), q: 'xyz' }, true)
    expect(c.title).toBe('找不到「xyz」的相關研報')
    expect(c.showBrowseAll).toBe(true); expect(c.showClear).toBe(true)
  })
  it('browse：無 browseAll、showClear 依 hasFilter', () => {
    const c = emptyState(defaultState(), false)
    expect(c.title).toBe('沒有符合條件的研報')
    expect(c.showBrowseAll).toBe(false); expect(c.showClear).toBe(false)
  })
})
```

- [ ] **Step 2: Run to verify FAIL**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/sortForMode.test.ts src/lib/resultsMeta.test.ts`
Expected: FAIL（module not found）。

- [ ] **Step 3: Implement `sortForMode.ts`**

```ts
import type { SearchMode, SortValue } from './searchFilters'

export interface SortOption { value: SortValue; label: string }

const SEARCH_OPTS: SortOption[] = [
  { value: 'relevance', label: '相關度' },
  { value: 'date_desc', label: '日期（新→舊）' },
  { value: 'date_asc', label: '日期（舊→新）' },
]
const BROWSE_OPTS: SortOption[] = [
  { value: 'date_desc', label: '日期（新→舊）' },
  { value: 'date_asc', label: '日期（舊→新）' },
]

export function sortOptions(mode: SearchMode): SortOption[] {
  return mode === 'search' ? SEARCH_OPTS : BROWSE_OPTS
}

/** 模式切換後若當前 sort 在新模式不合法（如 browse 下 relevance），回退該模式預設。 */
export function normalizeSort(mode: SearchMode, sort: SortValue): SortValue {
  if (sortOptions(mode).some(o => o.value === sort)) return sort
  return mode === 'search' ? 'relevance' : 'date_desc'
}
```

- [ ] **Step 4: Implement `resultsMeta.ts`**

```ts
import { marketLabel } from './meta'
import { hasAnyFilter, type SearchState } from './searchFilters'

export function resultsMetaText(s: SearchState, total: number): string {
  const mkt = s.market !== 'ALL' ? marketLabel(s.market) : null
  if (s.q.trim()) {
    const mktPart = mkt ? ` · ${mkt}` : ''
    return `「${s.q.trim()}」${mktPart} — 找到 ${total} 篇研報`
  }
  if (hasAnyFilter(s)) return `${mkt ?? '全部研報'} — ${total} 篇`
  return `全部研報 — 共 ${total} 篇`
}

export interface EmptyStateCopy {
  title: string
  hint: string
  showClear: boolean
  showBrowseAll: boolean
}

export function emptyState(s: SearchState, hasFilter: boolean): EmptyStateCopy {
  const isSearch = !!s.q.trim()
  return {
    title: isSearch ? `找不到「${s.q.trim()}」的相關研報` : '沒有符合條件的研報',
    hint: '換個說法或關鍵字試試，或清除目前的篩選條件重新檢索。',
    showClear: hasFilter,
    showBrowseAll: isSearch,
  }
}
```

- [ ] **Step 5: Run to verify PASS**

Run: `cd frontend && ./node_modules/.bin/vitest run src/lib/sortForMode.test.ts src/lib/resultsMeta.test.ts`
Expected: PASS（8 tests）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/sortForMode.ts frontend/src/lib/sortForMode.test.ts frontend/src/lib/resultsMeta.ts frontend/src/lib/resultsMeta.test.ts
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 sortForMode（模式合法排序+回退）與 resultsMeta 文案

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `grouping.ts`（月分組）

**Files:**
- Create: `frontend/src/lib/grouping.ts`
- Test: `frontend/src/lib/grouping.test.ts`

**Interfaces:**
- Consumes: `ReportRow` from `./schemas`。
- Produces: `MonthGroup { key: string; title: string; count: number; items: ReportRow[] }`、`monthGroups(rows: ReportRow[]): MonthGroup[]`。

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect } from 'vitest'
import { monthGroups } from './grouping'
import type { ReportRow } from './schemas'

function row(id: string, date: string | null): ReportRow {
  return {
    report_id: id, file_name: id + '.pdf', market: 'TW', source: null, summary: null,
    report_date: date, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
  }
}

describe('monthGroups', () => {
  it('依 YYYY-MM 分組、標頭 YYYY 年 M 月、count、組內順序保留', () => {
    const g = monthGroups([row('a', '2026-06-25'), row('b', '2026-06-02'), row('c', '2026-05-30')])
    expect(g.map(x => x.title)).toEqual(['2026 年 6 月', '2026 年 5 月'])
    expect(g[0].count).toBe(2)
    expect(g[0].items.map(i => i.report_id)).toEqual(['a', 'b'])
  })
  it('無/不合法日期→「未標日期」組置底', () => {
    const g = monthGroups([row('a', null), row('b', '2026-06-01'), row('c', 'bad')])
    expect(g.map(x => x.title)).toEqual(['2026 年 6 月', '未標日期'])
    expect(g[1].items.map(i => i.report_id)).toEqual(['a', 'c'])
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/lib/grouping.test.ts`（module not found）

- [ ] **Step 3: Implement `grouping.ts`**

```ts
import type { ReportRow } from './schemas'

export interface MonthGroup {
  key: string
  title: string
  count: number
  items: ReportRow[]
}

const UNDATED = '__undated__'

function monthKey(d: string | null | undefined): string {
  return d && /^\d{4}-\d{2}/.test(d) ? d.slice(0, 7) : UNDATED
}

/** 依 report_date 前 7 碼（YYYY-MM）分組；組序沿輸入順序（已由 sort 決定），未標日期置底。 */
export function monthGroups(rows: ReportRow[]): MonthGroup[] {
  const order: string[] = []
  const map = new Map<string, ReportRow[]>()
  for (const r of rows) {
    const k = monthKey(r.report_date)
    if (!map.has(k)) { map.set(k, []); order.push(k) }
    map.get(k)!.push(r)
  }
  const keys = order.filter(k => k !== UNDATED)
  if (map.has(UNDATED)) keys.push(UNDATED)
  return keys.map(k => {
    const items = map.get(k)!
    return {
      key: k,
      title: k === UNDATED ? '未標日期' : `${k.slice(0, 4)} 年 ${Number(k.slice(5, 7))} 月`,
      count: items.length,
      items,
    }
  })
}
```

- [ ] **Step 4: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/lib/grouping.test.ts`（2 tests）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/grouping.ts frontend/src/lib/grouping.test.ts
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 monthGroups 月分組純函式（未標日期置底）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `terms.ts` + `highlight.tsx`（命中高亮，XSS 安全）

**Files:**
- Create: `frontend/src/lib/terms.ts`、`frontend/src/lib/highlight.tsx`
- Test: `frontend/src/lib/terms.test.ts`、`frontend/src/lib/highlight.test.tsx`

**Interfaces:**
- Consumes: 無。
- Produces: `queryTerms(q: string): string[]`（鏡像 vanilla `buildTerms`）、`highlight(text: string, terms: string[]): ReactNode[]`（回 React 節點，**不使用 `dangerouslySetInnerHTML`**）。

- [ ] **Step 1: Write the failing tests**

`terms.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { queryTerms } from './terms'

describe('queryTerms', () => {
  it('CJK：>1 字切 2-gram、單字保留', () => {
    expect(queryTerms('台積電')).toEqual(['台積', '積電'])
    expect(queryTerms('台')).toEqual(['台'])
  })
  it('非 CJK：需 ≥2 字，單字元丟棄', () => {
    expect(queryTerms('AI')).toEqual(['AI'])
    expect(queryTerms('a')).toEqual([])
  })
  it('多 token 去重、依長度降序', () => {
    expect(queryTerms('台積電 AI')).toEqual(['台積', '積電', 'AI'])
  })
})
```

`highlight.test.tsx`：
```tsx
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { highlight } from './highlight'

describe('highlight', () => {
  it('命中詞包 <mark>、文字內容不變', () => {
    const { container } = render(<div>{highlight('台積電營收成長', ['台積', '積電'])}</div>)
    expect(container.querySelectorAll('mark').length).toBeGreaterThan(0)
    expect(container.textContent).toBe('台積電營收成長')
  })
  it('無 terms → 純文字、無 mark', () => {
    const { container } = render(<div>{highlight('純文字', [])}</div>)
    expect(container.querySelectorAll('mark').length).toBe(0)
    expect(container.textContent).toBe('純文字')
  })
  it('不解讀 HTML 特殊字元（XSS 安全）', () => {
    const { container } = render(<div>{highlight('<img src=x onerror=1>', ['img'])}</div>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('<img')
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/lib/terms.test.ts src/lib/highlight.test.tsx`

- [ ] **Step 3: Implement `terms.ts`**

```ts
/** 鏡像 vanilla render.js buildTerms：CJK 切 2-gram、非 CJK ≥2 字，依長度降序（長詞優先匹配）。 */
export function queryTerms(q: string): string[] {
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

- [ ] **Step 4: Implement `highlight.tsx`**

```tsx
import { Fragment } from 'react'
import type { ReactNode } from 'react'

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 把 text 中命中 terms 的片段包成 <mark>，其餘為純文字節點。
 * 用單一 capture group split：偶數索引為原文、奇數索引為命中片段。
 * 回傳 React 節點陣列——絕不注入 HTML，天生 XSS 安全。
 */
export function highlight(text: string, terms: string[]): ReactNode[] {
  if (!terms.length || !text) return [text]
  const re = new RegExp('(' + terms.map(escapeRegExp).join('|') + ')', 'gi')
  return text.split(re).map((part, i) =>
    i % 2 === 1
      ? <mark key={i}>{part}</mark>
      : <Fragment key={i}>{part}</Fragment>,
  )
}
```

- [ ] **Step 4b: Add global `<mark>` style**

`highlight` 渲染裸 `<mark>` 元素，靠全域規則上色（`--tf-mark` 已存在於 tokens.css）。在 `frontend/src/styles/tokens.css` 檔尾追加：
```css
mark { background: var(--tf-mark); color: inherit; border-radius: 3px; padding: 0 1px; }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/lib/terms.test.ts src/lib/highlight.test.tsx`（6 tests）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/terms.ts frontend/src/lib/terms.test.ts frontend/src/lib/highlight.tsx frontend/src/lib/highlight.test.tsx frontend/src/styles/tokens.css
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 queryTerms 切詞與 XSS 安全的 highlight（React 節點）+ 全域 mark 樣式

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `isLatest.ts` + `tableSort.ts`

**Files:**
- Create: `frontend/src/lib/isLatest.ts`、`frontend/src/lib/tableSort.ts`
- Test: `frontend/src/lib/isLatest.test.ts`、`frontend/src/lib/tableSort.test.ts`

**Interfaces:**
- Consumes: `ReportRow` from `./schemas`；`marketLabel` from `./meta`。
- Produces:
  - `latestId(rows: Pick<ReportRow,'report_id'|'report_date'>[]): string | null`
  - `TableSortKey`（`'name'|'market'|'type'|'date'|'source'|'score'|'match'`）、`TableSort { key: TableSortKey; dir: 'asc'|'desc' }`、`sortRows(rows: ReportRow[], sort: TableSort): ReportRow[]`

- [ ] **Step 1: Write the failing tests**

`isLatest.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { latestId } from './isLatest'

const r = (report_id: string, report_date: string | null) => ({ report_id, report_date })

describe('latestId (mirror answer.py)', () => {
  it('嚴格最大者的 id', () => {
    expect(latestId([r('a', '2026-05-01'), r('b', '2026-06-25'), r('c', '2026-06-01')])).toBe('b')
  })
  it('同日保留最先出現者（嚴格大於）', () => {
    expect(latestId([r('a', '2026-06-25'), r('b', '2026-06-25')])).toBe('a')
  })
  it('無日期不參與；全無日期→null', () => {
    expect(latestId([r('a', null), r('b', '2026-06-01')])).toBe('b')
    expect(latestId([r('a', null), r('b', null)])).toBeNull()
    expect(latestId([])).toBeNull()
  })
})
```

`tableSort.test.ts`：
```ts
import { describe, it, expect } from 'vitest'
import { sortRows, type TableSort } from './tableSort'
import type { ReportRow } from './schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'x', file_name: '', market: null, source: null, summary: null,
    report_date: null, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    ...p,
  }
}
const s = (key: TableSort['key'], dir: TableSort['dir']): TableSort => ({ key, dir })

describe('sortRows', () => {
  it('name 升冪/降冪', () => {
    const rows = [row({ file_name: 'B' }), row({ file_name: 'A' })]
    expect(sortRows(rows, s('name', 'asc')).map(r => r.file_name)).toEqual(['A', 'B'])
    expect(sortRows(rows, s('name', 'desc')).map(r => r.file_name)).toEqual(['B', 'A'])
  })
  it('score 數值排序（降冪）', () => {
    const rows = [row({ best_score: 0.2 }), row({ best_score: 0.9 })]
    expect(sortRows(rows, s('score', 'desc')).map(r => r.best_score)).toEqual([0.9, 0.2])
  })
  it('date 字串排序', () => {
    const rows = [row({ report_date: '2026-01-01' }), row({ report_date: '2026-06-01' })]
    expect(sortRows(rows, s('date', 'desc')).map(r => r.report_date)).toEqual(['2026-06-01', '2026-01-01'])
  })
  it('不變更輸入陣列', () => {
    const rows = [row({ file_name: 'B' }), row({ file_name: 'A' })]
    sortRows(rows, s('name', 'asc'))
    expect(rows.map(r => r.file_name)).toEqual(['B', 'A'])
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/lib/isLatest.test.ts src/lib/tableSort.test.ts`

- [ ] **Step 3: Implement `isLatest.ts`**

```ts
import type { ReportRow } from './schemas'

function normDate(d: string | null | undefined): string | null {
  return d && /^\d{4}-\d{2}-\d{2}/.test(d) ? d.slice(0, 10) : null
}

/**
 * 鏡像 app/services/answer.py：已載入結果集中 report_date 嚴格最大者的 id。
 * 嚴格大於（`>`）→ 同日保留最先出現者；無日期者一律不參與；全無→null。
 */
export function latestId(rows: Pick<ReportRow, 'report_id' | 'report_date'>[]): string | null {
  let bestId: string | null = null
  let bestDate: string | null = null
  for (const r of rows) {
    const d = normDate(r.report_date)
    if (d !== null && (bestDate === null || d > bestDate)) {
      bestDate = d
      bestId = r.report_id
    }
  }
  return bestId
}
```

- [ ] **Step 4: Implement `tableSort.ts`**

```ts
import { marketLabel } from './meta'
import type { ReportRow } from './schemas'

export type TableSortKey = 'name' | 'market' | 'type' | 'date' | 'source' | 'score' | 'match'
export interface TableSort { key: TableSortKey; dir: 'asc' | 'desc' }

function valueOf(r: ReportRow, key: TableSortKey): string | number {
  switch (key) {
    case 'name': return r.file_name ?? ''
    case 'market': return marketLabel(r.market ?? '')
    case 'type': return r.report_type ?? ''
    case 'date': return r.report_date ?? ''
    case 'source': return r.source ?? ''
    case 'score': return r.best_score ?? 0
    case 'match': return r.match_count ?? 0
  }
}

/** 依欄鍵排序（不變更輸入）。數值欄（score/match）用數值比較，其餘用中文 locale 字串比較。 */
export function sortRows(rows: ReportRow[], sort: TableSort): ReportRow[] {
  const out = [...rows]
  out.sort((a, b) => {
    const va = valueOf(a, sort.key)
    const vb = valueOf(b, sort.key)
    const c = typeof va === 'number' && typeof vb === 'number'
      ? va - vb
      : String(va).localeCompare(String(vb), 'zh-Hant')
    return sort.dir === 'asc' ? c : -c
  })
  return out
}
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/lib/isLatest.test.ts src/lib/tableSort.test.ts`（7 tests）

- [ ] **Step 6: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（0 errors）
```bash
git add frontend/src/lib/isLatest.ts frontend/src/lib/isLatest.test.ts frontend/src/lib/tableSort.ts frontend/src/lib/tableSort.test.ts
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 latestId（鏡像 answer.py）與 tableSort 表格排序

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `useSearchResults.ts`（react-query 抓取 + 分頁累積）

**Files:**
- Create: `frontend/src/lib/useSearchResults.ts`
- Test: `frontend/src/lib/useSearchResults.test.tsx`

**Interfaces:**
- Consumes: `browseReports`/`searchReports` from `./searchApi`；`modeOf`/`toApiParams`/`SearchState` from `./searchFilters`；`ReportRow` from `./schemas`；`useInfiniteQuery` from `@tanstack/react-query`。
- Produces:
  - `SEARCH_PAGE_SIZE = 50`
  - `UseSearchResults { rows: ReportRow[]; total: number; isLoading: boolean; isError: boolean; refetch(): void; hasMore: boolean; remaining: number; isFetchingMore: boolean; loadMore(): void }`
  - `useSearchResults(s: SearchState): UseSearchResults`
- **關鍵**：query key **只含會影響抓取的欄位**（`q/market/instrument_type/report_type/relates_stock/relates_futures/sort`），**不含 `view`/`tableSort`**——故切檢視/表格排序不重抓；改 `sort` 或篩選會重抓且分頁自動重置。分頁以 `offset = 已載入列數` 累積（對齊 vanilla `state.offset = state.rows.length`）。

- [ ] **Step 1: Write the failing tests**

`useSearchResults.test.tsx`：
```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useSearchResults } from './useSearchResults'
import { defaultState } from './searchFilters'
import * as api from './searchApi'

vi.mock('./searchApi')

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function listResp(ids: string[], total: number, offset = 0) {
  return {
    total, offset,
    items: ids.map(id => ({
      report_id: id, file_name: id, market: null, source: null, summary: null,
      report_date: null, report_type: null, instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    })),
  }
}

beforeEach(() => vi.resetAllMocks())

describe('useSearchResults', () => {
  it('browse 載入第一頁（rows + total）', async () => {
    vi.mocked(api.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    const { result } = renderHook(() => useSearchResults(defaultState()), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.rows.map(r => r.report_id)).toEqual(['a', 'b'])
    expect(result.current.total).toBe(2)
    expect(result.current.hasMore).toBe(false)
  })

  it('loadMore 以 offset=已載入數 抓下一頁並 append', async () => {
    vi.mocked(api.browseReports)
      .mockResolvedValueOnce(listResp(['a'], 2, 0))
      .mockResolvedValueOnce(listResp(['b'], 2, 1))
    const { result } = renderHook(() => useSearchResults(defaultState()), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.rows.length).toBe(1))
    expect(result.current.hasMore).toBe(true)
    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.rows.map(r => r.report_id)).toEqual(['a', 'b']))
    expect(vi.mocked(api.browseReports).mock.calls[1][0].get('offset')).toBe('1')
  })

  it('改 sort 會重抓；改 view 不會', async () => {
    vi.mocked(api.browseReports).mockResolvedValue(listResp(['a'], 1))
    const { result, rerender } = renderHook(({ s }) => useSearchResults(s), {
      wrapper: wrapper(), initialProps: { s: defaultState() },
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(api.browseReports).toHaveBeenCalledTimes(1)
    rerender({ s: { ...defaultState(), view: 'table' as const } })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(api.browseReports).toHaveBeenCalledTimes(1)   // 呈現態變更不重抓
    rerender({ s: { ...defaultState(), sort: 'date_asc' as const } })
    await waitFor(() => expect(api.browseReports).toHaveBeenCalledTimes(2))  // 排序變更重抓
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/lib/useSearchResults.test.tsx`

- [ ] **Step 3: Implement `useSearchResults.ts`**

```ts
import { useInfiniteQuery } from '@tanstack/react-query'
import { browseReports, searchReports } from './searchApi'
import { modeOf, toApiParams, type SearchState } from './searchFilters'
import type { ReportRow } from './schemas'

export const SEARCH_PAGE_SIZE = 50

interface Page { rows: ReportRow[]; total: number }

/** query key：只放會影響「抓取結果」的欄位；view/tableSort 為呈現態，刻意排除以免切檢視就重抓。 */
function resultsKey(s: SearchState) {
  return [
    'search-results', s.q.trim(), s.market, s.instrument_type, s.report_type,
    s.relates_stock, s.relates_futures, s.sort,
  ] as const
}

async function fetchPage(s: SearchState, offset: number): Promise<Page> {
  const params = toApiParams(s, { limit: SEARCH_PAGE_SIZE, offset })
  if (modeOf(s) === 'search') {
    const r = await searchReports(params)
    return { rows: r.results, total: r.total }
  }
  const r = await browseReports(params)
  return { rows: r.items, total: r.total }
}

export interface UseSearchResults {
  rows: ReportRow[]
  total: number
  isLoading: boolean
  isError: boolean
  refetch: () => void
  hasMore: boolean
  remaining: number
  isFetchingMore: boolean
  loadMore: () => void
}

export function useSearchResults(s: SearchState): UseSearchResults {
  const query = useInfiniteQuery({
    queryKey: resultsKey(s),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => fetchPage(s, pageParam),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.rows.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
  })
  const pages = query.data?.pages ?? []
  const rows = pages.flatMap(p => p.rows)
  const total = pages[0]?.total ?? 0
  return {
    rows,
    total,
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: () => { void query.refetch() },
    hasMore: query.hasNextPage,
    remaining: Math.max(0, total - rows.length),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => { void query.fetchNextPage() },
  }
}
```

- [ ] **Step 4: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/lib/useSearchResults.test.tsx`（3 tests）

- [ ] **Step 5: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（0 errors）
```bash
git add frontend/src/lib/useSearchResults.ts frontend/src/lib/useSearchResults.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 useSearchResults hook（分頁累積、view/sort 分離 query key）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `Modal` 原語

**Files:**
- Create: `frontend/src/components/primitives/Modal.tsx`、`frontend/src/components/primitives/Modal.module.css`
- Test: `frontend/src/components/primitives/Modal.test.tsx`

**Interfaces:**
- Consumes: `useFocusTrap` from `../../lib/useFocusTrap`。
- Produces: `Modal({ open, onClose, title?, children, className? })` — fixed 置中對話框，遮罩點擊/Esc 關閉、面板點擊不冒泡、focus-trap、`role="dialog"` `aria-modal`。

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Modal } from './Modal'

describe('Modal', () => {
  it('open=false 不渲染內容', () => {
    render(<Modal open={false} onClose={() => {}}>內容</Modal>)
    expect(screen.queryByText('內容')).toBeNull()
  })
  it('open=true 渲染 dialog + title + 內容', () => {
    render(<Modal open onClose={() => {}} title="標題">內容</Modal>)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('標題')).toBeTruthy()
    expect(screen.getByText('內容')).toBeTruthy()
  })
  it('Esc 觸發 onClose', () => {
    const onClose = vi.fn()
    render(<Modal open onClose={onClose}>x</Modal>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('點遮罩關閉、點面板不關閉', () => {
    const onClose = vi.fn()
    const { container } = render(<Modal open onClose={onClose} title="t">body</Modal>)
    fireEvent.click(screen.getByText('body'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(container.querySelector('[data-scrim]')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Modal.test.tsx`

- [ ] **Step 3: Implement `Modal.tsx`**

```tsx
import { useEffect, useRef, type ReactNode } from 'react'
import { useFocusTrap } from '../../lib/useFocusTrap'
import styles from './Modal.module.css'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  className?: string
}

export function Modal({ open, onClose, title, children, className }: ModalProps) {
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
    <div className={styles.scrim} data-scrim onClick={onClose}>
      <div
        ref={panelRef}
        className={`${styles.panel} ${className ?? ''}`}
        role="dialog"
        aria-modal="true"
        onClick={e => e.stopPropagation()}
      >
        {title != null && (
          <div className={styles.header}>
            <div className={styles.title}>{title}</div>
            <button type="button" className={styles.close} onClick={onClose} aria-label="關閉">×</button>
          </div>
        )}
        <div className={styles.body}>{children}</div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Implement `Modal.module.css`**

```css
/* 遮罩色為覆蓋層，tokens.css 無對應 token（陰影同色系 rgba(16,24,40,...)）。 */
.scrim {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
}
.panel {
  background: var(--tf-surface);
  border-radius: var(--tf-radius-card);
  box-shadow: var(--tf-shadow-modal);
  width: min(880px, 100%);
  max-height: 90vh;
  display: flex; flex-direction: column;
  overflow: hidden;
}
.header {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 18px; border-bottom: 1px solid var(--tf-border);
}
.title { flex: 1; min-width: 0; font-weight: 700; font-size: 15px; color: var(--tf-text-1); }
.close {
  border: none; background: none; cursor: pointer;
  font-size: 22px; line-height: 1; color: var(--tf-text-2); padding: 0 4px;
}
.body { padding: 16px 18px; overflow: auto; }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/components/primitives/Modal.test.tsx`（4 tests）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/primitives/Modal.tsx frontend/src/components/primitives/Modal.module.css frontend/src/components/primitives/Modal.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 Modal 原語（遮罩/Esc/focus-trap，零元件庫）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `ReportDetailModal`（打 `/full` + 四分支）

**Files:**
- Create: `frontend/src/components/ReportDetailModal.tsx`、`frontend/src/components/ReportDetailModal.module.css`
- Test: `frontend/src/components/ReportDetailModal.test.tsx`

**Interfaces:**
- Consumes: `Modal` from `./primitives/Modal`；`getReportFull` from `../lib/searchApi`；`useQuery` from `@tanstack/react-query`。
- Produces: `ReportDetailModal({ reportId: string | null, fileName?: string, onClose: () => void })` — `reportId!==null` 即開啟，打 `/api/report/{id}/full` 取 `has_file`，依 `has_file`/`isPdf` 分**四分支**（PDF iframe＋在新分頁開啟／非 PDF 下載提示／缺檔文案／`/full` 錯誤載入失敗）＋載入中。連結一律指向相對 `/api/report/{id}/file`（內部構造，天生 scheme 安全）。

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { ReportDetailModal } from './ReportDetailModal'
import * as api from '../lib/searchApi'

vi.mock('../lib/searchApi')

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrap = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return render(<ReportDetailModal reportId="r1" onClose={() => {}} />, { wrapper: wrap })
}
type Full = Awaited<ReturnType<typeof api.getReportFull>>
const full = (over: Partial<Full>): Full => ({
  report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大',
  summary: null, report_date: null, report_type: null, has_file: true, ...over,
})

beforeEach(() => vi.resetAllMocks())

describe('ReportDetailModal', () => {
  it('has_file + PDF → 內嵌 iframe（src 指向 /file）', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ file_name: 'a.pdf', has_file: true }))
    renderModal()
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull())
    expect(document.querySelector('iframe')?.getAttribute('src')).toContain('/api/report/r1/file')
  })
  it('has_file + 非 PDF → 下載提示 + 下載連結', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ file_name: 'a.docx', has_file: true }))
    renderModal()
    await waitFor(() => expect(screen.getByText(/無法內嵌預覽/)).toBeTruthy())
    expect(screen.getByText('下載原始檔').getAttribute('href')).toContain('/api/report/r1/file')
  })
  it('!has_file → 缺檔文案', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ has_file: false }))
    renderModal()
    await waitFor(() => expect(screen.getByText('找不到原始檔。')).toBeTruthy())
  })
  it('/full 錯誤 → 載入失敗 fallback', async () => {
    vi.mocked(api.getReportFull).mockRejectedValue(new Error('boom'))
    renderModal()
    await waitFor(() => expect(screen.getByText(/報告載入失敗/)).toBeTruthy())
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/components/ReportDetailModal.test.tsx`

- [ ] **Step 3: Implement `ReportDetailModal.tsx`**

```tsx
import { useQuery } from '@tanstack/react-query'
import { Modal } from './primitives/Modal'
import { getReportFull } from '../lib/searchApi'
import styles from './ReportDetailModal.module.css'

interface Props {
  reportId: string | null
  fileName?: string
  onClose: () => void
}

function isPdfName(name: string): boolean {
  return name.toLowerCase().endsWith('.pdf')
}
function fileHref(id: string): string {
  return `/api/report/${encodeURIComponent(id)}/file`
}

export function ReportDetailModal({ reportId, fileName, onClose }: Props) {
  const open = reportId !== null
  const query = useQuery({
    queryKey: ['report-full', reportId],
    queryFn: () => getReportFull(reportId as string),
    enabled: open,
  })
  const title = query.data?.file_name ?? fileName ?? '報告'

  function body() {
    if (!open) return null
    if (query.isLoading) return <div className={styles.state}>載入中…</div>
    if (query.isError || !query.data) return <div className={styles.state}>報告載入失敗，請稍後再試。</div>
    const d = query.data
    const href = fileHref(d.report_id)
    if (d.has_file && isPdfName(d.file_name)) {
      return (
        <div className={styles.pdfWrap}>
          <iframe className={styles.frame} src={href} title={d.file_name} />
          <a className={styles.link} href={href} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
        </div>
      )
    }
    if (d.has_file) {
      const ext = d.file_name.split('.').pop()?.toUpperCase() ?? '檔案'
      return (
        <div className={styles.state}>
          <p>{ext} 文件 無法內嵌預覽，請下載查看。</p>
          <a className={styles.download} href={href} download>下載原始檔</a>
        </div>
      )
    }
    return <div className={styles.state}>找不到原始檔。</div>
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      {body()}
    </Modal>
  )
}
```

- [ ] **Step 4: Implement `ReportDetailModal.module.css`**

```css
.state { padding: 24px 8px; text-align: center; color: var(--tf-text-2); font-size: 14px; }
.state p { margin: 0 0 14px; }
.pdfWrap { display: flex; flex-direction: column; gap: 10px; }
.frame {
  width: 100%; height: 72vh;
  border: 1px solid var(--tf-border); border-radius: var(--tf-radius-md);
  background: var(--tf-surface);
}
.link { align-self: flex-end; color: var(--tf-gold-text); font-size: 13px; font-weight: 600; text-decoration: none; }
.download {
  align-self: center; color: var(--tf-gold-text); font-size: 13px; font-weight: 600; text-decoration: none;
  padding: 8px 16px; border: 1px solid var(--tf-gold-strong); border-radius: var(--tf-radius-md);
}
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/components/ReportDetailModal.test.tsx`（4 tests）

- [ ] **Step 6: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（0 errors）
```bash
git add frontend/src/components/ReportDetailModal.tsx frontend/src/components/ReportDetailModal.module.css frontend/src/components/ReportDetailModal.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 ReportDetailModal（打 /full 取 has_file、PDF/下載/缺檔/失敗 四分支）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `ResultCard`（卡片，兩模式）

**Files:**
- Create: `frontend/src/features/search/ResultCard.tsx`、`frontend/src/features/search/ResultCard.module.css`
- Test: `frontend/src/features/search/ResultCard.test.tsx`

**Interfaces:**
- Consumes: `marketColor`/`marketLabel` from `../../lib/meta`；`highlight` from `../../lib/highlight`；`ReportRow` from `../../lib/schemas`；`SearchMode` from `../../lib/searchFilters`。
- Produces: `ResultCard({ row: ReportRow, mode: SearchMode, isLatest: boolean, terms: string[], onOpen: (id: string, fileName: string) => void })`。整卡可點/可鍵盤開啟；search 顯高亮首段片段+相關度條，browse 顯摘要（≤88 字）。視覺對齊 `.dc.html`（`docs/design/廷豐智能研報.dc.html:196-221`）。

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ResultCard } from './ResultCard'
import type { ReportRow } from '../../lib/schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_name: '研報.pdf', market: 'TW', source: '元大', summary: '摘要內容',
    report_date: '2026-06-25', report_type: '個股', instrument_types: ['股票'],
    relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}

describe('ResultCard', () => {
  it('browse：顯示摘要、無相關度條', () => {
    render(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} onOpen={() => {}} />)
    expect(screen.getByText('摘要內容')).toBeTruthy()
    expect(screen.queryByText(/相關度/)).toBeNull()
  })
  it('search：顯示高亮片段 + 相關度 + 最新徽章', () => {
    const r = row({ rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 0, content: '台積電營收成長' }] })
    render(<ResultCard row={r} mode="search" isLatest terms={['台積']} onOpen={() => {}} />)
    expect(screen.getByText(/相關度/)).toBeTruthy()
    expect(document.querySelector('mark')).not.toBeNull()
    expect(screen.getByText('最新')).toBeTruthy()
  })
  it('整卡可點 → onOpen(id, fileName)', () => {
    const onOpen = vi.fn()
    render(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: /研報\.pdf/ }))
    expect(onOpen).toHaveBeenCalledWith('r1', '研報.pdf')
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/ResultCard.test.tsx`

- [ ] **Step 3: Implement `ResultCard.tsx`**

```tsx
import { marketColor, marketLabel } from '../../lib/meta'
import { highlight } from '../../lib/highlight'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './ResultCard.module.css'

interface Props {
  row: ReportRow
  mode: SearchMode
  isLatest: boolean
  terms: string[]
  onOpen: (id: string, fileName: string) => void
}

function truncate(s: string | null, n: number): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

export function ResultCard({ row, mode, isLatest, terms, onOpen }: Props) {
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  const pills = [...(row.instrument_types ?? []), ...targets]
  const date = (row.report_date ?? '').slice(0, 10)
  const open = () => onOpen(row.report_id, row.file_name)
  const pct = mode === 'search'
    ? Math.max(4, Math.min(100, Math.round((row.best_score ?? 0) * 100)))
    : 0
  const snippet = row.passages?.[0]?.content ?? ''

  return (
    <div
      className={styles.card}
      role="button"
      tabIndex={0}
      aria-label={row.file_name}
      onClick={open}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }}
    >
      <div className={styles.top}>
        <span className={styles.badge} style={{ background: marketColor(row.market ?? '') }}>
          {marketLabel(row.market ?? '')}
        </span>
        {isLatest && <span className={styles.latest}>最新</span>}
      </div>
      <div className={styles.title}>{row.file_name}</div>
      {pills.length > 0 && (
        <div className={styles.tags}>
          {pills.map((t, i) => <span key={i} className={styles.tag}>{t}</span>)}
        </div>
      )}
      {mode === 'search' ? (
        <>
          {snippet && (
            <div className={styles.snippet}>{highlight(snippet.slice(0, 300), terms)}…</div>
          )}
          <div className={styles.scoreRow}>
            <div className={styles.scoreTrack}>
              <div className={styles.scoreFill} style={{ width: `${pct}%` }} />
            </div>
            <span className={styles.scoreNum}>相關度 {pct}</span>
          </div>
        </>
      ) : (
        row.summary && <div className={styles.summary}>{truncate(row.summary, 88)}</div>
      )}
      <div className={styles.footer}>
        <span>{[row.source, date].filter(Boolean).join(' · ')}</span>
        <span className={styles.cta}>查看全文 ›</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Implement `ResultCard.module.css`**（對齊 `.dc.html`）

```css
.card {
  background: var(--tf-surface); border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-card); box-shadow: var(--tf-shadow-xs);
  padding: 13px 15px; cursor: pointer; transition: border-color var(--tf-dur-1) var(--tf-ease-out);
}
.card:hover, .card:focus-visible { border-color: var(--tf-gold-text); outline: none; }
.top { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.badge { color: var(--tf-on-gold); border-radius: var(--tf-radius-pill); padding: 1px 8px; font-size: 12px; font-weight: 600; }
.latest { border: 1px solid var(--tf-success); color: var(--tf-success-text); border-radius: var(--tf-radius-pill); padding: 0 8px; font-size: 11px; font-weight: 600; }
.title { font-family: var(--tf-serif); font-weight: 700; font-size: 14.5px; line-height: 1.45; color: var(--tf-text-1); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 2px; }
.tag { background: var(--tf-border-weak); border-radius: var(--tf-radius-pill); padding: 2px 9px; font-size: 11px; color: var(--tf-text-2); }
.snippet { font-size: 12.5px; color: var(--tf-text-3); line-height: 1.55; margin-top: 6px; }
.scoreRow { display: flex; align-items: center; gap: 8px; margin-top: 9px; }
.scoreTrack { flex: 1; height: 6px; border-radius: var(--tf-radius-pill); background: var(--tf-border-weak); overflow: hidden; }
.scoreFill { height: 100%; border-radius: var(--tf-radius-pill); background: var(--tf-gold-text); }
.scoreNum { font-size: 11px; color: var(--tf-text-4); font-variant-numeric: tabular-nums; }
.summary { font-size: 13px; color: var(--tf-text-2); line-height: 1.55; margin-top: 2px; }
.footer { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; font-size: 12px; color: var(--tf-text-3); }
.cta { color: var(--tf-gold-text); font-weight: 600; }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/ResultCard.test.tsx`（3 tests）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/search/ResultCard.tsx frontend/src/features/search/ResultCard.module.css frontend/src/features/search/ResultCard.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 ResultCard 卡片（search 高亮片段+相關度條／browse 摘要，對齊 .dc.html）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `TableView`（平坦表格 + 表頭排序）

**Files:**
- Create: `frontend/src/features/search/TableView.tsx`、`frontend/src/features/search/TableView.module.css`
- Test: `frontend/src/features/search/TableView.test.tsx`

**Interfaces:**
- Consumes: `marketColor`/`marketLabel` from `../../lib/meta`；`sortRows`/`TableSort`/`TableSortKey` from `../../lib/tableSort`；`ReportRow` from `../../lib/schemas`；`SearchMode` from `../../lib/searchFilters`。
- Produces: `TableView({ rows, mode, sort, onSort, onOpen })`。**平坦表格（不分組，內部以 `sortRows` 排序）**。欄（browse）：報告名稱/市場/類型/日期/來源/標的；search 再加 相關度/命中。表頭可排序（`標的` 不可排）；列可點開。

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TableView } from './TableView'
import type { TableSort } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_name: 'A.pdf', market: 'TW', source: '元大', summary: null,
    report_date: '2026-06-25', report_type: '個股', instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}
const sort: TableSort = { key: 'date', dir: 'desc' }

describe('TableView', () => {
  it('browse 6 欄（含 標的、無 相關度/命中）', () => {
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} onOpen={() => {}} />)
    const heads = screen.getAllByRole('columnheader')
    expect(heads.length).toBe(6)
    expect(heads.some(h => h.textContent?.includes('標的'))).toBe(true)
    expect(heads.some(h => h.textContent?.includes('相關度'))).toBe(false)
  })
  it('search 8 欄（多 相關度/命中）', () => {
    render(<TableView rows={[row({ best_score: 0.8, match_count: 3 })]} mode="search" sort={sort} onSort={() => {}} onOpen={() => {}} />)
    expect(screen.getAllByRole('columnheader').length).toBe(8)
    expect(screen.getByText('80%')).toBeTruthy()
  })
  it('點可排序表頭 → onSort(key)；標的不可排', () => {
    const onSort = vi.fn()
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={onSort} onOpen={() => {}} />)
    fireEvent.click(screen.getByText(/報告名稱/))
    expect(onSort).toHaveBeenCalledWith('name')
    fireEvent.click(screen.getByText('標的'))
    expect(onSort).toHaveBeenCalledTimes(1)
  })
  it('點列 → onOpen(id, fileName)', () => {
    const onOpen = vi.fn()
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} onOpen={onOpen} />)
    fireEvent.click(screen.getByText('A.pdf'))
    expect(onOpen).toHaveBeenCalledWith('r1', 'A.pdf')
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/TableView.test.tsx`

- [ ] **Step 3: Implement `TableView.tsx`**

```tsx
import { marketColor, marketLabel } from '../../lib/meta'
import { sortRows, type TableSort, type TableSortKey } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './TableView.module.css'

interface Col { key: TableSortKey | null; label: string }
const BASE_COLS: Col[] = [
  { key: 'name', label: '報告名稱' },
  { key: 'market', label: '市場' },
  { key: 'type', label: '類型' },
  { key: 'date', label: '日期' },
  { key: 'source', label: '來源' },
  { key: null, label: '標的' },
]
const SEARCH_COLS: Col[] = [
  { key: 'score', label: '相關度' },
  { key: 'match', label: '命中' },
]

interface Props {
  rows: ReportRow[]
  mode: SearchMode
  sort: TableSort
  onSort: (key: TableSortKey) => void
  onOpen: (id: string, fileName: string) => void
}

export function TableView({ rows, mode, sort, onSort, onOpen }: Props) {
  const cols = mode === 'search' ? [...BASE_COLS, ...SEARCH_COLS] : BASE_COLS
  const sorted = sortRows(rows, sort)
  const arrow = (key: TableSortKey) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '')
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {cols.map(c => (
              <th
                key={c.label}
                className={c.key ? styles.sortable : undefined}
                aria-sort={c.key && sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                onClick={c.key ? () => onSort(c.key as TableSortKey) : undefined}
              >
                {c.label}{c.key ? arrow(c.key) : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(r => {
            const targets = [...(r.stock_targets ?? []), ...(r.futures_targets ?? [])]
            return (
              <tr key={r.report_id} className={styles.row} onClick={() => onOpen(r.report_id, r.file_name)}>
                <td className={styles.name}>{r.file_name}</td>
                <td>
                  <span className={styles.badge} style={{ background: marketColor(r.market ?? '') }}>
                    {marketLabel(r.market ?? '')}
                  </span>
                </td>
                <td>{r.report_type ?? ''}</td>
                <td className={styles.nowrap}>{(r.report_date ?? '').slice(0, 10)}</td>
                <td className={styles.nowrap}>{r.source ?? ''}</td>
                <td>{targets.join('、')}</td>
                {mode === 'search' && <td className={styles.nowrap}>{Math.round((r.best_score ?? 0) * 100)}%</td>}
                {mode === 'search' && <td className={styles.nowrap}>{r.match_count ?? 0}</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 4: Implement `TableView.module.css`**

```css
.wrap {
  border: 1px solid var(--tf-border); border-radius: var(--tf-radius-card);
  overflow: hidden; overflow-x: auto; background: var(--tf-surface);
  box-shadow: var(--tf-shadow-xs); margin-top: 10px;
}
.table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.table th {
  padding: 9px 12px; font-weight: 600; text-align: left;
  color: var(--tf-text-3); background: var(--tf-border-weak); white-space: nowrap;
}
.sortable { cursor: pointer; user-select: none; }
.row { border-top: 1px solid var(--tf-border-weak); cursor: pointer; }
.row:hover { background: var(--tf-gold-tint); }
.table td { padding: 10px 12px; color: var(--tf-text-2); }
.name { color: var(--tf-text-1); font-weight: 500; max-width: 380px; }
.nowrap { white-space: nowrap; color: var(--tf-text-3); }
.badge { color: var(--tf-on-gold); border-radius: var(--tf-radius-pill); padding: 1px 8px; font-size: 11.5px; font-weight: 600; }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/TableView.test.tsx`（4 tests）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/search/TableView.tsx frontend/src/features/search/TableView.module.css frontend/src/features/search/TableView.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 TableView 平坦表格（欄位+表頭排序，標的不排）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `MonthGroup` + `CardsView`（卡片檢視，月分組）

**Files:**
- Create: `frontend/src/features/search/MonthGroup.tsx`(+`.module.css`)、`frontend/src/features/search/CardsView.tsx`(+`.module.css`)
- Test: `frontend/src/features/search/CardsView.test.tsx`

**Interfaces:**
- Consumes: `monthGroups` from `../../lib/grouping`；`ResultCard` from `./ResultCard`；`ReportRow` from `../../lib/schemas`；`SearchMode` from `../../lib/searchFilters`。
- Produces:
  - `MonthGroup({ title: string, count: number, children: ReactNode })` — sticky pill 月標頭 + 子內容插槽（**僅卡片檢視用**）。
  - `CardsView({ rows: ReportRow[], mode: SearchMode, terms: string[], latestId: string | null, onOpen })` — 對 rows 做月分組，逐組渲染 `MonthGroup` + 兩欄卡片網格。

- [ ] **Step 1: Write the failing tests**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CardsView } from './CardsView'
import { MonthGroup } from './MonthGroup'
import type { ReportRow } from '../../lib/schemas'

function row(id: string, date: string | null): ReportRow {
  return {
    report_id: id, file_name: id + '.pdf', market: 'TW', source: null, summary: null,
    report_date: date, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
  }
}

describe('MonthGroup', () => {
  it('顯示標題、篇數、子內容', () => {
    render(<MonthGroup title="2026 年 6 月" count={3}><div>卡片</div></MonthGroup>)
    expect(screen.getByText('2026 年 6 月')).toBeTruthy()
    expect(screen.getByText('3 篇')).toBeTruthy()
    expect(screen.getByText('卡片')).toBeTruthy()
  })
})

describe('CardsView', () => {
  it('依月分組並標記最新', () => {
    render(<CardsView rows={[row('a', '2026-06-25'), row('b', '2026-05-01')]}
      mode="browse" terms={[]} latestId="a" onOpen={() => {}} />)
    expect(screen.getByText('2026 年 6 月')).toBeTruthy()
    expect(screen.getByText('2026 年 5 月')).toBeTruthy()
    expect(screen.getByText('最新')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/CardsView.test.tsx`

- [ ] **Step 3: Implement `MonthGroup.tsx`**

```tsx
import type { ReactNode } from 'react'
import styles from './MonthGroup.module.css'

interface Props { title: string; count: number; children: ReactNode }

export function MonthGroup({ title, count, children }: Props) {
  return (
    <section>
      <div className={styles.header}>
        <span className={styles.pill}>
          <span className={styles.title}>{title}</span>
          <span className={styles.dot}>·</span>
          <span className={styles.count}>{count} 篇</span>
        </span>
      </div>
      {children}
    </section>
  )
}
```

`MonthGroup.module.css`：
```css
.header { position: sticky; top: 0; z-index: 2; padding: 14px 0 9px; display: flex; pointer-events: none; }
.pill {
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--tf-border-weak); border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-pill); padding: 5px 15px; box-shadow: var(--tf-shadow-xs);
}
.title { font-family: var(--tf-serif); font-weight: 700; font-size: 15px; color: var(--tf-text-1); }
.dot { font-size: 11px; color: var(--tf-gold-strong); line-height: 1; }
.count { font-size: 11.5px; font-weight: 600; color: var(--tf-gold-text); font-variant-numeric: tabular-nums; }
```

- [ ] **Step 4: Implement `CardsView.tsx`**

```tsx
import { monthGroups } from '../../lib/grouping'
import { MonthGroup } from './MonthGroup'
import { ResultCard } from './ResultCard'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './CardsView.module.css'

interface Props {
  rows: ReportRow[]
  mode: SearchMode
  terms: string[]
  latestId: string | null
  onOpen: (id: string, fileName: string) => void
}

export function CardsView({ rows, mode, terms, latestId, onOpen }: Props) {
  return (
    <div>
      {monthGroups(rows).map(g => (
        <MonthGroup key={g.key} title={g.title} count={g.count}>
          <div className={styles.grid}>
            {g.items.map(r => (
              <ResultCard
                key={r.report_id}
                row={r}
                mode={mode}
                terms={terms}
                isLatest={r.report_id === latestId}
                onOpen={onOpen}
              />
            ))}
          </div>
        </MonthGroup>
      ))}
    </div>
  )
}
```

`CardsView.module.css`：
```css
.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
@media (max-width: 1023px) { .grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/CardsView.test.tsx`（2 tests）

- [ ] **Step 6: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（0 errors）
```bash
git add frontend/src/features/search/MonthGroup.tsx frontend/src/features/search/MonthGroup.module.css frontend/src/features/search/CardsView.tsx frontend/src/features/search/CardsView.module.css frontend/src/features/search/CardsView.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 MonthGroup 月標頭與 CardsView 卡片檢視（月分組）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `SearchBar` + `MarketChipBar`

**Files:**
- Create: `frontend/src/features/search/SearchBar.tsx`(+`.module.css`)、`frontend/src/features/search/MarketChipBar.tsx`(+`.module.css`)
- Test: `frontend/src/features/search/SearchBar.test.tsx`、`frontend/src/features/search/MarketChipBar.test.tsx`

**Interfaces:**
- Consumes: `Icon` from `../../components/primitives/Icon`；`MARKET_ORDER`/`marketLabel` from `../../lib/meta`。
- Produces:
  - `SearchBar({ initial: string, onSubmit: (q: string) => void })`——本地 draft、Enter 送出、**IME 組字守衛（`e.nativeEvent.isComposing`）**、清除鈕送空字（回 browse）。
  - `MarketChipBar({ value: string, onChange: (m: string) => void, counts?: Record<string, number> })`——`全部`+`MARKET_ORDER`，`value==='ALL'` 代表全部。

- [ ] **Step 1: Write the failing tests**

`SearchBar.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SearchBar } from './SearchBar'

describe('SearchBar', () => {
  it('Enter 送出 trim 後的字', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '  台積電  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledWith('台積電')
  })
  it('IME 組字中的 Enter 不送出', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '注音' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })
  it('清除鈕送出空字（回瀏覽）', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="AI" onSubmit={onSubmit} />)
    fireEvent.click(screen.getByLabelText('清除搜尋'))
    expect(onSubmit).toHaveBeenCalledWith('')
  })
})
```

`MarketChipBar.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MarketChipBar } from './MarketChipBar'

describe('MarketChipBar', () => {
  it('渲染 全部 + 各市場、標示 active', () => {
    render(<MarketChipBar value="TW" onChange={() => {}} />)
    expect(screen.getByRole('tab', { name: '全部' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '台股' }).getAttribute('aria-selected')).toBe('true')
  })
  it('點擊 → onChange(code)', () => {
    const onChange = vi.fn()
    render(<MarketChipBar value="ALL" onChange={onChange} />)
    fireEvent.click(screen.getByRole('tab', { name: '美股' }))
    expect(onChange).toHaveBeenCalledWith('US')
  })
  it('顯示篇數', () => {
    render(<MarketChipBar value="ALL" onChange={() => {}} counts={{ TW: 500 }} />)
    expect(screen.getByRole('tab', { name: '台股 500' })).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SearchBar.test.tsx src/features/search/MarketChipBar.test.tsx`

- [ ] **Step 3: Implement `SearchBar.tsx`**

```tsx
import { useEffect, useState, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import styles from './SearchBar.module.css'

interface Props { initial: string; onSubmit: (q: string) => void }

export function SearchBar({ initial, onSubmit }: Props) {
  const [draft, setDraft] = useState(initial)
  useEffect(() => { setDraft(initial) }, [initial])   // 父層外部重設 q（如「瀏覽全部」）時同步
  const submit = () => onSubmit(draft.trim())
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // React 19：e.isComposing 恆 undefined，必須讀 nativeEvent.isComposing
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
  }
  return (
    <div className={styles.bar}>
      <Icon name="search" size={18} className={styles.icon} />
      <input
        className={styles.input}
        value={draft}
        placeholder="搜尋研報主題、個股、產業…"
        aria-label="搜尋研報"
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKey}
      />
      {draft && (
        <button type="button" className={styles.clear} aria-label="清除搜尋"
          onClick={() => { setDraft(''); onSubmit('') }}>
          <Icon name="x" size={16} />
        </button>
      )}
      <button type="button" className={styles.go} onClick={submit}>搜尋</button>
    </div>
  )
}
```

`SearchBar.module.css`：
```css
.bar { display: flex; align-items: center; gap: 8px; background: var(--tf-surface); border: 1px solid var(--tf-border); border-radius: var(--tf-radius-pill); padding: 8px 10px 8px 14px; box-shadow: var(--tf-shadow-xs); }
.icon { color: var(--tf-text-3); flex-shrink: 0; }
.input { flex: 1; min-width: 0; border: none; outline: none; background: none; font-size: 14px; color: var(--tf-text-1); font-family: inherit; }
.clear { border: none; background: none; cursor: pointer; color: var(--tf-text-3); display: flex; padding: 2px; }
.go { border: none; background: var(--tf-gold-strong); color: var(--tf-on-gold); border-radius: var(--tf-radius-pill); padding: 6px 16px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; flex-shrink: 0; }
```

- [ ] **Step 4: Implement `MarketChipBar.tsx`**

```tsx
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import styles from './MarketChipBar.module.css'

const ALL = 'ALL'

interface Props {
  value: string
  onChange: (market: string) => void
  counts?: Record<string, number>
}

export function MarketChipBar({ value, onChange, counts }: Props) {
  const chips = [ALL, ...MARKET_ORDER]
  return (
    <div className={styles.bar} role="tablist" aria-label="市場篩選">
      {chips.map(code => {
        const active = value === code
        const label = code === ALL ? '全部' : marketLabel(code)
        const n = counts?.[code]
        return (
          <button
            key={code}
            type="button"
            role="tab"
            aria-selected={active}
            className={`${styles.chip} ${active ? styles.active : ''}`}
            onClick={() => onChange(code)}
          >
            {label}{typeof n === 'number' ? ` ${n}` : ''}
          </button>
        )
      })}
    </div>
  )
}
```

`MarketChipBar.module.css`：
```css
.bar { display: flex; flex-wrap: wrap; gap: 7px; }
.chip { border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-2); border-radius: var(--tf-radius-pill); padding: 5px 13px; font-size: 12.5px; cursor: pointer; font-family: inherit; }
.chip:hover { border-color: var(--tf-gold-strong); }
.active { background: var(--tf-gold-tint); border-color: var(--tf-gold-strong); color: var(--tf-gold-text); font-weight: 600; }
```

- [ ] **Step 5: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SearchBar.test.tsx src/features/search/MarketChipBar.test.tsx`（6 tests）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/search/SearchBar.tsx frontend/src/features/search/SearchBar.module.css frontend/src/features/search/SearchBar.test.tsx frontend/src/features/search/MarketChipBar.tsx frontend/src/features/search/MarketChipBar.module.css frontend/src/features/search/MarketChipBar.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 SearchBar（IME 守衛）與 MarketChipBar 市場列

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `SortMenu` + `MoreFiltersPopover` + `ActiveChips`

**Files:**
- Create: `frontend/src/features/search/SortMenu.tsx`(+`.module.css`)、`frontend/src/features/search/MoreFiltersPopover.tsx`(+`.module.css`)、`frontend/src/features/search/ActiveChips.tsx`(+`.module.css`)
- Test: `frontend/src/features/search/SortMenu.test.tsx`、`frontend/src/features/search/MoreFiltersPopover.test.tsx`、`frontend/src/features/search/ActiveChips.test.tsx`

**Interfaces:**
- Consumes: `Icon` from `../../components/primitives/Icon`；`Popover` from `../../components/primitives/Popover`；`MenuItem` from `../../components/primitives/Menu`；`sortOptions` from `../../lib/sortForMode`；`SearchMode`/`SortValue`/`SearchState`/`activeAdvancedCount` from `../../lib/searchFilters`；`marketLabel` from `../../lib/meta`。
- Produces:
  - `SortMenu({ mode: SearchMode, value: SortValue, onChange: (v: SortValue) => void })`
  - `MoreFiltersPopover({ state: SearchState, instrumentOptions: string[], reportTypeOptions: string[], onPatch: (p: Partial<SearchState>) => void, onClear: () => void })`
  - `ActiveChips({ state: SearchState, onPatch: (p: Partial<SearchState>) => void })`——顯示作用中篩選為可刪 chip（market/instrument_type/report_type/relates_stock/relates_futures）。

- [ ] **Step 1: Write the failing tests**

`SortMenu.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SortMenu } from './SortMenu'

describe('SortMenu', () => {
  it('顯示目前排序、開選單、選取回呼', () => {
    const onChange = vi.fn()
    render(<SortMenu mode="search" value="relevance" onChange={onChange} />)
    expect(screen.getByText(/排序：相關度/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /排序/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: '日期（新→舊）' }))
    expect(onChange).toHaveBeenCalledWith('date_desc')
  })
  it('browse 模式無 relevance 選項', () => {
    render(<SortMenu mode="browse" value="date_desc" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /排序/ }))
    expect(screen.queryByRole('menuitem', { name: '相關度' })).toBeNull()
  })
})
```

`MoreFiltersPopover.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { defaultState } from '../../lib/searchFilters'

const base = { state: defaultState(), instrumentOptions: ['股票', '期貨'], reportTypeOptions: ['個股報告'] }

describe('MoreFiltersPopover', () => {
  it('badge 顯示作用中進階篩選數', () => {
    render(<MoreFiltersPopover {...base} state={{ ...defaultState(), report_type: '個股報告', relates_stock: true }}
      onPatch={() => {}} onClear={() => {}} />)
    expect(screen.getByText('2')).toBeTruthy()
  })
  it('切換商品類型 → onPatch', () => {
    const onPatch = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={onPatch} onClear={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.change(screen.getByLabelText('商品類型'), { target: { value: '股票' } })
    expect(onPatch).toHaveBeenCalledWith({ instrument_type: '股票' })
  })
  it('個股 checkbox → onPatch(relates_stock)', () => {
    const onPatch = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={onPatch} onClear={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.click(screen.getByLabelText('只看個股相關'))
    expect(onPatch).toHaveBeenCalledWith({ relates_stock: true })
  })
  it('清除 → onClear', () => {
    const onClear = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={() => {}} onClear={onClear} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.click(screen.getByRole('button', { name: '清除篩選' }))
    expect(onClear).toHaveBeenCalled()
  })
})
```

`ActiveChips.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ActiveChips } from './ActiveChips'
import { defaultState } from '../../lib/searchFilters'

describe('ActiveChips', () => {
  it('無作用中篩選 → 不渲染任何 chip', () => {
    const { container } = render(<ActiveChips state={defaultState()} onPatch={() => {}} />)
    expect(container.querySelectorAll('button').length).toBe(0)
  })
  it('市場 chip 可移除 → onPatch(market ALL)', () => {
    const onPatch = vi.fn()
    render(<ActiveChips state={{ ...defaultState(), market: 'TW' }} onPatch={onPatch} />)
    fireEvent.click(screen.getByRole('button', { name: /台股/ }))
    expect(onPatch).toHaveBeenCalledWith({ market: 'ALL' })
  })
  it('個股 chip 可移除 → onPatch(relates_stock false)', () => {
    const onPatch = vi.fn()
    render(<ActiveChips state={{ ...defaultState(), relates_stock: true }} onPatch={onPatch} />)
    fireEvent.click(screen.getByRole('button', { name: /個股/ }))
    expect(onPatch).toHaveBeenCalledWith({ relates_stock: false })
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SortMenu.test.tsx src/features/search/MoreFiltersPopover.test.tsx src/features/search/ActiveChips.test.tsx`

- [ ] **Step 3: Implement `SortMenu.tsx`**

```tsx
import { useRef, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Popover } from '../../components/primitives/Popover'
import { MenuItem } from '../../components/primitives/Menu'
import { sortOptions } from '../../lib/sortForMode'
import type { SearchMode, SortValue } from '../../lib/searchFilters'
import styles from './SortMenu.module.css'

interface Props { mode: SearchMode; value: SortValue; onChange: (v: SortValue) => void }

export function SortMenu({ mode, value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const opts = sortOptions(mode)
  const current = opts.find(o => o.value === value) ?? opts[0]
  return (
    <div className={styles.wrap} ref={ref}>
      <button type="button" className={styles.trigger} onClick={() => setOpen(o => !o)}
        aria-haspopup="menu" aria-expanded={open}>
        排序：{current.label}<Icon name="chevronDown" size={14} />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.menu}>
        {opts.map(o => (
          <MenuItem key={o.value} onClick={() => { onChange(o.value); setOpen(false) }}>{o.label}</MenuItem>
        ))}
      </Popover>
    </div>
  )
}
```

`SortMenu.module.css`：
```css
.wrap { position: relative; }
.trigger { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-2); border-radius: var(--tf-radius-seg); padding: 6px 12px; font-size: 13px; cursor: pointer; font-family: inherit; }
.menu { top: 40px; right: 0; min-width: 160px; }
```

- [ ] **Step 4: Implement `MoreFiltersPopover.tsx`**

```tsx
import { useRef, useState } from 'react'
import { Popover } from '../../components/primitives/Popover'
import { activeAdvancedCount, type SearchState } from '../../lib/searchFilters'
import styles from './MoreFiltersPopover.module.css'

interface Props {
  state: SearchState
  instrumentOptions: string[]
  reportTypeOptions: string[]
  onPatch: (p: Partial<SearchState>) => void
  onClear: () => void
}

export function MoreFiltersPopover({ state, instrumentOptions, reportTypeOptions, onPatch, onClear }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const count = activeAdvancedCount(state)
  return (
    <div className={styles.wrap} ref={ref}>
      <button type="button" className={styles.trigger} onClick={() => setOpen(o => !o)}
        aria-haspopup="menu" aria-expanded={open}>
        更多篩選{count > 0 && <span className={styles.badge}>{count}</span>}
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.menu}>
        <label className={styles.field}>
          <span>商品類型</span>
          <select aria-label="商品類型" value={state.instrument_type}
            onChange={e => onPatch({ instrument_type: e.target.value })}>
            <option value="">全部</option>
            {instrumentOptions.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className={styles.field}>
          <span>報告類型</span>
          <select aria-label="報告類型" value={state.report_type}
            onChange={e => onPatch({ report_type: e.target.value })}>
            <option value="">全部</option>
            {reportTypeOptions.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className={styles.check}>
          <input type="checkbox" aria-label="只看個股相關" checked={state.relates_stock}
            onChange={e => onPatch({ relates_stock: e.target.checked })} />只看個股相關
        </label>
        <label className={styles.check}>
          <input type="checkbox" aria-label="只看期貨相關" checked={state.relates_futures}
            onChange={e => onPatch({ relates_futures: e.target.checked })} />只看期貨相關
        </label>
        <button type="button" className={styles.clear} onClick={onClear}>清除篩選</button>
      </Popover>
    </div>
  )
}
```

`MoreFiltersPopover.module.css`：
```css
.wrap { position: relative; }
.trigger { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-2); border-radius: var(--tf-radius-seg); padding: 6px 12px; font-size: 13px; cursor: pointer; font-family: inherit; }
.badge { background: var(--tf-gold-strong); color: var(--tf-on-gold); border-radius: var(--tf-radius-pill); font-size: 11px; min-width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; padding: 0 4px; }
.menu { top: 40px; right: 0; min-width: 240px; display: flex; flex-direction: column; gap: 10px; padding: 12px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--tf-text-3); }
.field select { padding: 6px 8px; border: 1px solid var(--tf-border); border-radius: var(--tf-radius-md); font-family: inherit; font-size: 13px; color: var(--tf-text-1); background: var(--tf-surface); }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--tf-text-2); cursor: pointer; }
.clear { border: none; background: var(--tf-border-weak); color: var(--tf-text-2); border-radius: var(--tf-radius-md); padding: 7px; font-size: 13px; cursor: pointer; font-family: inherit; }
```

- [ ] **Step 5: Implement `ActiveChips.tsx`**

```tsx
import { marketLabel } from '../../lib/meta'
import type { SearchState } from '../../lib/searchFilters'
import styles from './ActiveChips.module.css'

interface ChipDef { label: string; patch: Partial<SearchState> }

interface Props {
  state: SearchState
  onPatch: (p: Partial<SearchState>) => void
}

export function ActiveChips({ state, onPatch }: Props) {
  const chips: ChipDef[] = []
  if (state.market !== 'ALL') chips.push({ label: `市場：${marketLabel(state.market)}`, patch: { market: 'ALL' } })
  if (state.instrument_type) chips.push({ label: `類型：${state.instrument_type}`, patch: { instrument_type: '' } })
  if (state.report_type) chips.push({ label: `報告：${state.report_type}`, patch: { report_type: '' } })
  if (state.relates_stock) chips.push({ label: '個股相關', patch: { relates_stock: false } })
  if (state.relates_futures) chips.push({ label: '期貨相關', patch: { relates_futures: false } })
  if (chips.length === 0) return null
  return (
    <div className={styles.bar}>
      {chips.map((c, i) => (
        <button key={i} type="button" className={styles.chip} onClick={() => onPatch(c.patch)}>
          {c.label}<span className={styles.x}>×</span>
        </button>
      ))}
    </div>
  )
}
```

`ActiveChips.module.css`：
```css
.bar { display: flex; flex-wrap: wrap; gap: 7px; }
.chip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--tf-gold-strong); background: var(--tf-gold-tint); color: var(--tf-gold-text); border-radius: var(--tf-radius-pill); padding: 4px 10px; font-size: 12px; cursor: pointer; font-family: inherit; }
.x { font-size: 14px; line-height: 1; }
```

- [ ] **Step 6: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SortMenu.test.tsx src/features/search/MoreFiltersPopover.test.tsx src/features/search/ActiveChips.test.tsx`（9 tests）

- [ ] **Step 7: Typecheck + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`（0 errors）
```bash
git add frontend/src/features/search/SortMenu.tsx frontend/src/features/search/SortMenu.module.css frontend/src/features/search/SortMenu.test.tsx frontend/src/features/search/MoreFiltersPopover.tsx frontend/src/features/search/MoreFiltersPopover.module.css frontend/src/features/search/MoreFiltersPopover.test.tsx frontend/src/features/search/ActiveChips.tsx frontend/src/features/search/ActiveChips.module.css frontend/src/features/search/ActiveChips.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 SortMenu／MoreFiltersPopover／ActiveChips 篩選控制

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: `ResultsMeta` + `EmptyState` + `LoadMore` + `ViewSwitch`

**Files:**
- Create: `frontend/src/features/search/ResultsMeta.tsx`(+css)、`EmptyState.tsx`(+css)、`LoadMore.tsx`(+css)、`ViewSwitch.tsx`(+css)（皆於 `frontend/src/features/search/`）
- Test: 對應 4 個 `*.test.tsx`

**Interfaces:**
- Consumes: `EmptyStateCopy` from `../../lib/resultsMeta`；`ViewMode` from `../../lib/searchFilters`。
- Produces:
  - `ResultsMeta({ text: string })`
  - `EmptyState({ copy: EmptyStateCopy, onClear: () => void, onBrowseAll: () => void })`
  - `LoadMore({ remaining: number, loading: boolean, onClick: () => void })`
  - `ViewSwitch({ view: ViewMode, onChange: (v: ViewMode) => void })`（固定右下浮動 segmented）

- [ ] **Step 1: Write the failing tests**

`ResultsMeta.test.tsx`：
```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ResultsMeta } from './ResultsMeta'

it('渲染 meta 文字', () => {
  render(<ResultsMeta text="全部研報 — 共 100 篇" />)
  expect(screen.getByText('全部研報 — 共 100 篇')).toBeTruthy()
})
```

`EmptyState.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('search：清除篩選再試 + 瀏覽全部報告 皆顯示且回呼', () => {
    const onClear = vi.fn(); const onBrowseAll = vi.fn()
    render(<EmptyState copy={{ title: '找不到「x」的相關研報', hint: 'h', showClear: true, showBrowseAll: true }}
      onClear={onClear} onBrowseAll={onBrowseAll} />)
    fireEvent.click(screen.getByRole('button', { name: '清除篩選再試' }))
    fireEvent.click(screen.getByRole('button', { name: '瀏覽全部報告' }))
    expect(onClear).toHaveBeenCalled(); expect(onBrowseAll).toHaveBeenCalled()
  })
  it('browse 無篩選：無任何 CTA', () => {
    render(<EmptyState copy={{ title: '沒有符合條件的研報', hint: 'h', showClear: false, showBrowseAll: false }}
      onClear={() => {}} onBrowseAll={() => {}} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
```

`LoadMore.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoadMore } from './LoadMore'

describe('LoadMore', () => {
  it('顯示剩餘數、點擊回呼', () => {
    const onClick = vi.fn()
    render(<LoadMore remaining={30} loading={false} onClick={onClick} />)
    expect(screen.getByRole('button', { name: /還有 30 篇/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalled()
  })
  it('loading 時停用', () => {
    render(<LoadMore remaining={30} loading onClick={() => {}} />)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })
})
```

`ViewSwitch.test.tsx`：
```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ViewSwitch } from './ViewSwitch'

describe('ViewSwitch', () => {
  it('切表格 → onChange(table)、active 標示', () => {
    const onChange = vi.fn()
    render(<ViewSwitch view="cards" onChange={onChange} />)
    expect(screen.getByRole('button', { name: '卡片檢視' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: '表格檢視' }))
    expect(onChange).toHaveBeenCalledWith('table')
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/ResultsMeta.test.tsx src/features/search/EmptyState.test.tsx src/features/search/LoadMore.test.tsx src/features/search/ViewSwitch.test.tsx`

- [ ] **Step 3: Implement the four components**

`ResultsMeta.tsx`：
```tsx
import styles from './ResultsMeta.module.css'
export function ResultsMeta({ text }: { text: string }) {
  return <div className={styles.meta}>{text}</div>
}
```
`ResultsMeta.module.css`：
```css
.meta { font-size: 13px; color: var(--tf-text-3); padding: 4px 0 10px; }
```

`EmptyState.tsx`：
```tsx
import type { EmptyStateCopy } from '../../lib/resultsMeta'
import styles from './EmptyState.module.css'

interface Props { copy: EmptyStateCopy; onClear: () => void; onBrowseAll: () => void }

export function EmptyState({ copy, onClear, onBrowseAll }: Props) {
  return (
    <div className={styles.state}>
      <div className={styles.title}>{copy.title}</div>
      <div className={styles.hint}>{copy.hint}</div>
      <div className={styles.actions}>
        {copy.showClear && <button type="button" className={styles.btn} onClick={onClear}>清除篩選再試</button>}
        {copy.showBrowseAll && <button type="button" className={styles.btn} onClick={onBrowseAll}>瀏覽全部報告</button>}
      </div>
    </div>
  )
}
```
`EmptyState.module.css`：
```css
.state { text-align: center; padding: 56px 16px; }
.title { font-family: var(--tf-serif); font-weight: 700; font-size: 17px; color: var(--tf-text-1); }
.hint { font-size: 13px; color: var(--tf-text-3); margin-top: 8px; }
.actions { display: flex; gap: 10px; justify-content: center; margin-top: 18px; }
.btn { border: 1px solid var(--tf-gold-strong); background: var(--tf-surface); color: var(--tf-gold-text); border-radius: var(--tf-radius-pill); padding: 7px 16px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }
```

`LoadMore.tsx`：
```tsx
import styles from './LoadMore.module.css'

interface Props { remaining: number; loading: boolean; onClick: () => void }

export function LoadMore({ remaining, loading, onClick }: Props) {
  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.btn} disabled={loading} onClick={onClick}>
        {loading ? '載入中…' : `載入更多（還有 ${remaining.toLocaleString()} 篇）`}
      </button>
    </div>
  )
}
```
`LoadMore.module.css`：
```css
.wrap { display: flex; justify-content: center; padding: 20px 0 40px; }
.btn { border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-2); border-radius: var(--tf-radius-pill); padding: 9px 22px; font-size: 13px; cursor: pointer; font-family: inherit; }
.btn:disabled { opacity: .6; cursor: default; }
```

`ViewSwitch.tsx`：
```tsx
import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

export function ViewSwitch({ view, onChange }: Props) {
  return (
    <div className={styles.wrap} role="group" aria-label="檢視切換">
      <button type="button" aria-label="卡片檢視" aria-pressed={view === 'cards'}
        className={`${styles.btn} ${view === 'cards' ? styles.active : ''}`} onClick={() => onChange('cards')}>▦</button>
      <button type="button" aria-label="表格檢視" aria-pressed={view === 'table'}
        className={`${styles.btn} ${view === 'table' ? styles.active : ''}`} onClick={() => onChange('table')}>≣</button>
    </div>
  )
}
```
`ViewSwitch.module.css`：
```css
.wrap { position: fixed; right: 24px; bottom: 24px; z-index: 30; display: inline-flex; gap: 2px; background: var(--tf-surface); border: 1px solid var(--tf-border); border-radius: var(--tf-radius-pill); padding: 3px; box-shadow: var(--tf-shadow-viewtoggle); }
.btn { border: none; background: none; width: 34px; height: 34px; border-radius: var(--tf-radius-pill); cursor: pointer; font-size: 16px; color: var(--tf-text-3); }
.active { background: var(--tf-gold-tint); color: var(--tf-gold-text); }
@media (max-width: 1023px) { .wrap { bottom: 80px; } }
```

- [ ] **Step 4: Run to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/ResultsMeta.test.tsx src/features/search/EmptyState.test.tsx src/features/search/LoadMore.test.tsx src/features/search/ViewSwitch.test.tsx`（6 tests）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/ResultsMeta.tsx frontend/src/features/search/ResultsMeta.module.css frontend/src/features/search/ResultsMeta.test.tsx frontend/src/features/search/EmptyState.tsx frontend/src/features/search/EmptyState.module.css frontend/src/features/search/EmptyState.test.tsx frontend/src/features/search/LoadMore.tsx frontend/src/features/search/LoadMore.module.css frontend/src/features/search/LoadMore.test.tsx frontend/src/features/search/ViewSwitch.tsx frontend/src/features/search/ViewSwitch.module.css frontend/src/features/search/ViewSwitch.test.tsx
git commit -m "$(cat <<'EOF'
feat(檢索): 新增 ResultsMeta／EmptyState／LoadMore／ViewSwitch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: `SearchPage` 整合 + 路由 + URL 同步

**Files:**
- Modify（覆寫 placeholder）: `frontend/src/features/search/SearchPage.tsx`（**須 `export default`**，App.tsx 以 `lazy(() => import('./features/search/SearchPage'))` 載入，路由 `/search` 已就緒不需改）
- Create: `frontend/src/features/search/SearchPage.module.css`
- Modify: `frontend/e2e/shell.spec.ts:17`（Phase 0 斷言 placeholder 文字，本任務改為斷言搜尋框）
- Test: `frontend/src/features/search/SearchPage.test.tsx`

**Interfaces:**
- Consumes: `useSearchParams` from `react-router`；`useStats`、`useSearchResults`、`searchFilters`（`parseParams`/`buildParams`/`modeOf`/`normalizeSort`/`hasAnyFilter`/`clearFilters`/`browseAll`）、`queryTerms`、`latestId`、`resultsMeta`（`resultsMetaText`/`emptyState`）、`TableSort`/`TableSortKey`、以及 Task 10-15 全部子元件與 `ReportDetailModal`。
- Produces: `export default function SearchPage()`——URL 為單一真相；`view`/`sort`/篩選寫入 URL、`tableSort` 為本地呈現態；每次狀態變更經 `normalizeSort` 保持排序合法（清空 q → browse 自動回退 date_desc）。

- [ ] **Step 1: Write the failing test**

`SearchPage.test.tsx`：
```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router'
import type { ReactNode } from 'react'
import SearchPage from './SearchPage'
import * as searchApi from '../../lib/searchApi'
import * as useStatsMod from '../../lib/useStats'

vi.mock('../../lib/searchApi')
vi.mock('../../lib/useStats')

function listResp(ids: string[], total: number) {
  return {
    total, offset: 0,
    items: ids.map(id => ({
      report_id: id, file_name: id + '.pdf', market: 'TW', source: '元大', summary: '摘要',
      report_date: '2026-06-25', report_type: '個股', instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    })),
  }
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/search']}>
        <Routes><Route path="/search" element={node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(useStatsMod.useStats).mockReturnValue({
    data: { markets: [{ market: 'TW', count: 5 }], instrument_types: [], report_types: [] },
  } as unknown as ReturnType<typeof useStatsMod.useStats>)
})

describe('SearchPage 整合', () => {
  it('browse 載入結果並渲染卡片 + meta', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />)
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(screen.getByText(/共 2 篇/)).toBeTruthy()
  })

  it('切表格檢視不重抓（browseReports 次數不變）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a'], 1))
    wrap(<SearchPage />)
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '表格檢視' }))
    await waitFor(() => expect(screen.getAllByRole('columnheader').length).toBeGreaterThan(0))
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run to verify FAIL** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SearchPage.test.tsx`（placeholder 無 `a.pdf`）

- [ ] **Step 3: Implement `SearchPage.tsx`**（覆寫 placeholder）

```tsx
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useStats } from '../../lib/useStats'
import { useSearchResults } from '../../lib/useSearchResults'
import {
  parseParams, buildParams, modeOf, hasAnyFilter,
  clearFilters, browseAll, type SearchState,
} from '../../lib/searchFilters'
import { normalizeSort } from '../../lib/sortForMode'
import { queryTerms } from '../../lib/terms'
import { latestId } from '../../lib/isLatest'
import { resultsMetaText, emptyState } from '../../lib/resultsMeta'
import type { TableSort, TableSortKey } from '../../lib/tableSort'
import { SearchBar } from './SearchBar'
import { MarketChipBar } from './MarketChipBar'
import { SortMenu } from './SortMenu'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { ActiveChips } from './ActiveChips'
import { ResultsMeta } from './ResultsMeta'
import { CardsView } from './CardsView'
import { TableView } from './TableView'
import { EmptyState } from './EmptyState'
import { LoadMore } from './LoadMore'
import { ViewSwitch } from './ViewSwitch'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import styles from './SearchPage.module.css'

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const state = useMemo(() => parseParams(params.toString()), [params])
  const mode = modeOf(state)
  const [tableSort, setTableSort] = useState<TableSort>({ key: 'date', dir: 'desc' })
  const [openReport, setOpenReport] = useState<{ id: string; fileName: string } | null>(null)

  const stats = useStats()
  const results = useSearchResults(state)
  const terms = useMemo(() => queryTerms(state.q), [state.q])
  const latest = useMemo(() => latestId(results.rows), [results.rows])

  function applyState(next: SearchState) {
    setParams(buildParams({ ...next, sort: normalizeSort(modeOf(next), next.sort) }))
  }
  const update = (patch: Partial<SearchState>) => applyState({ ...state, ...patch })

  const marketCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const it of stats.data?.markets ?? []) m[it.market] = it.count
    return m
  }, [stats.data])
  const instrumentOptions = (stats.data?.instrument_types ?? []).map(t => t.type)
  const reportTypeOptions = (stats.data?.report_types ?? []).map(t => t.type)

  function onSort(key: TableSortKey) {
    setTableSort(s => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }
  const onOpen = (id: string, fileName: string) => setOpenReport({ id, fileName })

  return (
    <div className={styles.page}>
      <div className={styles.controls}>
        <SearchBar initial={state.q} onSubmit={q => update({ q })} />
        <MarketChipBar value={state.market} onChange={m => update({ market: m })} counts={marketCounts} />
        <div className={styles.toolbar}>
          <SortMenu mode={mode} value={state.sort} onChange={v => update({ sort: v })} />
          <MoreFiltersPopover
            state={state}
            instrumentOptions={instrumentOptions}
            reportTypeOptions={reportTypeOptions}
            onPatch={update}
            onClear={() => applyState(clearFilters(state))}
          />
        </div>
        <ActiveChips state={state} onPatch={update} />
      </div>

      {results.isError ? (
        <div className={styles.error}>
          載入失敗，請稍後再試。<button type="button" onClick={results.refetch}>重試</button>
        </div>
      ) : results.isLoading ? (
        <div className={styles.loading} aria-busy="true">載入中…</div>
      ) : results.total === 0 ? (
        <EmptyState
          copy={emptyState(state, hasAnyFilter(state))}
          onClear={() => applyState(clearFilters(state))}
          onBrowseAll={() => applyState(browseAll(state))}
        />
      ) : (
        <>
          <ResultsMeta text={resultsMetaText(state, results.total)} />
          {state.view === 'table' ? (
            <TableView rows={results.rows} mode={mode} sort={tableSort} onSort={onSort} onOpen={onOpen} />
          ) : (
            <CardsView rows={results.rows} mode={mode} terms={terms} latestId={latest} onOpen={onOpen} />
          )}
          {results.hasMore && (
            <LoadMore remaining={results.remaining} loading={results.isFetchingMore} onClick={results.loadMore} />
          )}
        </>
      )}

      <ViewSwitch view={state.view} onChange={v => update({ view: v })} />
      <ReportDetailModal
        reportId={openReport?.id ?? null}
        fileName={openReport?.fileName}
        onClose={() => setOpenReport(null)}
      />
    </div>
  )
}
```

- [ ] **Step 4: Implement `SearchPage.module.css`**

```css
.page { max-width: 1120px; margin: 0 auto; padding: 20px 24px 80px; }
.controls { display: flex; flex-direction: column; gap: 12px; margin-bottom: 8px; }
.toolbar { display: flex; align-items: center; gap: 10px; justify-content: flex-end; }
.loading, .error { text-align: center; padding: 56px 16px; color: var(--tf-text-3); }
.error button { margin-left: 10px; border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: var(--tf-radius-pill); padding: 4px 12px; cursor: pointer; font-family: inherit; }
```

- [ ] **Step 5: Fix Phase 0 placeholder assertions（e2e + 單元）**

覆寫 placeholder 會讓兩處 Phase 0 測試對 placeholder 文字的斷言失敗，皆改為斷言新搜尋框：

`frontend/e2e/shell.spec.ts` 第 17 行 `page.getByText('檢索頁（Phase 1 實作）')` → `page.getByLabel('搜尋研報')`。

`frontend/src/App.test.tsx`（`/search 落在…` 測試）的 `findByText('檢索頁（Phase 1 實作）')` → `findByLabelText('搜尋研報', {}, { timeout: 5000 })`（SearchPage 現為較大的 lazy chunk，動態載入可能超過預設 1000ms，故放寬逾時；斷言搜尋框存在＝真渲染，非套套邏輯）。

- [ ] **Step 6: Run unit test to verify PASS** — `cd frontend && ./node_modules/.bin/vitest run src/features/search/SearchPage.test.tsx`（2 tests）

- [ ] **Step 7: Typecheck + build + commit**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build`（皆成功）
```bash
git add frontend/src/features/search/SearchPage.tsx frontend/src/features/search/SearchPage.module.css frontend/src/features/search/SearchPage.test.tsx frontend/e2e/shell.spec.ts
git commit -m "$(cat <<'EOF'
feat(檢索): SearchPage 整合（URL 真相、抓取/檢視/篩選/分頁/詳情 modal 串接）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: e2e 冒煙（`:8098`）

**Files:**
- Create: `frontend/e2e/search.spec.ts`

**Interfaces:**
- Consumes: `@playwright/test`；既有 `playwright.config.ts`（`baseURL http://127.0.0.1:8098`, timeout 30s）。
- Produces: 一支端到端冒煙（login → browse → 搜尋 → 表格 → 開詳情）。

> **執行前置（controller）**：本測試打**真實後端 :8098**（工作樹起的預覽服務，非正式 :8097）。需 DB 可連 + **BGE-M3 已暖機**（首次搜尋會冷啟動嵌入模型，故搜尋步驟逾時放寬到 90s；搜尋為純檢索，不需 `claude` CLD/LLM）。跑完以 `ss` 核對埠後再 kill，勿誤殺 :8097。

- [ ] **Step 1: Write the e2e spec**

`frontend/e2e/search.spec.ts`：
```ts
import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')      // Phase 1 尚未 cutover
  await page.goto('/app/search')
}

test('browse → 搜尋 → 表格檢視 → 開詳情 modal', async ({ page }) => {
  await login(page)

  // browse 載入（快，無嵌入）：搜尋框可見 + 結果 meta 出現
  await expect(page.getByLabel('搜尋研報')).toBeVisible()
  await expect(page.getByText(/共 .* 篇/)).toBeVisible({ timeout: 15_000 })

  // 搜尋（首次可能冷啟動 BGE-M3，放寬逾時）
  await page.getByLabel('搜尋研報').fill('台積電')
  await page.getByLabel('搜尋研報').press('Enter')
  await expect(page.getByText(/找到 .* 篇研報/)).toBeVisible({ timeout: 90_000 })

  // 切表格檢視 → 出現表頭「報告名稱」
  await page.getByRole('button', { name: '表格檢視' }).click()
  await expect(page.getByRole('columnheader', { name: /報告名稱/ })).toBeVisible()

  // 點第一列 → 詳情 modal 開啟
  await page.locator('tbody tr').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15_000 })
})
```

- [ ] **Step 2: Run the e2e（controller，後端 :8098 已起且暖機）**

Run: `cd frontend && ./node_modules/.bin/playwright test e2e/search.spec.ts`
Expected: 1 passed。（若搜尋逾時，先確認 :8098 後端可達且 BGE-M3 已載入。）

- [ ] **Step 3: Full acceptance sweep**

Run（收斂用焦點檔已在各任務跑過，此處跑整體）：
```bash
cd frontend
./node_modules/.bin/vitest run
./node_modules/.bin/eslint .
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build
```
Expected: vitest 全綠、eslint 0、tsc 0、build 成功。（WSL 若 vitest fork worker flake，改跑各 `src/lib/*.test.ts` 與 `src/features/search/*.test.tsx` 焦點檔 + build 收斂。）

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/search.spec.ts
git commit -m "$(cat <<'EOF'
test(檢索): 新增檢索頁 e2e 冒煙（browse/搜尋/表格/詳情）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review（計畫對 spec 的覆蓋檢查）

**1. Spec 覆蓋**（每節都可對到任務）：
- §0 決策表 → 全計畫 Global Constraints + 各任務。
- §1 版面 → Task 16 SearchPage + `SearchPage.module.css`。
- §2 元件分解 → Tasks 8-16；§2.1 lib 純函式 → Tasks 1-7。
- §3 ResultCard 兩模式 → Task 10。
- §4 資料流/react-query（view/tableSort 不進 query key）→ Task 7 + 16。
- §5 isLatest（嚴格大於、同日第一筆、無日期不參與，鏡像 answer.py）→ Task 6。
- §6 篩選↔URL（單值、選項來自 /api/stats）→ Task 2 + 16（`marketCounts`/`instrumentOptions`/`reportTypeOptions`）。
- §7 文案（ResultsMeta 三分支、EmptyState 兩 CTA、LoadMore、月標頭）→ Task 3 + 12 + 15。
- §8 Modal + ReportDetailModal 四分支 → Task 8 + 9。
- §9 命中高亮（XSS 安全 `<mark>`）→ Task 5。
- §10 測試（含 sortForMode 回退、TableView 欄位、Modal 四分支、view 不重抓/sort 重抓）→ 各任務單元測試 + Task 16 整合 + Task 17 e2e。
- §11 後端契約（/reports、/search、/report/{id}/full、/file、/stats）→ Task 1。
- §12 非目標（不做市場分組/索引/drill-in）→ 未建任何相關元件。
- §13 驗收 → Task 17 Step 3 全套 + e2e。

**2. Placeholder 掃描**：無 TBD/TODO；每段程式碼皆完整、測試皆含實碼與預期輸出。

**3. 型別一致性**（跨任務）：
- `onOpen: (id: string, fileName: string) => void` — ResultCard / TableView / CardsView / SearchPage 一致。
- `ReportRow`（= `ReportResult` 超集，browse 時 score/match/passages 為 undefined）— 卡片/表格/分組/isLatest/tableSort 一致消費。
- `SortValue`/`ViewMode`/`SearchState`/`SearchMode` — searchFilters 定義，全程一致。
- `TableSort`/`TableSortKey`（鍵 `name/market/type/date/source/score/match`）— tableSort.ts 定義，TableView/SearchPage 一致。
- `UseSearchResults`（`rows/total/isLoading/isError/refetch/hasMore/remaining/isFetchingMore/loadMore`）— Task 7 定義，Task 16 全數消費。
- `EmptyStateCopy`（`title/hint/showClear/showBrowseAll`）— resultsMeta.ts 定義，EmptyState 消費。
- `SearchPage` 為 **default export**（對齊 App.tsx `lazy(import(...))`）。

**已於撰寫時就地修正**：（a）highlight 的 `<mark>` 需全域樣式 → Task 5 Step 4b 追加 tokens.css 規則；（b）Phase 0 `e2e/shell.spec.ts` 斷言 placeholder 文字 → Task 16 Step 5 改斷言搜尋框；（c）新增 `CardsView` 作為卡片檢視容器（§2 MonthGroup 的使用者）。

**已知簡化（非 spec 缺口）**：載入態用簡單「載入中…」文字而非骨架列（vanilla 有骨架；設計稿未強制，屬視覺細節，可於後續 polish 補）；表格列不顯「最新」徽章（對齊 `.dc.html` `showTable`，徽章為卡片專屬）。

---

## 執行順序與相依

Tasks 1-7（lib，純函式/hook，無 UI 相依，可依序快速過）→ 8-9（Modal/詳情）→ 10-15（呈現元件，依賴 lib）→ 16（整合，依賴全部）→ 17（e2e，依賴 16）。每任務獨立可測、獨立 commit。建議實作 model：Tasks 1-6、8、10-15 為「計畫含完整程式碼」的轉錄型任務（可用最便宜模型 implementer + 機械式 reviewer）；Task 7（react-query）、9、16（整合）用標準模型；最終整支 review 用最強模型。

