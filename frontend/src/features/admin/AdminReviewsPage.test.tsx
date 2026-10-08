import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminReviewsPage from './AdminReviewsPage'

afterEach(() => vi.unstubAllGlobals())

const judgeScaleCalls = (f: { mock: { calls: unknown[][] } }) => f.mock.calls.filter(([u]) => u === '/api/review/judge-scale').length

test('管理員看到待複核頁首與佇列；判定尺取不到（500）也照常；判定尺只取一次、不打 /api/progress', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'u1', username: 'root', role: 'admin' }), { status: 200 })
    if (url.startsWith('/api/review/queue')) {
      return new Response(JSON.stringify({
        kind: 'faithfulness', total: 0, limit: 10, offset: 0, has_more: false, next_offset: null, min_score: 0.9, items: [],
      }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'x' }), { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reviews']}><AdminReviewsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByText('待複核佇列')).toBeInTheDocument()
  expect(await screen.findByText('沒有待複核的項目')).toBeInTheDocument()
  // 切頁導覽在外殼（AdminShell），頁面本身只有標題
  expect(screen.getByRole('heading', { name: '待複核', level: 1 })).toBeInTheDocument()
  await waitFor(() => expect(judgeScaleCalls(fetchMock)).toBe(1))
  // 判定尺只是附註：取不到就不標，不擋佇列；完整管線資料（/api/progress，要 ops.read）不碰
  expect(screen.queryByText(/新量尺/)).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([u]) => u === '/api/progress')).toHaveLength(0)
})

test('判定尺剛換成 DeepSeek → 忠實度分頁標新量尺（資料來自 /api/review/judge-scale）', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'u1', username: 'root', role: 'admin' }), { status: 200 })
    if (url === '/api/review/judge-scale') {
      return new Response(JSON.stringify({
        judge_model: 'deepseek-flash', judge_since: '2026-09-25', other_judge_checked: 6,
      }), { status: 200 })
    }
    if (url.startsWith('/api/review/queue')) {
      return new Response(JSON.stringify({
        kind: 'faithfulness', total: 0, limit: 10, offset: 0, has_more: false, next_offset: null, min_score: 0.9, items: [],
      }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'x' }), { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reviews']}><AdminReviewsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByText(/判定尺 deepseek-flash 是新量尺（自 2026-09-25 起，DeepSeek）/)).toBeInTheDocument()
  expect(judgeScaleCalls(fetchMock)).toBe(1)
  expect(fetchMock.mock.calls.filter(([u]) => u === '/api/progress')).toHaveLength(0)
})

function mountWithMe(me: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify(me), { status: 200 })
    if (url.startsWith('/api/review/queue')) {
      return new Response(JSON.stringify({
        kind: 'faithfulness', total: 1, limit: 10, offset: 0, has_more: false, next_offset: null, min_score: 0.9,
        items: [{ qa_id: 'qa1', created_at: '2026-10-01T00:00:00Z', faithfulness_score: 0.2, asker_code: 'abcd1234' }],
      }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'x' }), { status: 500 })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reviews']}><AdminReviewsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('「查看內容」只在 /api/me 的 scopes 含 qa_content.read 時出現（顯示層）', async () => {
  mountWithMe({ id: 'u1', username: 'qa', role: 'admin', scopes: ['admin', 'review.manage', 'qa_content.read'] })
  expect(await screen.findByRole('button', { name: '查看內容' })).toBeInTheDocument()
})

test('一般管理員（沒有 qa_content.read）看不到「查看內容」', async () => {
  mountWithMe({ id: 'u1', username: 'root', role: 'admin', scopes: ['admin', 'review.manage'] })
  expect(await screen.findByText('問答 qa1')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '查看內容' })).not.toBeInTheDocument()
})
