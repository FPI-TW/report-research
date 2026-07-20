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
      report_id: id, file_hash: id.repeat(64).slice(0, 64), file_name: id,
      market: null, source: null, summary: null,
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
