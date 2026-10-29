import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router'
import type { ReactNode } from 'react'
import SearchPage from './SearchPage'
import * as searchApi from '../../lib/searchApi'
import * as useStatsMod from '../../lib/useStats'
import * as libraryApi from '../../lib/libraryApi'
import * as briefApi from '../../lib/briefApi'
import * as readingApi from '../../lib/readingApi'
import * as radarApi from '../../lib/radarApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import type { RadarOverview } from '../../lib/radarSchemas'

vi.mock('../../lib/searchApi')
vi.mock('../../lib/useStats')
vi.mock('../../lib/libraryApi')
vi.mock('../../lib/briefApi')
vi.mock('../../lib/readingApi')
vi.mock('../../lib/radarApi')

const hashOf = (id: string) => id.repeat(64).slice(0, 64)

function item(id: string) {
  return {
    report_id: id, file_hash: hashOf(id), file_name: id + '.pdf',
    market: 'TW', source: '元大', summary: `${id} 的摘要`,
    report_date: '2026-06-25', report_type: '個股', instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: ['8046'], futures_targets: null,
  }
}

function listResp(ids: string[], total: number) {
  return { total, offset: 0, items: ids.map(item) }
}

function searchResp(ids: string[], total: number, facets: { market: string; count: number }[] = []) {
  return {
    query: 'q', market: null, total, market_facets: facets,
    results: ids.map((id, i) => ({ ...item(id), rank: i + 1, best_score: 0.9, match_count: 3, passages: [] })),
  }
}

function LocationProbe() {
  const loc = useLocation()
  return <output data-testid="loc">{loc.pathname + loc.search}</output>
}

function wrap(node: ReactNode, entry = '/library') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes><Route path="/library" element={<>{node}<LocationProbe /></>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(useStatsMod.useStats).mockReturnValue({
    data: { total_reports: 5, markets: [{ market: 'TW', count: 5 }], instrument_types: [], report_types: [] },
  } as unknown as ReturnType<typeof useStatsMod.useStats>)
  vi.mocked(libraryApi.getRatingChanges).mockResolvedValue({
    items: [{
      market: 'TW', instrument_code: '8046', instrument_name: '南電', broker: 'fubon', broker_display: '富邦',
      report_date: '2026-10-05', rating_from: 'neutral', rating_to: 'buy', direction: 'up',
    }],
  })
  vi.mocked(briefApi.getLatestBrief).mockResolvedValue({
    status: 'ready', available_dates: [],
    brief: {
      brief_date: '2026-10-05', window_start: '', window_end: '', created_at: '', signal_count: 0,
      report_count: 1, markdown: '## 今日重點\n- 簡報第一條（kgi）', reports: [],
    },
  })
  vi.mocked(readingApi.getReadingDoc).mockResolvedValue({
    report_id: 'a', file_hash: hashOf('a'), file_name: 'a.pdf', instrument_types: [], stock_targets: ['8046'],
    futures_targets: [], has_file: true, is_pdf: true, text_state: 'ok', text_chars: 1, signals_state: 'none',
    signals: [], takeaways: [{ ordinal: 1, claim: '頭條的第一條主張', quote: '原文' }],
  } as ReadingDoc)
  vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue({
    coverage: { brokers_in_consensus: 9 },
    rating: { total_rated: 9, median_rating: 'buy', upgrades: 2, downgrades: 0 },
  } as unknown as RadarOverview)
})

describe('SearchPage 首頁', () => {
  it('無查詢無篩選 → bento：頭條（摘錄與共識）、最新評等變動、今日簡報、更多最新入庫', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />)
    expect(screen.getByTestId('home-skeleton')).toBeInTheDocument()

    const headline = await screen.findByRole('link', { name: 'a.pdf' })
    expect(headline).toHaveAttribute('href', `/report/${hashOf('a')}`)
    expect(within(headline).getByText('頭條的第一條主張')).toBeInTheDocument()
    expect(within(headline).getByText('買進')).toBeInTheDocument()
    expect(radarApi.getInstrumentRadar).toHaveBeenCalledWith('8046', 'TW', '90', expect.anything())

    expect(screen.getByRole('link', { name: /南電 8046/ })).toHaveAttribute('href', '/consensus?market=TW&code=8046&window=90')
    expect(screen.getByText('簡報第一條')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'b.pdf' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查看全部 5 篇/ })).toBeInTheDocument()
    // 統計卡與語料庫組成已拿掉
    expect(screen.queryByText(/篇研報/)).toBeNull()
    expect(screen.queryByText('語料庫組成')).toBeNull()
  })

  it('附屬資料失敗只影響那一塊，首頁照樣出來', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    vi.mocked(libraryApi.getRatingChanges).mockRejectedValue(new Error('boom'))
    vi.mocked(radarApi.getInstrumentRadar).mockRejectedValue(new Error('404'))
    wrap(<SearchPage />)
    const headline = await screen.findByRole('link', { name: 'a.pdf' })
    expect(within(headline).queryByText('券商共識')).toBeNull()
    expect(screen.getByText('評等變動載入失敗')).toBeInTheDocument()
  })

  it('點更多最新入庫的一篇 → 預覽彈窗，可開閱讀頁或就這篇提問', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />)
    fireEvent.click(await screen.findByRole('link', { name: 'b.pdf' }))

    const dialog = await screen.findByRole('dialog', { name: 'b.pdf' })
    expect(within(dialog).getByText('b 的摘要')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '關閉' })).toHaveFocus()
    expect(within(dialog).getByRole('link', { name: '開啟閱讀頁' })).toHaveAttribute('href', `/report/${hashOf('b')}`)
    expect(within(dialog).getByRole('link', { name: '就這篇提問' }))
      .toHaveAttribute('href', `/assistant?q=${encodeURIComponent('關於《b.pdf》：')}`)

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('查看全部 → 最新入庫清單（篇數、依日期排序）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />, '/library?all=1')
    expect(await screen.findByRole('heading', { name: '最新入庫' })).toBeInTheDocument()
    expect(screen.getByText('2 篇')).toBeInTheDocument()
    expect(screen.getByText('依日期排序')).toBeInTheDocument()
    expect(screen.queryByText('最相關')).toBeNull()
  })
})

