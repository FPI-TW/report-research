import React from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as api from '../api'
import { useSearchResults } from './useSearchResults'
import { DEFAULT_FILTERS } from '../lib/filters'

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

const BROWSE_ITEM = {
  report_id: 'r1',
  file_name: 'f',
  market: 'TW',
  source: null,
  summary: null,
  report_date: null,
  report_type: null,
  instrument_types: null,
  relates_stock: null,
  relates_futures: null,
  stock_targets: null,
  futures_targets: null,
}

const SEARCH_RESULT = {
  ...BROWSE_ITEM,
  report_id: 'r2',
  rank: 1,
  best_score: 0.9,
  match_count: 3,
  passages: [],
}

afterEach(() => {
  vi.restoreAllMocks()
})

test('browse 模式攤平 items 並算 hasMore', async () => {
  vi.spyOn(api, 'getReports').mockResolvedValue({ total: 80, offset: 0, items: [BROWSE_ITEM] })

  const { result } = renderHook(() => useSearchResults(DEFAULT_FILTERS), {
    wrapper: makeWrapper(),
  })

  await waitFor(() => expect(result.current.rows.length).toBe(1))
  expect(result.current.mode).toBe('browse')
  expect(result.current.total).toBe(80)
  expect(result.current.hasMore).toBe(true) // 1 < 80
  expect(result.current.isError).toBe(false)
  expect(api.getSearch).not.toHaveBeenCalled
})

test('search 模式使用 getSearch 並正規化結果', async () => {
  vi.spyOn(api, 'getSearch').mockResolvedValue({
    query: 'AI',
    market: null,
    total: 5,
    results: [SEARCH_RESULT],
  })

  const searchFilters = { ...DEFAULT_FILTERS, q: 'AI' }
  const { result } = renderHook(() => useSearchResults(searchFilters), {
    wrapper: makeWrapper(),
  })

  await waitFor(() => expect(result.current.rows.length).toBe(1))
  expect(result.current.mode).toBe('search')
  expect(result.current.rows[0].rank).toBe(1)
  expect(result.current.rows[0].bestScore).toBe(0.9)
  expect(result.current.rows[0].matchCount).toBe(3)
  expect(result.current.hasMore).toBe(true) // 1 < 5
  expect(result.current.isError).toBe(false)
})

test('total=0 且 items 空時 hasMore=false', async () => {
  vi.spyOn(api, 'getReports').mockResolvedValue({ total: 0, offset: 0, items: [] })

  const { result } = renderHook(() => useSearchResults(DEFAULT_FILTERS), {
    wrapper: makeWrapper(),
  })

  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.rows).toHaveLength(0)
  expect(result.current.total).toBe(0)
  expect(result.current.hasMore).toBe(false)
})

test('API 錯誤時 isError=true', async () => {
  vi.spyOn(api, 'getReports').mockRejectedValue(new Error('network error'))

  const { result } = renderHook(() => useSearchResults(DEFAULT_FILTERS), {
    wrapper: makeWrapper(),
  })

  await waitFor(() => expect(result.current.isError).toBe(true))
  expect(result.current.rows).toHaveLength(0)
})
