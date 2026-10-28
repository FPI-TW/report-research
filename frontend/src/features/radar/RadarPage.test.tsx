import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import * as radarApi from '../../lib/radarApi'
import {
  radarOverviewSchema, type BrokerHistory, type EventCard, type RadarOverview,
} from '../../lib/radarSchemas'
import RadarPage from './RadarPage'

vi.mock('../../lib/radarApi')

function wrap(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/consensus" element={<RadarPage />} />
          <Route path="/library" element={<LocationProbe />} />
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

function SearchProbe() {
  const location = useLocation()
  return <div data-testid="radar-search">{location.search}</div>
}

function wrapWithProbe(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/consensus" element={<><RadarPage /><SearchProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('展開的券商進網址', () => {
  it('網址帶 broker：進頁就展開那一家（貼連結給別人看得到同一個畫面）', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    vi.mocked(radarApi.getBrokerHistory).mockReturnValue(new Promise(() => {}))
    wrapWithProbe('/consensus?market=TW&code=8046&window=90&broker=daiwa')
    const toggle = await screen.findByTestId('broker-row-daiwa')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('展開與收合都寫回網址，且用 replace 不堆歷史', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    vi.mocked(radarApi.getBrokerHistory).mockReturnValue(new Promise(() => {}))
    wrapWithProbe('/consensus?market=TW&code=8046&window=90')
    const toggle = await screen.findByTestId('broker-row-daiwa')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toContain('broker=daiwa'))
    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).not.toContain('broker='))
  })
})

