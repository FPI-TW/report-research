# Phase 3 監控頁遷移（/app/monitor）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「研報導入監控」頁從 vanilla 遷移到 React 19 SPA 的 `/app/monitor`，逐像素落地 `.dc.html` MONITOR 畫面、功能對等 vanilla `tick()`，資料源 `GET /api/progress`（後端零改動）。

**Architecture:** react-query 5s 輪詢 hook（`keepPreviousData` 不閃爍）+ 本地 1s 時鐘 + 開頁基準法速率（純函式，移植 `eta.js`/`rate()`）→ 純呈現子元件（KPI/語意標註/報告導入/摘要生成/處理管線/市場分佈）。取代現有 3 行 stub `MonitorPage.tsx`。

**Tech Stack:** React 19、TypeScript、@tanstack/react-query 5.101、Zod 4、Vitest + @testing-library/react、CSS Modules；沿用 Phase 0/1/2 資產 `lib/api`(getJSON)、`lib/meta`(marketColor/marketLabel)、`styles/tokens.css`(`tf-pulse`/`tf-indet`/`tf-spin` keyframes)。

## Global Constraints

- **後端零改動**：只消費既有 `GET /api/progress`，不新增/改任何後端路由或回應。
- **設計權威＝`.dc.html` MONITOR 畫面（第 389–460 行）**；**功能對等＝vanilla `web/static/monitor.html` 的 `tick()`**。
- **輪詢間隔＝5000ms**；即時時鐘本地 1000ms（不吃請求）。
- **繁中文案逐字**；所有數字 `font-variant-numeric: tabular-nums`。
- 所有檔案置於 `frontend/src/features/monitor/`（schema/hooks/純函式/元件同資料夾）。
- 測試/驗證**直呼 binary**（npm script/RTK 會遮 exit）：`./node_modules/.bin/vitest run <files>`、`./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/eslint <paths>`、`./node_modules/.bin/vite build`。`tsconfig` 為 `noUnusedLocals: true`（勿留未用 import/變數）。
- 消費既有資產不得修改：`src/lib/api.ts`、`src/lib/meta.ts`、`src/styles/tokens.css`、`src/App.tsx`（`/monitor` 路由與 `lazy(() => import('./features/monitor/MonitorPage'))` 已存在，指向 default export）。
- **不含 cutover**（`/` 仍 vanilla）；不動 monitor 以外檔案。
- **建議模型（SDD）**：Task 1–5（schema/純函式/hooks，附完整碼＝轉錄+TDD）haiku；Task 6–11（元件/整合）sonnet；Task 12 e2e sonnet。

## 後端契約（`GET /api/progress`，消費、零改動）

```jsonc
{
  "ts": "HH:MM:SS",
  "db": { "reports": 12345, "chunks": 456789, "markets": [{ "market": "TW", "count": 8123 }] },
  "summary": { "done": 900, "total": 1000, "remaining": 100, "pct": 90.0 },
  "tagging": { "done": 800, "total": 1000, "fail": 200, "pct": 80.0 } | null,
  "ingest":  { "ingested": 42, "chunks": 3100, "fail": 1 } | null,
  "pipelines": { "web": true, "ingest": false, "tag": true, "summaries": false },
  "orchestrator": { "raw": "...", "timestamp": "..."|null, "status": "...", "label": "編排器執行中" } | null
}
```
（`tagging.fail`＝未標數＝標註剩餘；`db.markets[].market` 可能為 null，需守門。）

## File Structure（`frontend/src/features/monitor/`）

| 檔案 | 責任 | Task |
|---|---|---|
| `progressSchema.ts` | Zod `progressSchema` + 型別 | 1 |
| `rate.ts` | 純函式 `computeRates`/`rateText`/`ingestRateText`/`fmtInt` | 2 |
| `useClock.ts` | 本地 1s 時鐘 | 3 |
| `useProgress.ts` | react-query 5s 輪詢 | 4 |
| `useRates.ts` | baseline ref + computeRates | 5 |
| `MonitorPage.module.css` | 全頁樣式（Task 6 一次建立完整表） | 6 |
| `KpiCard.tsx` / `KpiGrid.tsx` | KPI 4 卡 | 6 |
| `ProgressPanel.tsx` | 語意標註/摘要生成共用 | 7 |
| `IngestPanel.tsx` | 報告導入 | 8 |
| `PipelineStatus.tsx` | 4 管線狀態 | 9 |
| `MarketDistribution.tsx` | 市場分佈 | 10 |
| `MonitorPage.tsx` | 組合（取代 stub） | 11 |
| `e2e/monitor.spec.ts` | 輕量冒煙（寫檔+type-check，live 延後） | 12 |

---

### Task 1: progressSchema.ts（Zod 驗證 + 型別）

**Files:**
- Create: `frontend/src/features/monitor/progressSchema.ts`
- Test: `frontend/src/features/monitor/progressSchema.test.ts`

**Interfaces:**
- Produces: `progressSchema`（ZodType）、型別 `Progress`, `Tagging`, `Ingest`, `Summary`, `Pipelines`, `MarketCount`, `Orchestrator`。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/progressSchema.test.ts`

```ts
import { expect, test } from 'vitest'
import { progressSchema } from './progressSchema'

const full = {
  ts: '12:00:00',
  db: { reports: 100, chunks: 5000, markets: [{ market: 'TW', count: 60 }] },
  summary: { done: 90, total: 100, remaining: 10, pct: 90 },
  tagging: { done: 80, total: 100, fail: 20, pct: 80 },
  ingest: { ingested: 5, chunks: 300, fail: 0 },
  pipelines: { web: true, ingest: false, tag: true, summaries: false },
  orchestrator: { raw: 'x', timestamp: null, status: 'running', label: '編排器執行中' },
}

test('解析完整 progress', () => {
  const p = progressSchema.parse(full)
  expect(p.db.reports).toBe(100)
  expect(p.db.markets[0].market).toBe('TW')
  expect(p.tagging?.pct).toBe(80)
})

test('tagging/ingest/orchestrator 可為 null', () => {
  const p = progressSchema.parse({ ...full, tagging: null, ingest: null, orchestrator: null })
  expect(p.tagging).toBeNull()
  expect(p.ingest).toBeNull()
  expect(p.orchestrator).toBeNull()
})

test('markets.market 可為 null', () => {
  const p = progressSchema.parse({ ...full, db: { reports: 1, chunks: 1, markets: [{ market: null, count: 3 }] } })
  expect(p.db.markets[0].market).toBeNull()
})

