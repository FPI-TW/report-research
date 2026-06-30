import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../theme'
import * as api from './api'
import SearchPage from './SearchPage'

// ── Shared fixtures ────────────────────────────────────────────────────────────
const STATS = {
  total_reports: 10,
  total_chunks: 100,
  markets: [{ market: 'TW', count: 5 }],
  instrument_types: [{ type: 'equity', count: 5 }],
  report_types: [{ type: '法說會', count: 3 }],
  username: 'testuser',
}

const BASE_ITEM = {
  report_id: 'r1',
  file_name: 'test-report.pdf',
  market: 'TW',
  source: null,
  summary: null,
  report_date: '2026-01-15',
  report_type: null,
  instrument_types: null,
  relates_stock: null,
  relates_futures: null,
  stock_targets: null,
  futures_targets: null,
}

// ── Test wrapper ───────────────────────────────────────────────────────────────
function renderPage(url = '/search') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return {
    qc,
    ...render(
      <MemoryRouter initialEntries={[url]}>
        <QueryClientProvider client={qc}>
          <MantineProvider theme={theme}>
            <SearchPage />
          </MantineProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    ),
  }
}

afterEach(() => {
  vi.restoreAllMocks()
})

// ── (a) Results render after load ──────────────────────────────────────────────
test('(a) stats+results 載入後顯示研報卡片', async () => {
  vi.spyOn(api, 'getStats').mockResolvedValue(STATS)
  vi.spyOn(api, 'getReports').mockResolvedValue({
    total: 1,
    offset: 0,
    items: [BASE_ITEM],
  })

  const { qc, unmount } = renderPage()

  // result-card 應在非同步載入後出現
  expect(await screen.findAllByTestId('result-card')).toHaveLength(1)
  // results-meta 應顯示篇數
  expect(screen.getByTestId('results-meta')).toBeInTheDocument()

  unmount()
  qc.clear()
})

// ── (b) Empty state when items=[] ──────────────────────────────────────────────
test('(b) 無結果時顯示 EmptyState', async () => {
  vi.spyOn(api, 'getStats').mockResolvedValue(STATS)
  vi.spyOn(api, 'getReports').mockResolvedValue({
    total: 0,
    offset: 0,
    items: [],
  })

  const { qc, unmount } = renderPage()

  expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
  // 卡片不應出現
  expect(screen.queryAllByTestId('result-card')).toHaveLength(0)

  unmount()
  qc.clear()
})

// ── (c) 載入更多 triggers fetchNextPage / loads page 2 ────────────────────────
test('(c) 點擊載入更多觸發第 2 頁請求', async () => {
  vi.spyOn(api, 'getStats').mockResolvedValue(STATS)
  const mockGetReports = vi
    .spyOn(api, 'getReports')
    .mockResolvedValueOnce({ total: 100, offset: 0, items: [BASE_ITEM] })
    .mockResolvedValueOnce({
      total: 100,
      offset: 1,
      items: [{ ...BASE_ITEM, report_id: 'r2', file_name: 'second-report.pdf' }],
    })

  const { qc, unmount } = renderPage()

  // 等第一頁出現
  await screen.findAllByTestId('result-card')

  // 載入更多按鈕應存在（total=100 > loaded=1）
  const btn = await screen.findByTestId('load-more-btn')
  expect(btn).toBeInTheDocument()

  // 點擊觸發 page 2
  fireEvent.click(btn)

  // 等待第二次 API 呼叫
  await waitFor(() => expect(mockGetReports).toHaveBeenCalledTimes(2))

  // 兩頁的卡片都應存在
  await waitFor(() =>
    expect(screen.getAllByTestId('result-card')).toHaveLength(2),
  )

  unmount()
  qc.clear()
})
