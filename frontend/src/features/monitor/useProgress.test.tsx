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