describe('SearchPage 搜尋', () => {
  it('搜尋態 → chips 顯示命中數、隱藏零命中市場；第一篇標最相關', async () => {
    vi.mocked(searchApi.searchReports).mockResolvedValue(
      searchResp(['a', 'b'], 8, [{ market: 'TW', count: 6 }, { market: 'US', count: 2 }]),
    )
    wrap(<SearchPage />, '/library?q=散熱')
    expect(await screen.findByRole('heading', { name: '搜尋結果' })).toBeInTheDocument()
    expect(screen.getByText('8 篇')).toBeInTheDocument()
    expect(screen.getByText('依相關度排序')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '台股 6' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^港股/ })).toBeNull()
    expect(within(screen.getByRole('link', { name: 'a.pdf' })).getByText('最相關')).toBeInTheDocument()
    expect(within(screen.getByRole('link', { name: 'b.pdf' })).queryByText('最相關')).toBeNull()
  })

  it('搜尋且已選市場 → chips 不顯示計數（其他市場命中數無從得知，不編造）', async () => {
    vi.mocked(searchApi.searchReports).mockResolvedValue(searchResp(['a'], 6, [{ market: 'TW', count: 6 }]))
    wrap(<SearchPage />, '/library?q=散熱&market=TW')
    await screen.findByRole('link', { name: 'a.pdf' })
    expect(screen.getByRole('button', { name: '台股' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /台股 \d/ })).toBeNull()
  })

  it('按 Enter 後骨架至少顯示 0.65 秒，即使結果已經回來', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    vi.mocked(searchApi.searchReports).mockResolvedValue(searchResp(['c'], 1))
    wrap(<SearchPage />)
    await screen.findByRole('link', { name: 'a.pdf' })

    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '散熱' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('loc')).toHaveTextContent('/library?q=%E6%95%A3%E7%86%B1')
    expect(screen.getByTestId('list-skeleton')).toBeInTheDocument()
    await new Promise(r => setTimeout(r, 300))
    expect(searchApi.searchReports).toHaveBeenCalled()
    expect(screen.getByTestId('list-skeleton')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'c.pdf' }, { timeout: 2000 })).toBeInTheDocument()
    expect(screen.queryByTestId('list-skeleton')).toBeNull()
  })

  it('沒有結果 → 「沒有符合的研報」，清除條件回首頁', async () => {
    vi.mocked(searchApi.searchReports).mockResolvedValue(searchResp([], 0))
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a'], 1))
    wrap(<SearchPage />, '/library?q=不存在&market=US')
    expect(await screen.findByRole('heading', { name: '沒有符合的研報' })).toBeInTheDocument()
    expect(screen.queryByText('依相關度排序')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '清除條件' }))
    expect(screen.getByTestId('loc')).toHaveTextContent(/^\/library$/)
    expect(screen.getByLabelText('搜尋研報')).toHaveValue('')
  })

  it('載入失敗 → 錯誤狀態，重試會再打一次', async () => {
    vi.mocked(searchApi.searchReports).mockRejectedValueOnce(new Error('timeout'))
      .mockResolvedValue(searchResp(['a'], 1))
    wrap(<SearchPage />, '/library?q=散熱')
    expect(await screen.findByRole('alert')).toHaveTextContent('研報載入失敗')
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(screen.getByTestId('list-skeleton')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'a.pdf' }, { timeout: 2000 })).toBeInTheDocument()
    expect(searchApi.searchReports).toHaveBeenCalledTimes(2)
  })

  it('切表格檢視不重抓（browseReports 次數不變）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a'], 1))
    wrap(<SearchPage />, '/library?market=TW')
    await screen.findByRole('link', { name: 'a.pdf' })
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('tab', { name: '表格檢視' }))
    await waitFor(() => expect(screen.getAllByRole('columnheader').length).toBeGreaterThan(0))
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
  })
})
