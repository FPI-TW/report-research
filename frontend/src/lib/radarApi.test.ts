import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getJSON } from './api'
import {
  getBrokerHistory,
  getInstrumentRadar,
  getRadarEvents,
  getRadarInstruments,
} from './radarApi'

vi.mock('./api', () => ({ getJSON: vi.fn() }))

beforeEach(() => {
  vi.resetAllMocks()
})

describe('radarApi', () => {
  it('目錄請求編碼 Unicode、offset 並傳遞 AbortSignal', () => {
    const signal = new AbortController().signal
    getRadarInstruments(
      { market: 'TW', q: '台積 電', limit: 50, offset: 50 },
      { signal },
    )

    expect(getJSON).toHaveBeenCalledWith(
      '/api/radar/instruments?market=TW&q=%E5%8F%B0%E7%A9%8D+%E9%9B%BB&limit=50&offset=50',
      expect.anything(),
      { cache: 'no-store', signal },
    )
  })

  it('總覽與券商歷程安全編碼 path 並傳遞 AbortSignal', () => {
    const signal = new AbortController().signal
    getInstrumentRadar('BRK/B', 'US', '30', { signal })
    getBrokerHistory('BRK/B', '券商 A/B', 'US', 'all', { signal })

    expect(getJSON).toHaveBeenNthCalledWith(
      1,
      '/api/instrument/BRK%2FB/radar?market=US&window=30',
      expect.anything(),
      { cache: 'no-store', signal },
    )
    expect(getJSON).toHaveBeenNthCalledWith(
      2,
      '/api/instrument/BRK%2FB/radar/brokers/%E5%88%B8%E5%95%86%20A%2FB?market=US&window=all',
      expect.anything(),
      { cache: 'no-store', signal },
    )
  })

  it('事件請求包含 limit/offset 並傳遞 AbortSignal', () => {
    const signal = new AbortController().signal
    getRadarEvents(
      '8046',
      { market: 'TW', window: '90', limit: 12, offset: 24 },
      { signal },
    )

    expect(getJSON).toHaveBeenCalledWith(
      '/api/instrument/8046/radar/events?market=TW&window=90&limit=12&offset=24',
      expect.anything(),
      { cache: 'no-store', signal },
    )
  })
})