describe('RadarPage', () => {
  it('無 code 時顯示標的清單，標題是「券商共識」', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 1, offset: 0,
      items: [{
        market: 'TW', market_display: '台股',
        instrument_code: '8046', instrument_name: '南電',
        broker_count: 12, report_count: 91,
        latest_report_date: '2026-07-11', coverage_state: 'partial',
      }],
    })
    wrap('/consensus')
    expect(screen.getByRole('heading', { level: 1, name: '券商共識' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('南電')).toBeInTheDocument())
    expect(screen.getByText('檔標的')).toHaveTextContent('1 檔標的')
    expect(radarApi.getRadarInstruments).toHaveBeenCalled()
  })

  it('載入中與載入失敗都不顯示標的數', async () => {
    let reject: (e: Error) => void = () => {}
    vi.mocked(radarApi.getRadarInstruments).mockReturnValue(new Promise((_, r) => { reject = r }))
    wrap('/consensus')
    expect(screen.getAllByTestId('picker-skeleton').length).toBeGreaterThan(0)
    expect(screen.queryByText(/檔標的/)).not.toBeInTheDocument()
    reject(new Error('down'))
    expect(await screen.findByRole('alert')).toHaveTextContent('券商共識載入失敗')
    expect(screen.queryByText(/檔標的/)).not.toBeInTheDocument()
  })

  it('載入失敗可重試', async () => {
    vi.mocked(radarApi.getRadarInstruments)
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValueOnce({ total: 0, offset: 0, items: [] })
    wrap('/consensus')
    fireEvent.click(await screen.findByRole('button', { name: '重試' }))
    await waitFor(() => expect(radarApi.getRadarInstruments).toHaveBeenCalledTimes(2))
  })

  it('有篩選但沒有結果：顯示「沒有符合的標的」，清除篩選會清掉網址條件', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrapWithProbe('/consensus?q=zzz&market=US&stance=bearish')
    expect(await screen.findByRole('heading', { name: '沒有符合的標的' })).toBeInTheDocument()
    expect(screen.getByText('找不到「zzz」，換個名稱或代號再試一次。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除篩選' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe(''))
  })

  it('清單與詳情都顯示免責與 AI 聲明', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrap('/consensus')
    expect(screen.getByText(/由 AI 整理.*非投資建議/)).toBeInTheDocument()
  })

  it('顯示完整九市場並標記目前選擇', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrap('/consensus?market=FX')

    for (const label of ['全部', '台股', '美股', '港股', '陸股', '台指期', '外匯', '總經', '全球', '加密']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: '外匯' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('搜尋標的')).toHaveAttribute('maxlength', '64')
  })

  it('立場分段寫進網址的 stance，「全部」會刪掉它', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrapWithProbe('/consensus')
    const group = screen.getByRole('radiogroup', { name: '立場' })
    expect(within(group).getByRole('radio', { name: '全部' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(group).getByRole('radio', { name: '偏空' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe('?stance=bearish'))
    fireEvent.click(within(group).getByRole('radio', { name: '全部' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe(''))
  })

  it('排序下拉寫進網址的 sort（預設值不寫）', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrapWithProbe('/consensus')
    fireEvent.change(screen.getByRole('combobox', { name: '排序' }), { target: { value: 'reports' } })
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe('?sort=reports'))
    fireEvent.change(screen.getByRole('combobox', { name: '排序' }), { target: { value: 'latest' } })
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe(''))
  })

  it('搜尋框按 Enter 立即寫進網址，清除鈕清空', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrapWithProbe('/consensus')
    const input = screen.getByLabelText('搜尋標的')
    fireEvent.change(input, { target: { value: '台積' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent)
      .toBe(`?q=${encodeURIComponent('台積')}`))
    fireEvent.click(screen.getByRole('button', { name: '清除搜尋' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toBe(''))
    expect(input).toHaveValue('')
  })

  it('無效市場不進入詳情，改回標的清單', async () => {
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({
      total: 0, limit: 50, offset: 0, has_more: false, next_offset: null, items: [],
    })

    wrap('/consensus?market=ZZ&code=8046')

    expect(screen.getByRole('heading', { level: 1, name: '券商共識' })).toBeInTheDocument()
    await waitFor(() => expect(radarApi.getRadarInstruments).toHaveBeenCalled())
    expect(radarApi.getInstrumentRadar).not.toHaveBeenCalled()
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

    wrap('/consensus')
    const more = await screen.findByRole('button', { name: '載入更多（還有 25 檔）' })
    fireEvent.click(more)

    await waitFor(() => expect(screen.getByText('標的74')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /載入更多/ })).not.toBeInTheDocument()
    expect(radarApi.getRadarInstruments).toHaveBeenLastCalledWith(
      { market: undefined, q: undefined, limit: 50, offset: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('標的目錄下一頁失敗時保留既有列，並可重試失敗頁', async () => {
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

    wrap('/consensus')
    fireEvent.click(await screen.findByRole('button', { name: '載入更多（還有 25 檔）' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('載入更多標的失敗')
    expect(screen.getByText('標的0')).toBeInTheDocument()

    fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
    await waitFor(() => expect(screen.getByText('標的74')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(nextAttempts).toBe(2)
  })

  it('有 market+code 時載入詳情的各區塊', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      coverage: {
        state: 'ok', brokers_total: 12, brokers_extracted: 1,
        brokers_in_consensus: 1, reports_available: 91, note: '',
      },
    }))
    wrap('/consensus?market=TW&code=8046&window=90')
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '南電' })).toBeInTheDocument())
    for (const name of ['共識評等', '目標價中位數', 'EPS 中位數', '各券商目標價', '四向觀點', '各券商最新觀點', '近期變化']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }
    expect(screen.getAllByText('轉強').length).toBeGreaterThan(0)
    expect(screen.getByText(
      (_, el) => el?.tagName === 'P' && (el.textContent ?? '').startsWith('12 家券商，1 家納入共識，91 篇研報'),
    )).toBeInTheDocument()
    expect(radarApi.getInstrumentRadar).toHaveBeenCalledWith(
      '8046', 'TW', '90', expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('返回鈕回到清單並保留清單的篩選', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    vi.mocked(radarApi.getRadarInstruments).mockResolvedValue({ total: 0, offset: 0, items: [] })
    wrapWithProbe('/consensus?q=%E5%8D%97&market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '券商共識' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).not.toContain('code='))
    expect(screen.getByTestId('radar-search').textContent).toContain('q=')
  })

  it('partial 在頁首說明列下方附上資料註記', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      coverage: {
        state: 'partial',
        brokers_total: 2,
        brokers_extracted: 2,
        brokers_in_consensus: 2,
        reports_available: 8,
        note: '歷史研報仍有部分欄位尚待整理。',
      },
    }))

    wrap('/consensus?market=TW&code=8046&window=90')

    expect(await screen.findByText('歷史研報仍有部分欄位尚待整理。')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '共識評等' })).toBeInTheDocument()
  })

  it('pending_extraction 顯示整理中狀態，「相關研報」帶代號與市場到研報庫', async () => {
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
    wrap('/consensus?market=TW&code=8046')
    expect(await screen.findByRole('heading', { name: '觀點資料整理中' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '相關研報' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/library?q=8046&market=TW')
  })

  it('window_empty 提供「看全部期間」', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview({
      coverage: {
        state: 'window_empty', brokers_total: 3, brokers_extracted: 3,
        brokers_in_consensus: 0, reports_available: 5, note: '',
      },
    }))
    wrapWithProbe('/consensus?market=TW&code=8046&window=30')
    fireEvent.click(await screen.findByRole('button', { name: '看全部期間' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toContain('window=all'))
  })

  it('404 顯示找不到這檔標的', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockRejectedValue(new ApiError(404, 'HTTP 404'))
    wrap('/consensus?market=TW&code=0000')
    expect(await screen.findByRole('heading', { name: '找不到這檔標的' })).toBeInTheDocument()
  })

  it('切期間呼叫 API', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    wrap('/consensus?market=TW&code=8046&window=90')
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '南電' })).toBeInTheDocument())
    const group = screen.getByRole('radiogroup', { name: '期間' })
    expect(within(group).getByRole('radio', { name: '90 天' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(group).getByRole('radio', { name: '30 天' }))
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

    wrap('/consensus?market=TW&code=8046&window=90')
    const expand = await screen.findByRole('button', { name: '查看全部 20 則' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(expand.getAttribute('aria-controls')!)).not.toBeNull()
    // 按鈕是 h2 的兄弟而不是子節點，標題的可及名稱才不會隨展開狀態改變。
    expect(screen.getByRole('heading', { name: '近期變化' })).toBeInTheDocument()
    fireEvent.click(expand)
    await waitFor(() => expect(screen.getByText('event-11')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: '收合' })).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: '載入更多（還有 8 則）' }))
    await waitFor(() => expect(screen.getByText('event-19')).toBeInTheDocument())
    expect(radarApi.getRadarEvents).toHaveBeenLastCalledWith(
      '8046',
      { market: 'TW', window: '90', limit: 12, offset: 12 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('舊 overview 只有 preview 與 total 時，點查看全部仍會請求事件 API', async () => {
    const legacyPayload = {
      ...overview({
        rating: null,
        recent_events: [radarEvent(0), radarEvent(1), radarEvent(2)],
        recent_events_total: 20,
      }),
    } as Record<string, unknown>
    delete legacyPayload.recent_events_has_more
    delete legacyPayload.recent_events_next_offset

    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(
      radarOverviewSchema.parse(legacyPayload),
    )
    vi.mocked(radarApi.getRadarEvents).mockResolvedValue({
      market: 'TW', instrument_code: '8046', window: '90', as_of: '2026-07-11',
      total: 20, limit: 12, offset: 0, has_more: true, next_offset: 12,
      items: Array.from({ length: 12 }, (_, i) => radarEvent(i)),
    })

    wrap('/consensus?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '查看全部 20 則' }))

    await waitFor(() => expect(radarApi.getRadarEvents).toHaveBeenCalledWith(
      '8046',
      { market: 'TW', window: '90', limit: 12, offset: 0 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
  })

  it('完整事件首次請求失敗時保留預覽，重試後恢復', async () => {
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

    wrap('/consensus?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '查看全部 20 則' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('載入完整事件失敗')
    expect(screen.getByText('event-0')).toBeInTheDocument()
    fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
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

    wrap('/consensus?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '查看全部 20 則' }))
    await waitFor(() => expect(screen.getByText('event-11')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '載入更多（還有 8 則）' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('載入完整事件失敗')
    expect(screen.getByText('event-11')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '載入更多（還有 8 則）' })).not.toBeInTheDocument()

    fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
    await waitFor(() => expect(screen.getByText('event-19')).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(nextAttempts).toBe(2)
  })

  it('展開券商列才請求歷程，歷程列出日期、評等、目標價與備註', async () => {
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
      }] as unknown as BrokerHistory['snapshots'],
      diffs: [{
        from_report_date: null, to_report_date: '2026-07-11',
        changes: [], has_prior_report: false, has_prior_comparable: false, note: '沒有更早研報',
      }],
      coverage_state: 'partial',
    })
    wrap('/consensus?market=TW&code=8046')
    await waitFor(() => expect(screen.getByTestId('broker-row-daiwa')).toBeInTheDocument())
    expect(radarApi.getBrokerHistory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))
    await waitFor(() => {
      expect(radarApi.getBrokerHistory).toHaveBeenCalledWith(
        '8046', 'daiwa', 'TW', '90',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
    expect(await screen.findByText('首次納入')).toBeInTheDocument()
  })

  it('點目標價點圖上的點會展開那家券商', async () => {
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(overview())
    vi.mocked(radarApi.getBrokerHistory).mockReturnValue(new Promise(() => {}))
    wrapWithProbe('/consensus?market=TW&code=8046&window=90')
    fireEvent.click(await screen.findByRole('button', { name: '大和，目標價 2,444' }))
    await waitFor(() => expect(screen.getByTestId('radar-search').textContent).toContain('broker=daiwa'))
    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'true')
  })
})
