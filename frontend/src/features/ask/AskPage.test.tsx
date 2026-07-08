import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from '../../lib/readSSE'

const streamAsk = vi.fn()
vi.mock('../../lib/askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: vi.fn(),
  getConversation: vi.fn(async () => []),
  sendFeedback: vi.fn(async () => {}),
}))
import AskPage from './AskPage'

afterEach(() => vi.clearAllMocks())

function immediate(events: RawSSEEvent[]) {
  return (async function* () { for (const e of events) yield e })()
}
function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/ask']}><AskPage /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('送出問題→串流答案顯示、資料來源鈕出現', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }] },
    { event: 'status', data: { stage: 'generating', thinking_ms: 3000 } },
    { event: 'token', data: '這是答案 [1]' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: ['r1'] } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '台積電評價' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByText('台積電評價')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(await screen.findByRole('button', { name: '資料來源 1' })).toBeInTheDocument()
})

test('離題→Callout warning', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [] },
    { event: 'notice', data: '無法回答此問題' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '今天天氣' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByRole('alert')).toHaveTextContent('無法回答此問題')
})
