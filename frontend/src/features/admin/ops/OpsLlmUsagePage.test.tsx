import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
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

const firstCells = (name: string) => within(screen.getByRole('table', { name })).getAllByRole('row').slice(1)
  .map(r => (r as HTMLTableRowElement).cells[0].textContent)

test('合計、批次與線上兩段各自的表格；線上顯示歸因人數與未歸因次數', async () => {
  mount({ ...BATCH, online: ONLINE, combined: { totals: totals(18), by_day: [{ day: '2026-10-05', ...totals(3) }, { day: '2026-10-06', ...totals(15) }], cost_available: false } })
  expect(await screen.findByRole('table', { name: '線上・依任務' })).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '合計呼叫次數' })).getByText('18')).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '呼叫次數' })).getByText('11')).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '線上呼叫次數' })).getByText('7')).toBeInTheDocument()
  // 三組磚畫面上的標籤都是「呼叫次數」，分得開靠的是所在的段落標題
  expect(screen.getByRole('heading', { name: '合計（批次＋線上）' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '批次' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '線上（問答、忠實度抽查）' })).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(firstCells('線上・依任務')).toEqual(['ask_answer', 'faithfulness'])
  // 批次與線上各有自己的分頁籤，切一邊不動另一邊
  fireEvent.click(within(screen.getByRole('tablist', { name: '線上・用量明細' })).getByRole('tab', { name: '依日期' }))
  expect(firstCells('線上・依日期')).toEqual(['2026-10-06', '2026-10-05'])
  expect(screen.getByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(screen.getByText(/歸因到/)).toHaveTextContent('歸因到 3 位使用者')
  expect(screen.getByText(/未歸因/)).toHaveTextContent('未歸因 2 次')
  // 線上讀得到：每日呼叫畫批次＋線上的合計
  const daily = within(screen.getByRole('region', { name: '每日呼叫' }))
  expect(daily.getByText('批次＋線上')).toBeInTheDocument()
  expect(daily.getByTitle('2026-10-06・15 次')).toBeInTheDocument()
  expect(daily.getByTitle('2026-10-04・0 次')).toBeInTheDocument()
})

test('線上讀不到時只在線上那段警示，批次照常', async () => {
  mount({ ...BATCH, online: { ...ONLINE, available: false, error: '線上用量暫時讀不到（OperationalError）',
    totals: totals(0), by_day: [], by_task: [], by_model: [] }, combined: { totals: totals(11), by_day: [], cost_available: false } })
  expect(await screen.findByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('線上用量暫時讀不到（OperationalError）（批次用量不受影響）')
  expect(screen.queryByRole('group', { name: '合計呼叫次數' })).not.toBeInTheDocument()
  // 線上讀不到：每日呼叫只畫批次，並標明
  const daily = within(screen.getByRole('region', { name: '每日呼叫' }))
  expect(daily.getByText('只有批次')).toBeInTheDocument()
  expect(daily.getByTitle('2026-10-06・11 次')).toBeInTheDocument()
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

test('後端帶費用時多一塊費用磚與費用欄；沒帶的列顯示 —', async () => {
  mount({ ...BATCH, cost_available: true, totals: totals(11, { cost: 1.23456 }),
    by_task: [{ task: 'summary', ...totals(8, { cost: 1.23456 }) }, { task: 'title', ...totals(3) }] })
  const table = within(await screen.findByRole('table', { name: '依任務' }))
  expect(table.getByRole('columnheader', { name: '費用' })).toBeInTheDocument()
  const rows = table.getAllByRole('row').slice(1)
  expect(rows[0]).toHaveTextContent('1.2346')
  expect((rows[1] as HTMLTableRowElement).cells[8]).toHaveTextContent('—')
  expect(screen.getByRole('group', { name: '費用' })).toHaveTextContent('1.2346')
  // 沒有 online 欄位的舊後端：線上那段說明，不當成錯誤
  expect(screen.getByText('後端沒有回傳線上用量。')).toBeInTheDocument()
})
