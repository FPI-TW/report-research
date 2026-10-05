import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminReviewsPage from './AdminReviewsPage'

afterEach(() => vi.unstubAllGlobals())

test('管理員看到待複核佇列與管理分頁；/api/progress 只取一次、不輪詢', async () => {
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
  expect(screen.getByRole('link', { name: '待複核' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '帳號管理' })).toHaveAttribute('href', '/admin/users')
  expect(fetchMock.mock.calls.filter(([u]) => u === '/api/progress')).toHaveLength(1)
})
