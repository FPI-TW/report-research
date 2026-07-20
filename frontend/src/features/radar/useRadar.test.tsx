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
})
