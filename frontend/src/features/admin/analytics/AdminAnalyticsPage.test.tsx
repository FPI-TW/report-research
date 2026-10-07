import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminAnalyticsPage from './AdminAnalyticsPage'
import { addDays, cellText, fmtMs, sinceForDays, taipeiToday } from './analyticsFormat'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const RANGE = {
  since: '2026-01-01', until: '2026-10-07', today: '2026-10-07', live_since: '2026-07-10', timezone: 'Asia/Taipei',
  min_users: 3,
  spans: [
    { since: '2026-01-01', until: '2026-07-09', source: 'rollup' },
    { since: '2026-07-10', until: '2026-10-07', source: 'live' },
  ],
}

const point = (day: string, over: Record<string, unknown> = {}) => ({
  day, source: 'live', has_data: true, questions: 4, askers: 2, active_users: 3, reading: 5, report_file: 1, search: 2,
  latency_p50_ms: 1200, latency_p95_ms: 4000, thinking_p50_ms: 300, thinking_p95_ms: 900, ...over,
})

const OVERVIEW = {
  range: RANGE,
  totals: { questions: 120, stopped: 2, reading: 50, report_file: 7, search: 30, active_users_peak: 9,
    distinct_users_live: 11, missing_days: 2 },
  latency: { p50_ms: 1500, p95_ms: 5200, thinking_p50_ms: 420, thinking_p95_ms: null, n: 98 },
  daily: [point('2026-10-05', { source: 'rollup', has_data: false, questions: null, askers: null, active_users: null,
    latency_p50_ms: null, latency_p95_ms: null }), point('2026-10-06'), point('2026-10-07')],
}

const cell = (key: string, value: number | null, users: number | null, label: string | null = null) => ({
  key, label, value, users, suppressed: value === null,
})
const list = (cells: unknown[], suppressed_count = 0) => ({ cells, suppressed_count, truncated: false })

const TOP = {
  range: RANGE, limit: 15,
  targets: list([cell('2330', 12, 5, '台積電')], 4),
  reports: list([], 2),
  markets: list([cell('TW', 20, 6, '台股'), cell('US', null, null, '美股')], 1),
  reading: list([cell('ab'.repeat(32), 9, 3, '報告甲')]),
  report_file: list([]),
  search_markets: list([]),
}

const ROUTES = {
  range: RANGE, questions: 120, stopped: 2, llm_truncated: 1, invalid_citation_rows: 3, invalid_citations: 5,
  distributions: [
    { name: 'path', suppressible: true, cells: [cell('corpus_qa', 80, 8), cell('advice_risk', null, null)] },
    { name: 'decided_by', suppressible: false, cells: [cell('llm', 70, 8)] },
    { name: 'llm_model', suppressible: false, cells: [] },
    { name: 'llm_error', suppressible: false, cells: [] },
  ],
}

const QUALITY = {
  range: RANGE, judge_model: 'deepseek-flash', faithfulness_min: 0.9, other_judge_checked: 4,
  weeks: [{ week_start: '2026-10-05', partial: true, questions: 30, checked_all: 12, judge_checked: 10, degraded: 1,
    below_min: 2, avg_score: 0.9123, score_n: 9, likes: 3, dislikes: 1 }],
}

const OPERATIONS = {
  range: RANGE,
  weeks: [{ week_start: '2026-10-05', partial: true, uploads_received: 4, uploads_published: 2, uploads_rejected: 1,
    uploads_infected: 0, uploads_failed: 0, reviews: 6, qa_content_reads: 1 }],
  audit_actions: [{ action: 'review.update', count: 6 }, { action: 'qa_content.read', count: 1 }],
}

