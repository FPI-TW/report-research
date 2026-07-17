import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as radarApi from '../../lib/radarApi'
import type { EventCard, RadarOverview } from '../../lib/radarSchemas'
import RadarPage from './RadarPage'

vi.mock('../../lib/radarApi')

function wrap(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/radar" element={<RadarPage />} />
          <Route path="/search" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}{location.search}</div>
}

function overview(partial?: Partial<RadarOverview>): RadarOverview {
  return {
    market: 'TW',
    market_display: '台股',
    instrument_code: '8046',
    instrument_name: '南電',
    window: '90',
    as_of: '2026-07-11',
    coverage: {
      state: 'partial',
      brokers_total: 12,
      brokers_extracted: 1,
      brokers_in_consensus: 1,
      reports_available: 91,
      note: '部分擷取',
    },
    rating: {
      distribution: [{ rating: 'buy', count: 1 }],
      bullish: 1, neutral: 0, bearish: 0, unknown: 0,
      total_rated: 1, median_rating: 'buy',
      upgrades: 0, downgrades: 0, unchanged: 1,
    },
    target_price: {
      primary_currency: 'TWD',
      groups: [{
        currency: 'TWD', median: 2444, q1: 2444, q3: 2444,
        low: 2400, high: 2500, count: 1,
        revision_pct: 3.2, revision_direction: 'up',
      }],
      note: null,
    },
    eps: { primary: null, groups: [] },
    thesis: [
      {
        dimension: 'outlook', dimension_display: '展望',
        label: 'strengthen', label_display: '轉強',
        brokers_strengthen: 1, brokers_weaken: 0, brokers_comparable: 1,
        coverage_note: '1/1 家出現上修訊號',
      },
      {
        dimension: 'catalyst', dimension_display: '催化劑',
        label: 'insufficient', label_display: '資料不足',
        brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
      },
      {
        dimension: 'risk', dimension_display: '風險',
        label: 'stable', label_display: '無顯著變化',
        brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
      },
      {
        dimension: 'valuation', dimension_display: '估值',
        label: 'diverging', label_display: '分歧擴大',
        brokers_strengthen: 0, brokers_weaken: 0, brokers_comparable: 0,
      },
    ],
    recent_events: [{
      broker: 'daiwa', broker_display: '大和',
      report_date: '2026-07-11',
      headline: '上修目標價',
      changes: [{
        field: 'target_price', label: '目標價上修', direction: 'up',
        prev_value: '2300', curr_value: '2444', pct_change: 6.3,
        comparable: true, incomparable_reason: null, dimension: null,
      }],
      evidence: ['需求能見度改善'],
      report_link: {
        report_id: 'r1', file_name: 'daiwa.pdf',
        report_date: '2026-07-11', broker: 'daiwa', broker_display: '大和',
      },
    }],
    recent_events_total: 1,
    recent_events_has_more: false,
    recent_events_next_offset: null,
    brokers: [{
      broker: 'daiwa', broker_display: '大和',
      latest_rating: 'buy', latest_rating_raw: 'Buy',
      latest_target_price: 2444, latest_target_currency: 'TWD',
      latest_eps_value: null, latest_eps_fy: null,
      latest_report_date: '2026-07-11',
      report_link: {
        report_id: 'r1', file_name: 'daiwa.pdf',
        report_date: '2026-07-11', broker: 'daiwa', broker_display: '大和',
      },
      recent_change_label: '目標價上修',
      recent_change_direction: 'up',
      stale: false, has_history: true,
    }],
    notes: [],
    ...partial,
  }
}