test('缺必要欄位 → throw', () => {
  expect(() => progressSchema.parse({ ...full, db: { reports: 1 } })).toThrow()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/progressSchema.test.ts`（Expected: FAIL，找不到模組）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/progressSchema.ts`

```ts
import { z } from 'zod'

export const marketCountSchema = z.object({ market: z.string().nullable(), count: z.number() })
export const taggingSchema = z.object({ done: z.number(), total: z.number(), fail: z.number(), pct: z.number() })
export const ingestSchema = z.object({ ingested: z.number(), chunks: z.number(), fail: z.number() })
export const summarySchema = z.object({ done: z.number(), total: z.number(), remaining: z.number(), pct: z.number() })
export const pipelinesSchema = z.object({ web: z.boolean(), ingest: z.boolean(), tag: z.boolean(), summaries: z.boolean() })
export const orchestratorSchema = z.object({
  raw: z.string(),
  timestamp: z.string().nullable(),
  status: z.string(),
  label: z.string(),
})

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketCountSchema),
  }),
  summary: summarySchema,
  tagging: taggingSchema.nullable(),
  ingest: ingestSchema.nullable(),
  pipelines: pipelinesSchema,
  orchestrator: orchestratorSchema.nullable(),
})

export type Progress = z.infer<typeof progressSchema>
export type Tagging = z.infer<typeof taggingSchema>
export type Ingest = z.infer<typeof ingestSchema>
export type Summary = z.infer<typeof summarySchema>
export type Pipelines = z.infer<typeof pipelinesSchema>
export type MarketCount = z.infer<typeof marketCountSchema>
export type Orchestrator = z.infer<typeof orchestratorSchema>
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/progressSchema.test.ts`（Expected: PASS）；`./node_modules/.bin/tsc --noEmit` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/progressSchema.ts frontend/src/features/monitor/progressSchema.test.ts
git commit -m "$(cat <<'EOF'
feat(監控): progressSchema — /api/progress Zod 驗證與型別

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: rate.ts（速率/ETA 純函式，移植 eta.js + rate()）

**Files:**
- Create: `frontend/src/features/monitor/rate.ts`
- Test: `frontend/src/features/monitor/rate.test.ts`

**Interfaces:**
- Produces:
  - `interface Rates { rpm: number|null; cps: number|null; spm: number|null; tpm: number|null }`
  - `interface RateInputs { reports: number; chunks: number; sumDone: number|null; tagDone: number|null }`
  - `interface Baseline { reports: number; chunks: number; sum: number; tag: number }`
  - `computeRates(cur: RateInputs, base: Baseline, elapsedSec: number): Rates`
  - `rateText(remaining: number, rate: number|null, unit: string): string`
  - `ingestRateText(rpm: number|null, cps: number|null): string`
  - `fmtInt(n: number): string`

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/rate.test.ts`

```ts
import { expect, test } from 'vitest'
import { computeRates, rateText, ingestRateText, fmtInt } from './rate'

const B = { reports: 0, chunks: 0, sum: 0, tag: 0 }

test('rateText：rate null → 計算中', () => {
  expect(rateText(10, null, '摘要')).toBe('速率 計算中…')
})
test('rateText：rate<0.05 → 只顯速率不給 ETA', () => {
  expect(rateText(10, 0.04, '摘要')).toBe('速率 0.0 摘要/分')
})
test('rateText：remaining<=0 → 已完成', () => {
  expect(rateText(0, 2, '標註')).toBe('速率 2.0 標註/分 · 已完成')
})
test('rateText：mins<90 用「分」', () => {
  expect(rateText(20, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~10 分')
})
test('rateText：mins>=90 用「時」', () => {
  expect(rateText(300, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~2.5 時')
})
test('ingestRateText：rpm null → 計算中', () => {
  expect(ingestRateText(null, 1)).toBe('速率 計算中…')
})
test('ingestRateText：cps null → 只速率', () => {
  expect(ingestRateText(3, null)).toBe('速率 3.0 篇/分')
})
test('ingestRateText：雙段', () => {
  expect(ingestRateText(3, 1.5)).toBe('速率 3.0 篇/分 · 1.5 片段/秒')
})
test('computeRates：暖機<8s → 全 null', () => {
  expect(computeRates({ reports: 10, chunks: 100, sumDone: 5, tagDone: 3 }, B, 4)).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})
test('computeRates：正常算速率', () => {
  const r = computeRates({ reports: 60, chunks: 600, sumDone: 30, tagDone: 20 }, B, 60)
  expect(r.rpm).toBeCloseTo(60)
  expect(r.cps).toBeCloseTo(10)
  expect(r.spm).toBeCloseTo(30)
  expect(r.tpm).toBeCloseTo(20)
})
test('computeRates：sumDone/tagDone null → 對應 null', () => {
  const r = computeRates({ reports: 60, chunks: 600, sumDone: null, tagDone: null }, B, 60)
  expect(r.spm).toBeNull()
  expect(r.tpm).toBeNull()
})
test('fmtInt：千分位', () => {
  expect(fmtInt(1234567)).toBe('1,234,567')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/rate.test.ts`（Expected: FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/rate.ts`

```ts
export interface Rates {
  rpm: number | null
  cps: number | null
  spm: number | null
  tpm: number | null
}
export interface RateInputs {
  reports: number
  chunks: number
  sumDone: number | null
  tagDone: number | null
}
export interface Baseline {
  reports: number
  chunks: number
  sum: number
  tag: number
}

const NULL_RATES: Rates = { rpm: null, cps: null, spm: null, tpm: null }

/**
 * 開頁基準法（移植 vanilla rate()）：baseline 於首筆由呼叫端捕獲，本函式只做
 * 「開頁至今平均速率」的算術 + 8 秒暖機。elapsedSec<8 → 全 null（暖機期間
 * vanilla 回 lastRate，因該期尚無非 null 值故等價）。
 */
export function computeRates(cur: RateInputs, base: Baseline, elapsedSec: number): Rates {
  if (elapsedSec < 8) return NULL_RATES
  return {
    rpm: ((cur.reports - base.reports) / elapsedSec) * 60,
    cps: (cur.chunks - base.chunks) / elapsedSec,
    spm: cur.sumDone == null ? null : ((cur.sumDone - base.sum) / elapsedSec) * 60,
    tpm: cur.tagDone == null ? null : ((cur.tagDone - base.tag) / elapsedSec) * 60,
  }
}

/** 摘要/標註面板速率行（移植 eta.js）。remaining＝剩餘數，unit 例「摘要」「標註」。 */
export function rateText(remaining: number, rate: number | null, unit: string): string {
  if (rate == null) return '速率 計算中…'
  const line = `速率 ${rate.toFixed(1)} ${unit}/分`
  if (rate < 0.05) return line
  if (remaining <= 0) return `${line} · 已完成`
  const mins = remaining / rate
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`
  return `${line} · 預估剩餘 ${eta}`
}

/** 導入面板速率行（移植 eta.js）：無已知總量、不給 ETA，第二段放每秒片段數。 */
export function ingestRateText(rpm: number | null, cps: number | null): string {
  if (rpm == null) return '速率 計算中…'
  const line = `速率 ${rpm.toFixed(1)} 篇/分`
  return cps == null ? line : `${line} · ${cps.toFixed(1)} 片段/秒`
}

/** 千分位整數格式（穩定用 en-US 逗號）。 */
export function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/rate.test.ts`（PASS）；`tsc --noEmit` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/rate.ts frontend/src/features/monitor/rate.test.ts
git commit -m "$(cat <<'EOF'
feat(監控): rate — 速率/ETA 純函式（移植 eta.js + 開頁基準法）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: useClock.ts（本地 1 秒時鐘）

**Files:**
- Create: `frontend/src/features/monitor/useClock.ts`
- Test: `frontend/src/features/monitor/useClock.test.ts`

**Interfaces:**
- Produces: `useClock(): string`（回傳 `HH:MM:SS`，每秒更新）

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/useClock.test.ts`

```ts
import { afterEach, expect, test, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useClock } from './useClock'

afterEach(() => vi.useRealTimers())

test('回傳 HH:MM:SS 並每秒更新', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 6, 5, 9, 8, 7))
  const { result } = renderHook(() => useClock())
  expect(result.current).toBe('09:08:07')
  act(() => {
    vi.setSystemTime(new Date(2026, 6, 5, 9, 8, 8))
    vi.advanceTimersByTime(1000)
  })
  expect(result.current).toBe('09:08:08')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/useClock.test.ts`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/useClock.ts`

```ts
import { useEffect, useState } from 'react'

function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 本地牆鐘（HH:MM:SS），每秒更新；不吃任何請求。 */
export function useClock(): string {
  const [now, setNow] = useState(() => fmt(new Date()))
  useEffect(() => {
    const id = setInterval(() => setNow(fmt(new Date())), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/useClock.test.ts`（PASS）；`tsc --noEmit` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/useClock.ts frontend/src/features/monitor/useClock.test.ts
git commit -m "$(cat <<'EOF'
feat(監控): useClock — 本地 1s 即時時鐘

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: useProgress.ts（react-query 5s 輪詢）

**Files:**
- Create: `frontend/src/features/monitor/useProgress.ts`
- Test: `frontend/src/features/monitor/useProgress.test.tsx`

**Interfaces:**
- Consumes: `getJSON`（`src/lib/api.ts`）、`progressSchema`/`Progress`（Task 1）。
- Produces: `useProgress(): UseQueryResult<Progress>`（`queryKey ['progress']`、`refetchInterval 5000`、`placeholderData keepPreviousData`）。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/useProgress.test.tsx`

```tsx
import { afterEach, expect, test, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useProgress } from './useProgress'

const fixture = {
  ts: '12:00:00',
  db: { reports: 100, chunks: 5000, markets: [] },
  summary: { done: 90, total: 100, remaining: 10, pct: 90 },
  tagging: null, ingest: null,
  pipelines: { web: true, ingest: false, tag: false, summaries: false },
  orchestrator: null,
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
afterEach(() => vi.restoreAllMocks())

test('抓取並解析 /api/progress', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => fixture })))
  const { result } = renderHook(() => useProgress(), { wrapper: wrapper() })
  await waitFor(() => expect(result.current.data).toBeDefined())
  expect(result.current.data?.db.reports).toBe(100)
})

test('抓取失敗 → isError（retry:false）', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 500, ok: false, json: async () => ({}) })))
  const { result } = renderHook(() => useProgress(), { wrapper: wrapper() })
  await waitFor(() => expect(result.current.isError).toBe(true))
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/useProgress.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/useProgress.ts`

```ts
import { useQuery, keepPreviousData, type UseQueryResult } from '@tanstack/react-query'
import { getJSON } from '../../lib/api'
import { progressSchema, type Progress } from './progressSchema'

/** 每 5 秒輪詢 /api/progress；keepPreviousData 於重抓/失敗時保留上一筆，避免閃爍。 */
export function useProgress(): UseQueryResult<Progress> {
  return useQuery<Progress>({
    queryKey: ['progress'],
    queryFn: () => getJSON('/api/progress', progressSchema, { cache: 'no-store' }),
    refetchInterval: 5000,
    placeholderData: keepPreviousData,
  })
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/useProgress.test.tsx`（PASS）；`tsc --noEmit` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/useProgress.ts frontend/src/features/monitor/useProgress.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): useProgress — react-query 5s 輪詢 /api/progress

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: useRates.ts（baseline ref + computeRates）

**Files:**
- Create: `frontend/src/features/monitor/useRates.ts`
- Test: `frontend/src/features/monitor/useRates.test.tsx`

**Interfaces:**
- Consumes: `computeRates`/`Rates`/`Baseline`/`RateInputs`（Task 2）、`Progress`（Task 1）。
- Produces: `useRates(progress: Progress | undefined): Rates`（首筆捕獲 baseline+t0 回全 null；其後回開頁至今速率）。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/useRates.test.tsx`

```tsx
import { afterEach, expect, test, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRates } from './useRates'
import type { Progress } from './progressSchema'

function mk(reports: number, tagDone: number): Progress {
  return {
    ts: '', db: { reports, chunks: reports * 10, markets: [] },
    summary: { done: 0, total: 10, remaining: 10, pct: 0 },
    tagging: { done: tagDone, total: 100, fail: 100 - tagDone, pct: tagDone },
    ingest: null,
    pipelines: { web: true, ingest: false, tag: true, summaries: false },
    orchestrator: null,
  }
}

afterEach(() => vi.restoreAllMocks())

test('首筆 → 全 null（記 baseline）', () => {
  const { result } = renderHook(({ p }) => useRates(p), { initialProps: { p: mk(10, 5) as Progress | undefined } })
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})

test('第二筆且經過 >8s → 算開頁至今速率', () => {
  let t = 1_000_000
  vi.spyOn(Date, 'now').mockImplementation(() => t)
  const { result, rerender } = renderHook(({ p }) => useRates(p), { initialProps: { p: mk(0, 0) as Progress | undefined } })
  expect(result.current.tpm).toBeNull()
  t += 60_000 // +60s
  rerender({ p: mk(60, 60) })
  expect(result.current.rpm).toBeCloseTo(60)
  expect(result.current.tpm).toBeCloseTo(60)
})

test('progress undefined → 全 null', () => {
  const { result } = renderHook(() => useRates(undefined))
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/useRates.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/useRates.ts`

```ts
import { useRef } from 'react'
import { computeRates, type Rates, type Baseline, type RateInputs } from './rate'
import type { Progress } from './progressSchema'

const NULL_RATES: Rates = { rpm: null, cps: null, spm: null, tpm: null }

/**
 * 開頁基準法：首次拿到 progress 時捕獲 baseline + t0（ref，掛載後不變＝「本次開頁」），
 * 其後每次回開頁至今平均速率。導覽離開再回來 → 元件重掛 → ref 重置 → 基準重來。
 */
export function useRates(progress: Progress | undefined): Rates {
  const base = useRef<{ b: Baseline; t: number } | null>(null)
  if (!progress) return NULL_RATES

  const cur: RateInputs = {
    reports: progress.db.reports,
    chunks: progress.db.chunks,
    sumDone: progress.summary?.done ?? null,
    tagDone: progress.tagging?.done ?? null,
  }
  const now = Date.now()
  if (!base.current) {
    base.current = {
      b: { reports: cur.reports, chunks: cur.chunks, sum: cur.sumDone ?? 0, tag: cur.tagDone ?? 0 },
      t: now,
    }
    return NULL_RATES
  }
  return computeRates(cur, base.current.b, (now - base.current.t) / 1000)
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/useRates.test.tsx`（PASS）；`tsc --noEmit` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/useRates.ts frontend/src/features/monitor/useRates.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): useRates — 開頁基準法速率（baseline ref + computeRates）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: MonitorPage.module.css + KpiCard + KpiGrid

**Files:**
- Create: `frontend/src/features/monitor/MonitorPage.module.css`（**完整全頁樣式，一次建立**）
- Create: `frontend/src/features/monitor/KpiCard.tsx`
- Create: `frontend/src/features/monitor/KpiGrid.tsx`
- Test: `frontend/src/features/monitor/KpiGrid.test.tsx`

**Interfaces:**
- Consumes: `Progress`（Task 1）、`fmtInt`（Task 2）。
- Produces:
  - `KpiCard({ label: string; value: string; suffix?: string; sub?: string })`
  - `KpiGrid({ progress: Progress })`
  - CSS module（供 Task 7–11 共用 import）。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/KpiGrid.test.tsx`

```tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiGrid } from './KpiGrid'
import type { Progress } from './progressSchema'

function mk(over: Partial<Progress> = {}): Progress {
  return {
    ts: '', db: { reports: 1234, chunks: 56789, markets: [{ market: 'TW', count: 5 }, { market: 'US', count: 3 }] },
    summary: { done: 90, total: 100, remaining: 10, pct: 90 },
    tagging: { done: 80, total: 100, fail: 20, pct: 80 },
    ingest: null, pipelines: { web: true, ingest: false, tag: true, summaries: false }, orchestrator: null,
    ...over,
  }
}

test('4 卡值與情境副字', () => {
  render(<KpiGrid progress={mk()} />)
  expect(screen.getByText('1,234')).toBeInTheDocument()
  expect(screen.getByText('2 個市場')).toBeInTheDocument()
  expect(screen.getByText('向量片段總數')).toBeInTheDocument()
  expect(screen.getByText('已標註 80 / 100')).toBeInTheDocument()
  expect(screen.getByText('已生成 90 / 100')).toBeInTheDocument()
  expect(screen.getByText('80.00')).toBeInTheDocument()
  expect(screen.getByText('90.00')).toBeInTheDocument()
})

test('tagging null → 標註卡顯 —、無副字', () => {
  render(<KpiGrid progress={mk({ tagging: null })} />)
  expect(screen.getByText('—')).toBeInTheDocument()
  expect(screen.queryByText(/已標註/)).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/KpiGrid.test.tsx`（FAIL）

- [ ] **Step 3a: 建立完整 CSS module** — `frontend/src/features/monitor/MonitorPage.module.css`

```css
/* 監控頁樣式，逐值對齊 .dc.html MONITOR 畫面（第 389–460 行）。Task 6–11 共用此檔。 */
.page { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.scroll { flex: 1; overflow-y: auto; padding: 28px 32px; }
.inner { max-width: 1120px; margin: 0 auto; }

/* 頁首 */
.header { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 20px; }
.title { font-family: 'Noto Serif TC', Georgia, serif; font-weight: 700; font-size: 26px; color: #101828; margin: 0 0 6px; }
.sub { font-size: 13px; color: #667085; font-variant-numeric: tabular-nums; }
.headRight { display: flex; align-items: center; gap: 12px; }
.live { display: inline-flex; align-items: center; gap: 6px; background: #faf3e3; color: #8a5a0f; border-radius: 999px; font-size: 11.5px; font-weight: 700; padding: 4px 11px; }
.liveDot { width: 7px; height: 7px; border-radius: 999px; background: #34c759; animation: tf-pulse 1.6s ease-in-out infinite; }
.stale { background: #fef3f2; color: #b42318; }
.staleDot { background: #f04438; animation: none; }
.clock { font-family: ui-monospace, Menlo, monospace; font-size: 13px; color: #667085; font-variant-numeric: tabular-nums; }

/* 卡片共用 */
.card { background: #fff; border: 1px solid #e4e7ec; border-radius: 12px; box-shadow: 0 1px 3px rgba(16, 24, 40, .06); }

/* KPI 網格 */
.kpiGrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kpiCard { padding: 16px 18px; }
.kpiLabel { font-size: 12px; color: #667085; margin-bottom: 8px; }
.kpiValRow { display: flex; align-items: baseline; gap: 4px; }
.kpiVal { font-size: 26px; font-weight: 700; color: #101828; line-height: 1.1; font-variant-numeric: tabular-nums; }
.kpiSuffix { font-size: 13px; color: #98a2b3; }
.kpiSub { font-size: 12px; color: #667085; margin-top: 8px; }

/* 面板 */
.panelGrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px; }
.panel { padding: 18px; }
.ptitle { font-family: 'Noto Serif TC', Georgia, serif; font-weight: 700; font-size: 16px; color: #101828; margin-bottom: 14px; }
.pmain { display: flex; align-items: baseline; gap: 6px; margin-bottom: 12px; }
.big { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; color: #101828; line-height: 1; }
.pof { font-size: 14px; color: #98a2b3; }
.ppct { margin-left: auto; font-size: 13px; font-weight: 600; color: #8a5a0f; font-variant-numeric: tabular-nums; }
.bar { height: 9px; border-radius: 999px; background: #f2f4f7; overflow: hidden; }
.barFill { height: 100%; border-radius: 999px; background: #8a5a0f; transition: width .34s cubic-bezier(.22, 1, .36, 1); }
.prate { font-size: 12.5px; color: #667085; margin-top: 10px; }
.pidle { font-size: 13px; color: #98a2b3; margin: 6px 0 12px; }

/* 報告導入 */
.ingCurrent { font-size: 13px; color: #344054; margin-bottom: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.indetWrap { position: relative; height: 9px; border-radius: 999px; background: #f2f4f7; overflow: hidden; }
.indetBar { position: absolute; top: 0; height: 100%; width: 34%; border-radius: 999px; background: #8a5a0f; animation: tf-indet 1.4s ease-in-out infinite; }
.indetIdle { position: absolute; top: 0; left: 0; height: 100%; width: 100%; border-radius: 999px; background: #e4e7ec; }

/* 處理管線 */
.pipeList { display: flex; flex-direction: column; gap: 13px; margin-top: 14px; }
.pipeRow { display: flex; align-items: center; gap: 10px; }
.pipeDot { width: 9px; height: 9px; border-radius: 999px; flex: none; }
.pipeOn { background: #34c759; }
.pipeOff { background: #d0d5dd; }
.pipeName { font-size: 13.5px; color: #344054; flex: 1; }
.pipeBadge { font-size: 11.5px; font-weight: 600; border-radius: 999px; padding: 2px 9px; }
.badgeOn { background: #ecfdf3; color: #027a48; }
.badgeOff { background: #f2f4f7; color: #667085; }

/* 市場分佈 */
.mktList { display: flex; flex-direction: column; gap: 11px; margin-top: 16px; }
.mktRow { display: flex; align-items: center; gap: 12px; }
.mktLabel { width: 52px; font-size: 12.5px; color: #344054; flex: none; }
.mktBar { flex: 1; height: 8px; border-radius: 999px; background: #f2f4f7; overflow: hidden; }
.mktFill { height: 100%; border-radius: 999px; }
.mktCount { width: 66px; text-align: right; font-size: 12px; color: #667085; font-variant-numeric: tabular-nums; flex: none; }
.mktPct { width: 38px; text-align: right; font-size: 12px; color: #98a2b3; flex: none; }

/* 頁尾 + 工具 */
.footer { text-align: center; font-size: 11.5px; color: #98a2b3; margin-top: 22px; }
.marginTop { margin-top: 12px; }

@media (max-width: 900px) {
  .kpiGrid { grid-template-columns: repeat(2, 1fr); }
  .panelGrid { grid-template-columns: 1fr; }
  .scroll { padding: 20px 16px; }
}
```

- [ ] **Step 3b: 實作 KpiCard** — `frontend/src/features/monitor/KpiCard.tsx`

```tsx
import styles from './MonitorPage.module.css'

export function KpiCard({ label, value, suffix, sub }: {
  label: string
  value: string
  suffix?: string
  sub?: string
}) {
  return (
    <div className={`${styles.card} ${styles.kpiCard}`}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiValRow}>
        <span className={styles.kpiVal}>{value}</span>
        {suffix ? <span className={styles.kpiSuffix}>{suffix}</span> : null}
      </div>
      {sub ? <div className={styles.kpiSub}>{sub}</div> : null}
    </div>
  )
}
```

- [ ] **Step 3c: 實作 KpiGrid** — `frontend/src/features/monitor/KpiGrid.tsx`

```tsx
import styles from './MonitorPage.module.css'
import { KpiCard } from './KpiCard'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'

export function KpiGrid({ progress }: { progress: Progress }) {
  const { db, tagging, summary } = progress
  return (
    <div className={styles.kpiGrid}>
      <KpiCard label="已導入報告" value={fmtInt(db.reports)} sub={`${db.markets.length} 個市場`} />
      <KpiCard label="總片段 CHUNKS" value={fmtInt(db.chunks)} sub="向量片段總數" />
      <KpiCard
        label="標註進度"
        value={tagging ? tagging.pct.toFixed(2) : '—'}
        suffix="%"
        sub={tagging ? `已標註 ${fmtInt(tagging.done)} / ${fmtInt(tagging.total)}` : undefined}
      />
      <KpiCard
        label="摘要進度"
        value={summary.pct.toFixed(2)}
        suffix="%"
        sub={`已生成 ${fmtInt(summary.done)} / ${fmtInt(summary.total)}`}
      />
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/KpiGrid.test.tsx`（PASS）；`tsc --noEmit`、`eslint src/features/monitor/` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/MonitorPage.module.css frontend/src/features/monitor/KpiCard.tsx frontend/src/features/monitor/KpiGrid.tsx frontend/src/features/monitor/KpiGrid.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): 全頁 CSS module + KPI 4 卡

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: ProgressPanel（語意標註 / 摘要生成共用）

**Files:**
- Create: `frontend/src/features/monitor/ProgressPanel.tsx`
- Test: `frontend/src/features/monitor/ProgressPanel.test.tsx`

**Interfaces:**
- Consumes: `fmtInt`（Task 2）、CSS module（Task 6）。
- Produces: `ProgressPanel({ title: string; data: { done: number; total: number; pct: number } | null; rateLine: string; idleText: string })`。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/ProgressPanel.test.tsx`

```tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProgressPanel } from './ProgressPanel'

test('有資料：done/total/pct + 速率行 + 進度條寬', () => {
  const { container } = render(
    <ProgressPanel title="語意標註" data={{ done: 80, total: 100, pct: 80 }} rateLine="速率 2.0 標註/分 · 預估剩餘 ~10 分" idleText="目前無執行中的標註" />,
  )
  expect(screen.getByText('語意標註')).toBeInTheDocument()
  expect(screen.getByText('80')).toBeInTheDocument()
  expect(screen.getByText('/ 100 篇')).toBeInTheDocument()
  expect(screen.getByText('80.00%')).toBeInTheDocument()
  expect(screen.getByText('速率 2.0 標註/分 · 預估剩餘 ~10 分')).toBeInTheDocument()
  const fill = container.querySelector('[class*="barFill"]') as HTMLElement
  expect(fill.style.width).toBe('80%')
})

test('data null → 閒置態，不顯進度條', () => {
  const { container } = render(
    <ProgressPanel title="語意標註" data={null} rateLine="" idleText="目前無執行中的標註" />,
  )
  expect(screen.getByText('目前無執行中的標註')).toBeInTheDocument()
  expect(container.querySelector('[class*="barFill"]')).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/ProgressPanel.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/ProgressPanel.tsx`

```tsx
import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'

export function ProgressPanel({ title, data, rateLine, idleText }: {
  title: string
  data: { done: number; total: number; pct: number } | null
  rateLine: string
  idleText: string
}) {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>{title}</div>
      {data ? (
        <>
          <div className={styles.pmain}>
            <span className={styles.big}>{fmtInt(data.done)}</span>
            <span className={styles.pof}>/ {fmtInt(data.total)} 篇</span>
            <span className={styles.ppct}>{data.pct.toFixed(2)}%</span>
          </div>
          <div className={styles.bar}><div className={styles.barFill} style={{ width: `${data.pct}%` }} /></div>
          <div className={styles.prate}>{rateLine}</div>
        </>
      ) : (
        <div className={styles.pidle}>{idleText}</div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/ProgressPanel.test.tsx`（PASS）；`tsc`/`eslint` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/ProgressPanel.tsx frontend/src/features/monitor/ProgressPanel.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): ProgressPanel — 語意標註/摘要生成共用面板

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: IngestPanel（報告導入）

**Files:**
- Create: `frontend/src/features/monitor/IngestPanel.tsx`
- Test: `frontend/src/features/monitor/IngestPanel.test.tsx`

**Interfaces:**
- Consumes: `Progress`（Task 1）、`fmtInt`（Task 2）、CSS module（Task 6）。
- Produces: `IngestPanel({ progress: Progress; rateLine: string })`。current 文字 fallback：`orchestrator.label` → `本輪已導入 N 篇 · 失敗 M` → `目前無執行中的導入`；`pipelines.ingest` true→`tf-indet` 不定量條、false→靜止灰條。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/IngestPanel.test.tsx`

```tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { IngestPanel } from './IngestPanel'
import type { Progress } from './progressSchema'

function mk(over: Partial<Progress> = {}): Progress {
  return {
    ts: '', db: { reports: 0, chunks: 0, markets: [] },
    summary: { done: 0, total: 0, remaining: 0, pct: 0 },
    tagging: null, ingest: null,
    pipelines: { web: true, ingest: false, tag: false, summaries: false },
    orchestrator: null, ...over,
  }
}

test('orchestrator.label 優先', () => {
  render(<IngestPanel progress={mk({ orchestrator: { raw: '', timestamp: null, status: 'running', label: '編排器執行中' } })} rateLine="速率 計算中…" />)
  expect(screen.getByText('編排器執行中')).toBeInTheDocument()
})

test('無 orchestrator 有 ingest → 本輪已導入', () => {
  render(<IngestPanel progress={mk({ ingest: { ingested: 42, chunks: 100, fail: 1 } })} rateLine="" />)
  expect(screen.getByText('本輪已導入 42 篇 · 失敗 1')).toBeInTheDocument()
})

test('皆無 → 目前無執行中的導入', () => {
  render(<IngestPanel progress={mk()} rateLine="" />)
  expect(screen.getByText('目前無執行中的導入')).toBeInTheDocument()
})

test('pipelines.ingest true → 不定量條；false → 靜止條', () => {
  const { container: on } = render(<IngestPanel progress={mk({ pipelines: { web: true, ingest: true, tag: false, summaries: false } })} rateLine="" />)
  expect(on.querySelector('[class*="indetBar"]')).not.toBeNull()
  const { container: off } = render(<IngestPanel progress={mk()} rateLine="" />)
  expect(off.querySelector('[class*="indetBar"]')).toBeNull()
  expect(off.querySelector('[class*="indetIdle"]')).not.toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/IngestPanel.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/IngestPanel.tsx`

```tsx
import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'

export function IngestPanel({ progress, rateLine }: { progress: Progress; rateLine: string }) {
  const { ingest, orchestrator, pipelines } = progress
  const active = pipelines.ingest
  const current =
    orchestrator?.label ??
    (ingest ? `本輪已導入 ${fmtInt(ingest.ingested)} 篇 · 失敗 ${fmtInt(ingest.fail)}` : '目前無執行中的導入')
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>報告導入</div>
      <div className={styles.ingCurrent}>{current}</div>
      <div className={styles.indetWrap}>
        {active ? <div className={styles.indetBar} /> : <div className={styles.indetIdle} />}
      </div>
      <div className={styles.prate}>{rateLine}</div>
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/IngestPanel.test.tsx`（PASS）；`tsc`/`eslint` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/IngestPanel.tsx frontend/src/features/monitor/IngestPanel.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): IngestPanel — 報告導入（不定量條 + 狀態 fallback）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: PipelineStatus（4 管線狀態）

**Files:**
- Create: `frontend/src/features/monitor/PipelineStatus.tsx`
- Test: `frontend/src/features/monitor/PipelineStatus.test.tsx`

**Interfaces:**
- Consumes: `Pipelines`（Task 1）、CSS module（Task 6）。
- Produces: `PipelineStatus({ pipelines: Pipelines })`。4 列 = Web 服務(`web`)/報告導入(`ingest`)/語意標註(`tag`)/摘要生成(`summaries`)，狀態徽章「執行中/已停止」。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/PipelineStatus.test.tsx`

```tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineStatus } from './PipelineStatus'

test('4 列名稱 + 狀態徽章對應布林', () => {
  render(<PipelineStatus pipelines={{ web: true, ingest: false, tag: true, summaries: false }} />)
  for (const n of ['Web 服務', '報告導入', '語意標註', '摘要生成']) {
    expect(screen.getByText(n)).toBeInTheDocument()
  }
  expect(screen.getAllByText('執行中')).toHaveLength(2)   // web + tag
  expect(screen.getAllByText('已停止')).toHaveLength(2)   // ingest + summaries
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/PipelineStatus.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/PipelineStatus.tsx`

```tsx
import styles from './MonitorPage.module.css'
import type { Pipelines } from './progressSchema'

const ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', name: 'Web 服務' },
  { key: 'ingest', name: '報告導入' },
  { key: 'tag', name: '語意標註' },
  { key: 'summaries', name: '摘要生成' },
]

export function PipelineStatus({ pipelines }: { pipelines: Pipelines }) {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>處理管線</div>
      <div className={styles.pipeList}>
        {ROWS.map(r => {
          const on = pipelines[r.key]
          return (
            <div key={r.key} className={styles.pipeRow}>
              <span className={`${styles.pipeDot} ${on ? styles.pipeOn : styles.pipeOff}`} />
              <span className={styles.pipeName}>{r.name}</span>
              <span className={`${styles.pipeBadge} ${on ? styles.badgeOn : styles.badgeOff}`}>{on ? '執行中' : '已停止'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/PipelineStatus.test.tsx`（PASS）；`tsc`/`eslint` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/PipelineStatus.tsx frontend/src/features/monitor/PipelineStatus.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): PipelineStatus — 4 條處理管線狀態

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: MarketDistribution（市場分佈）

**Files:**
- Create: `frontend/src/features/monitor/MarketDistribution.tsx`
- Test: `frontend/src/features/monitor/MarketDistribution.test.tsx`

**Interfaces:**
- Consumes: `MarketCount`（Task 1）、`fmtInt`（Task 2）、`marketColor`/`marketLabel`（`src/lib/meta.ts`）、CSS module（Task 6）。
- Produces: `MarketDistribution({ markets: MarketCount[] })`。依 count 由大到小排序；長條寬 = count/max×100%、色 = marketColor；佔比 = count/total×100%（四捨五入整數）。空 → 「—」。`market` 為 null 時標籤/色 fallback「—」/灰。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/MarketDistribution.test.tsx`

```tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarketDistribution } from './MarketDistribution'
import { marketLabel } from '../../lib/meta'

test('依 count 由大到小、寬度與佔比', () => {
  const { container } = render(<MarketDistribution markets={[{ market: 'US', count: 20 }, { market: 'TW', count: 80 }]} />)
  const rows = [...container.querySelectorAll('[class*="mktRow"]')]
  // 第一列應為 TW（count 大）
  expect(rows[0].textContent).toContain(marketLabel('TW'))
  const fill = rows[0].querySelector('[class*="mktFill"]') as HTMLElement
  expect(fill.style.width).toBe('100.0%')   // 80/80
  expect(screen.getByText('80')).toBeInTheDocument()
  expect(screen.getByText('80%')).toBeInTheDocument()   // 80/100
})

test('空 markets → 顯 —', () => {
  render(<MarketDistribution markets={[]} />)
  expect(screen.getByText('—')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/MarketDistribution.test.tsx`（FAIL）

- [ ] **Step 3: 實作** — `frontend/src/features/monitor/MarketDistribution.tsx`

```tsx
import styles from './MonitorPage.module.css'
import { marketColor, marketLabel } from '../../lib/meta'
import { fmtInt } from './rate'
import type { MarketCount } from './progressSchema'

export function MarketDistribution({ markets }: { markets: MarketCount[] }) {
  const rows = [...markets].sort((a, b) => b.count - a.count)
  const total = rows.reduce((s, m) => s + m.count, 0)
  const max = Math.max(1, ...rows.map(m => m.count))
  return (
    <div className={`${styles.card} ${styles.panel} ${styles.marginTop}`}>
      <div className={styles.ptitle}>市場分佈</div>
      <div className={styles.mktList}>
        {rows.length === 0 ? (
          <div className={styles.mktRow}><span className={styles.mktLabel}>—</span></div>
        ) : (
          rows.map(m => {
            const code = m.market ?? '—'
            const label = m.market ? marketLabel(m.market) : '—'
            const color = m.market ? marketColor(m.market) : '#98a2b3'
            const pct = total ? (m.count / total) * 100 : 0
            return (
              <div key={code} className={styles.mktRow}>
                <span className={styles.mktLabel}>{label}</span>
                <div className={styles.mktBar}>
                  <div className={styles.mktFill} style={{ width: `${((m.count / max) * 100).toFixed(1)}%`, background: color }} />
                </div>
                <span className={styles.mktCount}>{fmtInt(m.count)}</span>
                <span className={styles.mktPct}>{pct.toFixed(0)}%</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過** — `./node_modules/.bin/vitest run src/features/monitor/MarketDistribution.test.tsx`（PASS）；`tsc`/`eslint` clean。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/MarketDistribution.tsx frontend/src/features/monitor/MarketDistribution.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): MarketDistribution — 市場分佈長條（meta 色票）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: MonitorPage（組合，取代 stub）

**Files:**
- Modify（取代）: `frontend/src/features/monitor/MonitorPage.tsx`（現為 3 行 stub）
- Test: `frontend/src/features/monitor/MonitorPage.test.tsx`

**Interfaces:**
- Consumes: `useProgress`(4)、`useClock`(3)、`useRates`(5)、`rateText`/`ingestRateText`(2)、`KpiGrid`(6)、`ProgressPanel`(7)、`IngestPanel`(8)、`PipelineStatus`(9)、`MarketDistribution`(10)、CSS module(6)。
- Produces: `export default function MonitorPage()`（App.tsx 已 `lazy` import default）。

- [ ] **Step 1: 寫失敗測試** — `frontend/src/features/monitor/MonitorPage.test.tsx`

```tsx
import { afterEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import MonitorPage from './MonitorPage'

const fixture = {
  ts: '12:00:00',
  db: { reports: 1000, chunks: 50000, markets: [{ market: 'TW', count: 600 }, { market: 'US', count: 400 }] },
  summary: { done: 900, total: 1000, remaining: 100, pct: 90 },
  tagging: { done: 800, total: 1000, fail: 200, pct: 80 },
  ingest: { ingested: 5, chunks: 300, fail: 0 },
  pipelines: { web: true, ingest: true, tag: true, summaries: false },
  orchestrator: null,
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>
}
afterEach(() => vi.restoreAllMocks())

test('成功輪詢 → 頁首副字 + LIVE + 面板', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => fixture })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('1,000 篇已導入 · 3/4 條管線執行中')).toBeInTheDocument())
  expect(screen.getByText('LIVE')).toBeInTheDocument()
  expect(screen.getByText('研報導入監控')).toBeInTheDocument()
  expect(screen.getByText('處理管線')).toBeInTheDocument()
  expect(screen.getByText('市場分佈')).toBeInTheDocument()
  expect(screen.getByText('已導入報告')).toBeInTheDocument()   // KPI label（唯一）
  expect(screen.getByText('Web 服務')).toBeInTheDocument()     // 管線列（唯一）
  // 「語意標註」「摘要生成」各出現於「面板標題」+「管線列名稱」共 2 處 → 用 getAllByText
  expect(screen.getAllByText('語意標註').length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('摘要生成').length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('資料每 5 秒自動更新 · 廷豐智能研報導入管線')).toBeInTheDocument()
})

test('首抓失敗 → LIVE 顯「重連中」', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 500, ok: false, json: async () => ({}) })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('重連中')).toBeInTheDocument())
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/monitor/MonitorPage.test.tsx`（FAIL，stub 尚未組合）

- [ ] **Step 3: 實作（取代 stub）** — `frontend/src/features/monitor/MonitorPage.tsx`

```tsx
import styles from './MonitorPage.module.css'
import { useProgress } from './useProgress'
import { useClock } from './useClock'
import { useRates } from './useRates'
import { rateText, ingestRateText, fmtInt } from './rate'
import { KpiGrid } from './KpiGrid'
import { ProgressPanel } from './ProgressPanel'
import { IngestPanel } from './IngestPanel'
import { PipelineStatus } from './PipelineStatus'
import { MarketDistribution } from './MarketDistribution'

export default function MonitorPage() {
  const q = useProgress()
  const clock = useClock()
  const rates = useRates(q.data)
  const p = q.data
  const live = !q.isError

  const alive = p ? [p.pipelines.web, p.pipelines.ingest, p.pipelines.tag, p.pipelines.summaries].filter(Boolean).length : 0

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.inner}>
          <div className={styles.header}>
            <div>
              <h2 className={styles.title}>研報導入監控</h2>
              <div className={styles.sub}>
                {p ? `${fmtInt(p.db.reports)} 篇已導入 · ${alive}/4 條管線執行中` : '連線中…'}
              </div>
            </div>
            <div className={styles.headRight}>
              <span className={`${styles.live} ${live ? '' : styles.stale}`}>
                <span className={`${styles.liveDot} ${live ? '' : styles.staleDot}`} />
                {live ? 'LIVE' : '重連中'}
              </span>
              <span className={styles.clock}>{clock}</span>
            </div>
          </div>

          {p ? (
            <>
              <KpiGrid progress={p} />
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="語意標註"
                  data={p.tagging}
                  rateLine={rateText(p.tagging?.fail ?? 0, rates.tpm, '標註')}
                  idleText="目前無執行中的標註"
                />
                <IngestPanel progress={p} rateLine={ingestRateText(rates.rpm, rates.cps)} />
              </div>
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="摘要生成"
                  data={p.summary}
                  rateLine={rateText(p.summary.remaining, rates.spm, '摘要')}
                  idleText="—"
                />
                <PipelineStatus pipelines={p.pipelines} />
              </div>
              <MarketDistribution markets={p.db.markets} />
              <div className={styles.footer}>資料每 5 秒自動更新 · 廷豐智能研報導入管線</div>
            </>
          ) : (
            <div className={styles.footer}>{q.isError ? '連線失敗，重試中…' : '載入中…'}</div>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過 + 整體收斂** —
  - `./node_modules/.bin/vitest run src/features/monitor/`（全綠）
  - `./node_modules/.bin/tsc --noEmit`（clean）
  - `./node_modules/.bin/eslint src/features/monitor/`（clean）
  - `./node_modules/.bin/vite build`（成功；確認 App.tsx lazy import 正常）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/monitor/MonitorPage.tsx frontend/src/features/monitor/MonitorPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(監控): MonitorPage 組合頁（取代 stub，5s 輪詢/LIVE/全面板）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: e2e 冒煙 monitor.spec.ts（寫檔 + type-check；live run 延後手動）

**Files:**
- Create: `frontend/e2e/monitor.spec.ts`

**Interfaces:**
- Consumes: 既有 `e2e/` 慣例（`login(page)` pattern、baseURL `http://127.0.0.1:8098`）。

**注意：本任務只寫檔 + type-check/lint，不啟動任何伺服器、不跑 playwright（監控唯讀但仍需後端 :8098）。live run 延後由控制器手動跑（與 Phase 2 同慣例：`--workers=1`、對工作樹 :8098、用後核對埠再 kill）。**

- [ ] **Step 1: 寫 e2e spec** — `frontend/e2e/monitor.spec.ts`

```ts
import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')   // 尚未 cutover
  await page.goto('/app/monitor')
}

test('監控頁：標題 + KPI + LIVE + 處理管線', async ({ page }) => {
  await login(page)
  await expect(page.getByRole('heading', { name: '研報導入監控' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('已導入報告')).toBeVisible()
  await expect(page.getByText('處理管線')).toBeVisible()
  // LIVE 或（暫時斷線）重連中
  await expect(page.getByText(/LIVE|重連中/)).toBeVisible()
  // 至少一條管線列
  await expect(page.getByText('Web 服務')).toBeVisible()
})
```

- [ ] **Step 2: Type-check / lint（不跑 live）** —
  - `./node_modules/.bin/tsc --noEmit`（clean）
  - `./node_modules/.bin/eslint e2e/monitor.spec.ts`（clean）
  - `./node_modules/.bin/playwright test --list e2e/monitor.spec.ts`（僅列出、不執行；確認可被發現、不啟伺服器）

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/monitor.spec.ts
git commit -m "$(cat <<'EOF'
test(監控): e2e 冒煙（標題/KPI/LIVE/管線）— live run 延後手動

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review（已於撰寫後執行）

**1. Spec coverage:**
- 後端零改動 → 全任務只消費 `/api/progress`（Task 4）。✅
- `.dc.html` 逐段（頁首/LIVE/時鐘/KPI/語意標註/報告導入/摘要生成/處理管線/市場分佈/頁尾）→ Task 6–11。✅
- 5s 輪詢 → Task 4；本地 1s 時鐘 → Task 3。✅
- KPI 情境副字（決策 4）→ Task 6 測試斷言「N 個市場/向量片段總數/已標註 x/y/已生成 x/y」。✅
- 速率/ETA 移植（開頁基準法 + eta.js）→ Task 2/5。✅
- LIVE 健康態 + keepPreviousData → Task 4/11。✅
- null tagging/ingest 閒置態 → Task 7/8。✅
- 市場分佈 count desc + marketColor/Label + 空態 + market null 守門 → Task 10。✅
- e2e → Task 12。✅

**2. Placeholder scan:** 無 TBD/TODO；每個 code step 皆完整程式碼。✅

**3. Type consistency:** `progressSchema` 型別（`Progress`/`Tagging`/`Ingest`/`Summary`/`Pipelines`/`MarketCount`/`Orchestrator`）與後續一致；`rate.ts` 的 `computeRates`/`rateText`/`ingestRateText`/`fmtInt` 簽名於各消費點一致；`useRates(progress?)`、`useProgress()`、`useClock()` 回傳型別一致；元件 props 與 MonitorPage 傳入一致（`ProgressPanel` 收 `{done,total,pct}`，`p.summary` 帶額外 `remaining` 屬結構相容）。✅