function mount(scopes = ['admin', 'analytics.read'], fail: string | null = null) {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input)
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes }))
    if (fail && url.startsWith(fail)) {
      return new Response(JSON.stringify({ detail: '資料庫暫時無法使用，請稍後再試', code: 'db_unavailable' }), { status: 503 })
    }
    const body = url.startsWith('/api/admin/analytics/overview') ? OVERVIEW
      : url.startsWith('/api/admin/analytics/top') ? TOP
        : url.startsWith('/api/admin/analytics/routes') ? ROUTES
          : url.startsWith('/api/admin/analytics/quality') ? QUALITY
            : url.startsWith('/api/admin/analytics/operations') ? OPERATIONS : {}
    return new Response(JSON.stringify(body))
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/analytics']}><AdminAnalyticsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('總覽卡片、資料來源、缺彙總的提示與趨勢圖', async () => {
  mount()
  const overview = await screen.findByRole('region', { name: '總覽' })
  expect(await within(overview).findByLabelText('問答數')).toHaveTextContent('120')
  expect(within(overview).getByLabelText('回答延遲 p50／p95')).toHaveTextContent('1.5 秒／5.2 秒')
  expect(within(overview).getByLabelText('首字時間 p50／p95')).toHaveTextContent('420 ms／—')
  const sources = within(overview).getByRole('list', { name: '資料來源' })
  expect(sources).toHaveTextContent('2026-01-01 ～ 2026-07-09：每晚彙總')
  expect(sources).toHaveTextContent('2026-07-10 ～ 2026-10-07：即時查詢')
  expect(within(overview).getByText(/有 2 天還沒有每晚彙總/)).toBeInTheDocument()
  expect(within(overview).getByRole('img', { name: /每日問答數：3 天，合計 8/ })).toBeInTheDocument()
})

test('k 門檻：被抑制的格子顯示「<3」，開放詞彙只說另有幾項', async () => {
  mount()
  const top = await screen.findByRole('region', { name: '熱門標的與研報' })
  const targets = await within(top).findByRole('list', { name: '問答引用的標的' })
  expect(targets).toHaveTextContent('台積電')
  expect(targets).toHaveTextContent('12')
  expect(within(top).getByText('另有 4 項少於 3 人，不顯示')).toBeInTheDocument()
  expect(within(top).getByText('所有項目都少於 3 人，不顯示')).toBeInTheDocument()   // 研報：全被抑制
  const markets = within(top).getByRole('list', { name: '問答引用的市場' })
  const us = within(markets).getByText('美股').closest('li')!
  expect(within(us).getByText('<3')).toHaveAttribute('title', '少於 3 位使用者，不顯示數字')
  const routes = await screen.findByRole('region', { name: '路由分布' })
  const path = await within(routes).findByRole('list', { name: '路由類別' })
  expect(within(path).getByText('語料問答')).toBeInTheDocument()
  expect(within(within(path).getByText('投資建議風險').closest('li')!).getByText('<3')).toBeInTheDocument()
  expect(routes).toHaveTextContent('含無效引用的回答 3（共 5 處）')
})

test('品質與上傳審核量的週表', async () => {
  mount()
  const quality = await screen.findByRole('region', { name: '品質趨勢（每週）' })
  const row = (await within(quality).findByRole('table', { name: '每週品質' })).querySelector('tbody tr')!
  expect(row).toHaveTextContent('0.912')
  expect(row).toHaveTextContent('（不完整）')
  expect(quality).toHaveTextContent('其他 judge 量的 4 筆不計入分數')
  const ops = await screen.findByRole('region', { name: '上傳與審核量' })
  expect(await within(ops).findByText(/待複核處理/, { selector: 'span' })).toBeInTheDocument()
  expect(within(ops).getByRole('table', { name: '每週上傳與審核' })).toHaveTextContent('6')
})

test('切換範圍時以台北日期送 since；單一區塊失敗只影響那一塊', async () => {
  const fetchMock = mount(['admin', 'analytics.read'], '/api/admin/analytics/quality')
  expect(await screen.findByRole('alert')).toHaveTextContent('品質趨勢載入失敗：資料庫暫時無法使用')
  expect(await screen.findByLabelText('問答數')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('範圍'), { target: { value: '90' } })
  const since = sinceForDays(90)
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) =>
    String(u) === `/api/admin/analytics/overview?since=${since}`)).toBe(true))
  expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/admin/analytics/top?') &&
    String(u).includes('limit=15'))).toBe(true)
})

test('沒有 analytics.read 時不打任何分析 API', async () => {
  const fetchMock = mount(['admin'])
  expect(await screen.findByRole('heading', { name: '需要「使用分析」權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/admin'))).toBe(false)
})

test('格式化工具', () => {
  expect(cellText({ key: 'x', value: null, users: null, suppressed: true }, 3)).toBe('<3')
  expect(cellText({ key: 'x', value: 1234, users: 4, suppressed: false }, 3)).toBe('1,234')
  expect(fmtMs(999)).toBe('999 ms')
  expect(fmtMs(1500)).toBe('1.5 秒')
  expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  // 台北 10/7 00:30 ＝ UTC 10/6 16:30。
  expect(taipeiToday(new Date('2026-10-06T16:30:00Z'))).toBe('2026-10-07')
  expect(sinceForDays(30, '2026-10-07')).toBe('2026-09-08')
})
