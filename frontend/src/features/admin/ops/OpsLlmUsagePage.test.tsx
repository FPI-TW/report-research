import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsLlmUsagePage from './OpsLlmUsagePage'

afterEach(() => vi.unstubAllGlobals())

const totals = (calls: number, over: Record<string, unknown> = {}) => ({
  calls, failures: 0, prompt_hit_tokens: 100, prompt_miss_tokens: 900, completion_tokens: 200, reasoning_tokens: 0,
  calls_without_tokens: 0, total_ms: calls * 2000, cost: null, ...over,
})

const BATCH = {
  since: '2026-09-06T02:00:00Z', until: '2026-10-06T02:00:00Z', timezone: 'Asia/Taipei',
  source: { exists: true, size_bytes: 4096, scanned_bytes: 4096, truncated: false, lines_scanned: 11, lines_invalid: 0,
    lines_in_range: 11, earliest_ts: '2026-09-20T00:00:00Z', latest_ts: '2026-10-06T01:00:00Z' },
  totals: totals(11),
  by_day: [{ day: '2026-10-06', ...totals(11) }],
  by_task: [{ task: 'summary', ...totals(11) }],
  by_model: [{ model: 'deepseek-flash', ...totals(11) }],
  rows: [], rows_truncated: false, cost_available: false,
}

const ONLINE = {
  available: true, error: null, since_day: '2026-09-06', until_day: '2026-10-06',
  totals: totals(7, { failures: 1 }),
  by_day: [{ day: '2026-10-05', ...totals(3) }, { day: '2026-10-06', ...totals(4) }],
  by_task: [{ task: 'ask_answer', ...totals(5) }, { task: 'faithfulness', ...totals(2) }],
  by_model: [{ model: 'deepseek-flash', ...totals(7) }],
  rows: [], rows_truncated: false, attributed_users: 3, unattributed_calls: 2, cost_available: false,
}

function mount(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'ops.read'] }))
    return new Response(JSON.stringify(body))
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/operations/llm-usage']}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="llm-usage" element={<OpsLlmUsagePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('合計、批次與線上兩段各自的表格；線上顯示歸因人數與未歸因次數', async () => {
  mount({ ...BATCH, online: ONLINE, combined: { totals: totals(18), by_day: [], cost_available: false } })
  expect(await screen.findByRole('table', { name: '線上・依任務' })).toBeInTheDocument()
  expect(within(screen.getByLabelText('合計呼叫次數')).getByText('18')).toBeInTheDocument()
  expect(within(screen.getByLabelText('呼叫次數')).getByText('11')).toBeInTheDocument()
  expect(within(screen.getByLabelText('線上呼叫次數')).getByText('7')).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '依任務' })).toBeInTheDocument()
  const tasks = within(screen.getByRole('table', { name: '線上・依任務' })).getAllByRole('row').slice(1)
    .map(r => (r as HTMLTableRowElement).cells[0].textContent)
  expect(tasks).toEqual(['ask_answer', 'faithfulness'])
  const days = within(screen.getByRole('table', { name: '線上・依日期' })).getAllByRole('row').slice(1)
    .map(r => (r as HTMLTableRowElement).cells[0].textContent)
  expect(days).toEqual(['2026-10-06', '2026-10-05'])
  expect(screen.getByText(/歸因到/)).toHaveTextContent('歸因到 3 位使用者')
  expect(screen.getByText(/未歸因/)).toHaveTextContent('未歸因 2 次')
})

test('線上讀不到時只在線上那段警示，批次照常', async () => {
  mount({ ...BATCH, online: { ...ONLINE, available: false, error: '線上用量暫時讀不到（OperationalError）',
    totals: totals(0), by_day: [], by_task: [], by_model: [] }, combined: { totals: totals(11), by_day: [], cost_available: false } })
  expect(await screen.findByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(screen.getByText(/線上用量暫時讀不到/)).toBeInTheDocument()
  expect(screen.queryByLabelText('合計呼叫次數')).not.toBeInTheDocument()
})

test('批次檔案不存在時仍顯示線上用量', async () => {
  mount({ ...BATCH, source: { ...BATCH.source, exists: false }, totals: totals(0), by_day: [], by_task: [], by_model: [],
    online: ONLINE, combined: { totals: totals(7), by_day: [], cost_available: false } })
  expect(await screen.findByText(/還沒有用量紀錄/)).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '線上・依任務' })).toBeInTheDocument()
})

test('線上這段沒有呼叫時說明', async () => {
  mount({ ...BATCH, online: { ...ONLINE, totals: totals(0), by_day: [], by_task: [], by_model: [], attributed_users: 0,
    unattributed_calls: 0 }, combined: { totals: totals(11), by_day: [], cost_available: false } })
  expect(await screen.findByText('這段期間沒有任何線上 LLM 呼叫。')).toBeInTheDocument()
})
