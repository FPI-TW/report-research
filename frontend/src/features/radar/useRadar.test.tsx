import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as radarApi from '../../lib/radarApi'
import type { RadarOverview, Window } from '../../lib/radarSchemas'
import { useInstrumentRadar, useRadarEvents, useRadarInstruments } from './useRadar'

vi.mock('../../lib/radarApi')

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function overview(window: Window, name: string): RadarOverview {
  return {
    market: 'TW', market_display: '台股', instrument_code: '8046', instrument_name: name,
    window, as_of: '2026-07-11',
    coverage: {
      state: 'ok', brokers_total: 1, brokers_extracted: 1,
      brokers_in_consensus: 1, reports_available: 1, note: '',
    },
    rating: null, target_price: null, eps: null, thesis: [], recent_events: [],
    recent_events_total: 0, recent_events_has_more: false,
    recent_events_next_offset: null, brokers: [], notes: [],
  }
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('useRadar', () => {
  it('切換窗期時不把 90 天資料當成 30 天資料', async () => {
    let resolve30!: (value: RadarOverview) => void
    const pending30 = new Promise<RadarOverview>(resolve => { resolve30 = resolve })
    vi.mocked(radarApi.getInstrumentRadar).mockImplementation((_code, _market, window) => (
      window === '90' ? Promise.resolve(overview('90', 'old-90')) : pending30
    ))

    const { result, rerender } = renderHook(
      ({ window }) => useInstrumentRadar('8046', 'TW', window),
      { initialProps: { window: '90' as Window }, wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data?.instrument_name).toBe('old-90'))

    rerender({ window: '30' })
    expect(result.current.data).toBeUndefined()
    expect(result.current.isPending).toBe(true)

    resolve30(overview('30', 'new-30'))
    await waitFor(() => expect(result.current.data?.instrument_name).toBe('new-30'))
  })

  it('切換 query key 會取消尚未完成的舊窗期請求', async () => {
    const signals: AbortSignal[] = []
    vi.mocked(radarApi.getInstrumentRadar).mockImplementation((_code, _market, _window, options) => {
      if (options?.signal) signals.push(options.signal)
      return new Promise(() => undefined)
    })

    const { rerender } = renderHook(
      ({ window }) => useInstrumentRadar('8046', 'TW', window),
      { initialProps: { window: '90' as Window }, wrapper: wrapper() },
    )
    await waitFor(() => expect(signals).toHaveLength(1))

    rerender({ window: '30' })
    await waitFor(() => expect(signals).toHaveLength(2))
    expect(signals[0].aborted).toBe(true)
  })

  it('標的目錄逐頁載入並扁平合併', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockImplementation(async params => {
      const offset = params.offset ?? 0
      const count = offset === 0 ? 50 : 25
      return {
        total: 75, limit: 50, offset,
        has_more: offset === 0, next_offset: offset === 0 ? 50 : null,
        items: Array.from({ length: count }, (_, index) => ({
          market: 'TW' as const, instrument_code: String(offset + index),
          broker_count: 1, report_count: 1, coverage_state: 'ok' as const,
        })),
      }
    })

    const { result } = renderHook(
      () => useRadarInstruments({ market: 'TW' }),
      { wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data?.items).toHaveLength(50))
    expect(result.current.hasMore).toBe(true)

    await act(async () => { await result.current.loadMore() })
    await waitFor(() => expect(result.current.data?.items).toHaveLength(75))
    expect(result.current.hasMore).toBe(false)
    expect(result.current.data?.has_more).toBe(false)
    expect(result.current.data?.next_offset).toBeNull()
    expect(radarApi.getRadarInstruments).toHaveBeenLastCalledWith(
      { market: 'TW', q: undefined, limit: 50, offset: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('事件分頁依 next_offset 載入且不重複', async () => {
    vi.mocked(radarApi.getRadarEvents).mockImplementation(async (_code, params) => {
      const offset = params.offset ?? 0
      const count = offset === 0 ? 12 : 8
      return {
        market: 'TW', instrument_code: '8046', window: '90', as_of: '2026-07-11',
        total: 20, limit: 12, offset, has_more: offset === 0,
        next_offset: offset === 0 ? 12 : null,
        items: Array.from({ length: count }, (_, index) => ({
          broker: `b-${offset + index}`, report_date: '2026-07-11',
          headline: `event-${offset + index}`, changes: [], evidence: ['evidence'],
          report_link: { report_id: `r-${offset + index}` },
        })),
      }
    })

    const { result } = renderHook(
      () => useRadarEvents('8046', 'TW', '90', true),
      { wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data?.items).toHaveLength(12))

    await act(async () => { await result.current.loadMore() })
    await waitFor(() => expect(result.current.data?.items).toHaveLength(20))
    expect(new Set(result.current.data?.items.map(item => item.report_link.report_id)).size).toBe(20)
    expect(result.current.data?.has_more).toBe(false)
    expect(result.current.data?.next_offset).toBeNull()
    expect(radarApi.getRadarEvents).toHaveBeenLastCalledWith(
      '8046',
      { market: 'TW', window: '90', limit: 12, offset: 12 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  /*
   * `sort`／`stance` 沒進 queryKey 的失效方式**完全無聲**：切排序會命中同一筆快取，
   * 畫面不動、network 沒有請求、console 乾淨（staleTime 30s 讓誤判更頑固），
   * 看起來就像後端不支援排序。所以這裡驗的是「切了之後真的換了一批資料」。
   */
  it('切換排序會重新請求並換掉資料，不吃舊排序的快取', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockImplementation(async params => ({
      total: 1, limit: 50, offset: 0, has_more: false, next_offset: null,
      items: [{
        market: 'TW' as const, market_display: '台股',
        instrument_code: params.sort ?? 'latest',
        instrument_name: `依 ${params.sort ?? 'latest'}`,
        broker_count: 1, report_count: 1, coverage_state: 'ok' as const,
      }],
    }))

    const { result, rerender } = renderHook(
      ({ sort }) => useRadarInstruments({ market: 'TW', sort }),
      { initialProps: { sort: 'latest' as const }, wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data?.items[0].instrument_name).toBe('依 latest'))

    rerender({ sort: 'reports' as never })
    await waitFor(() => expect(result.current.data?.items[0].instrument_name).toBe('依 reports'))
    expect(radarApi.getRadarInstruments).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: 'reports' }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('立場篩選同樣進 queryKey', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockImplementation(async params => ({
      total: 1, limit: 50, offset: 0, has_more: false, next_offset: null,
      items: [{
        market: 'TW' as const, market_display: '台股',
        instrument_code: '1', instrument_name: params.stance ?? '全部',
        broker_count: 1, report_count: 1, coverage_state: 'ok' as const,
      }],
    }))

    const { result, rerender } = renderHook(
      ({ stance }) => useRadarInstruments({ market: 'TW', stance }),
      { initialProps: { stance: undefined as 'bullish' | undefined }, wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data?.items[0].instrument_name).toBe('全部'))

    rerender({ stance: 'bullish' })
    await waitFor(() => expect(result.current.data?.items[0].instrument_name).toBe('bullish'))
  })

  /*
   * 預設排序不送出去：後端 `sort` 的預設就是 `latest`。若無條件塞鍵，既有那條
   * 「載入下一頁的參數逐字比對」會紅，而網址也會多出一個等同於預設的參數。
   */
  it('預設排序不進請求參數', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 0, limit: 50, offset: 0, has_more: false, next_offset: null, items: [],
    })

    const { result } = renderHook(
      () => useRadarInstruments({ market: 'TW', sort: 'latest' }),
      { wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(radarApi.getRadarInstruments).toHaveBeenLastCalledWith(
      { market: 'TW', q: undefined, sort: undefined, stance: undefined, limit: 50, offset: 0 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })
})