function radarEvent(index: number): EventCard {
  return {
    broker: `broker-${index}`,
    broker_display: `券商 ${index}`,
    report_date: '2026-07-11',
    headline: `event-${index}`,
    changes: [],
    evidence: [`evidence-${index}`],
    report_link: {
      report_id: `event-report-${index}`,
      file_name: `event-${index}.pdf`,
      report_date: '2026-07-11',
      broker: `broker-${index}`,
      broker_display: `券商 ${index}`,
    },
  }
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('RadarPage', () => {
  it('無 code 時顯示選標的清單', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 1, offset: 0,
      items: [{
        market: 'TW', market_display: '台股',
        instrument_code: '8046', instrument_name: '南電',
        broker_count: 12, report_count: 91,
        latest_report_date: '2026-07-11', coverage_state: 'partial',
      }],
    })
    wrap('/radar')
    expect(screen.getByRole('heading', { name: '廷豐觀點' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('南電')).toBeInTheDocument())
    expect(radarApi.getRadarInstruments).toHaveBeenCalled()
  })

  // 免責曾在改版中從 picker 副標與 RadarHeader info 泡泡雙雙消失而無人察覺
  // （plan 誤以為「已改置底 notes」，但 notes 只放資料品質註記）。這兩條把它釘住。
  it('picker 狀態顯示免責', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 0, offset: 0, items: [],
    })
    wrap('/radar')
    expect(screen.getByText(/非系統預測或投資建議/)).toBeInTheDocument()
  })

  it('picker 顯示完整九市場並標記目前選擇', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrap('/radar?market=FX')

    for (const label of ['台股', '美股', '港股', '陸股', '台指期', '外匯', '總經', '全球', '加密']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: '外匯' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('搜尋標的')).toHaveAttribute('maxlength', '64')
  })

  it('無效市場不進入 overview，改回選標的狀態', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 0, limit: 50, offset: 0, has_more: false, next_offset: null, items: [],
    })

    wrap('/radar?market=ZZ&code=8046')

    expect(screen.getByRole('heading', { name: '廷豐觀點' })).toBeInTheDocument()
    await waitFor(() => expect(radarApi.getRadarInstruments).toHaveBeenCalled())
    expect(radarApi.getInstrumentRadar).not.toHaveBeenCalled()
  })

  it('overview 狀態顯示免責（即使 notes 為空）', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({ notes: [] }))
    wrap('/radar?market=TW&code=8046&window=90')
    await waitFor(() => expect(screen.getByRole('heading', { name: '南電' })).toBeInTheDocument())
    expect(screen.getByText(/非系統預測或投資建議/)).toBeInTheDocument()
  })

  it('標的數超出 limit 時筆數標籤如實顯示已載入/總數', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 200, offset: 0,
      items: Array.from({ length: 50 }, (_, i) => ({
        market: 'TW', market_display: '台股',
        instrument_code: String(1000 + i), instrument_name: `標的${i}`,
        broker_count: 3, report_count: 5,
        latest_report_date: '2026-07-11', coverage_state: 'partial' as const,
      })),
    })
    wrap('/radar')
    await waitFor(() => expect(screen.getByText('顯示 50 / 200 檔')).toBeInTheDocument())
  })

  it('標的目錄可依 offset 載入全部結果', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockImplementation(async params => {
      const offset = params.offset ?? 0
      const count = offset === 0 ? 50 : 25
      return {
        total: 75, limit: 50, offset,
        has_more: offset === 0, next_offset: offset === 0 ? 50 : null,
        items: Array.from({ length: count }, (_, i) => ({
          market: 'TW', market_display: '台股',
          instrument_code: String(1000 + offset + i), instrument_name: `標的${offset + i}`,
          broker_count: 3, report_count: 5,
          latest_report_date: '2026-07-11', coverage_state: 'partial' as const,
        })),
      }
    })

    wrap('/radar')
    const more = await screen.findByRole('button', { name: '載入更多（尚有 25 檔）' })
    fireEvent.click(more)

    await waitFor(() => expect(screen.getByText('顯示 75 檔')).toBeInTheDocument())
    expect(radarApi.getRadarInstruments).toHaveBeenLastCalledWith(
      { market: undefined, q: undefined, limit: 50, offset: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('標的目錄下一頁失敗時保留既有卡片，並可重試失敗頁', async () => {
    let nextAttempts = 0
    vi.mocked(radarApi.getRadarInstruments).mockImplementation(async params => {
      const offset = params.offset ?? 0
      if (offset === 50 && ++nextAttempts === 1) throw new Error('next-page')
      const count = offset === 0 ? 50 : 25
      return {
        total: 75, limit: 50, offset,
        has_more: offset === 0, next_offset: offset === 0 ? 50 : null,
        items: Array.from({ length: count }, (_, i) => ({
          market: 'TW', market_display: '台股',
          instrument_code: String(1000 + offset + i), instrument_name: `標的${offset + i}`,
          broker_count: 3, report_count: 5,
          latest_report_date: '2026-07-11', coverage_state: 'partial' as const,
        })),
      }
    })

    wrap('/radar')
    fireEvent.click(await screen.findByRole('button', { name: '載入更多（尚有 25 檔）' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('載入更多標的失敗')
    expect(screen.getByText('標的0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '載入更多（尚有 25 檔）' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重試載入更多' }))
    await waitFor(() => expect(screen.getByText('標的74')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(nextAttempts).toBe(2)
  })

  it('標的數未超出 limit 時筆數標籤只顯示總數', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 1, offset: 0,
      items: [{
        market: 'TW', market_display: '台股',
        instrument_code: '8046', instrument_name: '南電',
        broker_count: 12, report_count: 91,
        latest_report_date: '2026-07-11', coverage_state: 'partial',
      }],
    })
    wrap('/radar')
    await waitFor(() => expect(screen.getByText('顯示 1 檔')).toBeInTheDocument())
  })

  it('有 market+code 時載入總覽', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    wrap('/radar?market=TW&code=8046&window=90')
    await waitFor(() => expect(screen.getByRole('heading', { name: '南電' })).toBeInTheDocument())
    expect(screen.getByText('券商共識')).toBeInTheDocument()
    expect(screen.getByText('四向觀點')).toBeInTheDocument()
    expect(screen.getByText('轉強')).toBeInTheDocument()
    expect(screen.getByText('近期關鍵變化')).toBeInTheDocument()
    expect(screen.getByText('各券商最新觀點')).toBeInTheDocument()
    expect(radarApi.getInstrumentRadar).toHaveBeenCalledWith(
      '8046', 'TW', '90', expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('pending_extraction 顯示尚未整理狀態', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      coverage: {
        state: 'pending_extraction',
        brokers_total: 12, brokers_extracted: 0,
        brokers_in_consensus: 0, reports_available: 91,
        note: '此標的已有研報，但尚未完成擷取。',
      },
      rating: null, target_price: null, eps: null,
      thesis: overview().thesis.map(t => ({ ...t, label: 'insufficient', label_display: '資料不足' })),
      recent_events: [], recent_events_total: 0, brokers: [],
    }))
    wrap('/radar?market=TW&code=8046')
    await waitFor(() => expect(screen.getByText('尚未完成觀點資料整理')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '查看相關研報' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/search?q=8046&market=TW')
  })

  it('切窗期呼叫 API 並不重掛載 header', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    wrap('/radar?market=TW&code=8046&window=90')
    await waitFor(() => expect(screen.getByText('南電')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('radio', { name: '30 天' }))
    await waitFor(() => {
      expect(radarApi.getInstrumentRadar).toHaveBeenCalledWith(
        '8046', 'TW', '30', expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
  })

  it('查看全部事件後可依 next_offset 載入完整清單', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      recent_events: [radarEvent(0), radarEvent(1), radarEvent(2)],
      recent_events_total: 20,
      recent_events_has_more: true,
      recent_events_next_offset: 3,
    }))
    vi.mocked(radarApi.getRadarEvents).mockImplementation(async (_code, params) => {
      const offset = params.offset ?? 0
      const count = offset === 0 ? 12 : 8
      return {
        market: 'TW', instrument_code: '8046', window: '90', as_of: '2026-07-11',
        total: 20, limit: 12, offset, has_more: offset === 0,
        next_offset: offset === 0 ? 12 : null,
        items: Array.from({ length: count }, (_, i) => radarEvent(offset + i)),
      }
    })

    wrap('/radar?market=TW&code=8046&window=90')
    const expand = await screen.findByRole('button', { name: '查看全部 20 項' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(expand).toHaveAttribute('aria-controls', 'radar-recent-events')
    fireEvent.click(expand)
    await waitFor(() => expect(screen.getByText('event-11')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: '收合' })).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: '載入更多（尚有 8 項）' }))
    await waitFor(() => expect(screen.getByText('event-19')).toBeInTheDocument())
    expect(radarApi.getRadarEvents).toHaveBeenLastCalledWith(
      '8046',
      { market: 'TW', window: '90', limit: 12, offset: 12 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('完整事件首次請求失敗時保留 preview，重試後恢復', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      recent_events: [radarEvent(0), radarEvent(1), radarEvent(2)],
      recent_events_total: 20,
      recent_events_has_more: true,
      recent_events_next_offset: 3,
    }))
    vi.mocked(radarApi.getRadarEvents)
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({
        market: 'TW', instrument_code: '8046', window: '90', as_of: '2026-07-11',
        total: 12, limit: 12, offset: 0, has_more: false, next_offset: null,
        items: Array.from({ length: 12 }, (_, i) => radarEvent(i)),
      })

    wrap('/radar?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '查看全部 20 項' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('載入完整事件失敗')
    expect(screen.getByText('event-0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試載入事件' }))
    await waitFor(() => expect(screen.getByText('event-11')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('事件下一頁失敗時保留已載入事件，並只重試失敗頁', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      recent_events: [radarEvent(0), radarEvent(1), radarEvent(2)],
      recent_events_total: 20,
      recent_events_has_more: true,
      recent_events_next_offset: 3,
    }))
    let nextAttempts = 0
    vi.mocked(radarApi.getRadarEvents).mockImplementation(async (_code, params) => {
      const offset = params.offset ?? 0
      if (offset === 12 && ++nextAttempts === 1) throw new Error('next-page')
      const count = offset === 0 ? 12 : 8
      return {
        market: 'TW', instrument_code: '8046', window: '90', as_of: '2026-07-11',
        total: 20, limit: 12, offset, has_more: offset === 0,
        next_offset: offset === 0 ? 12 : null,
        items: Array.from({ length: count }, (_, i) => radarEvent(offset + i)),
      }
    })

    wrap('/radar?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '查看全部 20 項' }))
    await waitFor(() => expect(screen.getByText('event-11')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '載入更多（尚有 8 項）' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('載入完整事件失敗')
    expect(screen.getByText('event-11')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '載入更多（尚有 8 項）' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重試載入事件' }))
    await waitFor(() => expect(screen.getByText('event-19')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(nextAttempts).toBe(2)
  })

  it('展開券商列才請求歷程', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    vi.mocked(radarApi.getBrokerHistory).mockResolvedValue({
      market: 'TW', instrument_code: '8046',
      broker: 'daiwa', broker_display: '大和',
      window: '90', as_of: '2026-07-11',
      current_rating: 'buy', report_count: 1,
      snapshots: [{
        report_id: 'r1', report_date: '2026-07-11', in_window: true,
        rating: 'buy', rating_raw: 'Buy',
        target_price: 2444, target_currency: 'TWD',
        eps: [], thesis: [], extraction_status: 'valid',
        report_link: {
          report_id: 'r1', file_name: 'daiwa.pdf',
          report_date: '2026-07-11', broker: 'daiwa', broker_display: '大和',
        },
      }],
      diffs: [{
        from_report_date: null, to_report_date: '2026-07-11',
        changes: [], has_prior_comparable: false, note: '無前次',
      }],
      coverage_state: 'partial',
    })
    wrap('/radar?market=TW&code=8046')
    await waitFor(() => expect(screen.getByTestId('broker-row-daiwa')).toBeInTheDocument())
    expect(radarApi.getBrokerHistory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))
    await waitFor(() => {
      expect(radarApi.getBrokerHistory).toHaveBeenCalledWith(
        '8046', 'daiwa', 'TW', '90',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
    await waitFor(() => {
      expect(screen.getAllByText(/觀點歷程/).length).toBeGreaterThan(0)
    })
  })
})
