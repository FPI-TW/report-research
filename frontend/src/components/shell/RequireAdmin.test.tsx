import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { RequireAdmin } from './RequireAdmin'

afterEach(() => vi.unstubAllGlobals())

function mount(me: unknown, status = 200) {
  const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify(me), { status }))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RequireAdmin><div>管理內容</div></RequireAdmin>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('管理員看得到內容', async () => {
  mount({ id: 'u1', username: 'root', role: 'admin' })
  expect(await screen.findByText('管理內容')).toBeInTheDocument()
})

test('一般使用者看到「需要管理員權限」，且只打了 /api/me（內容不掛載就不會取數）', async () => {
  const fetchMock = mount({ id: 'u2', username: 'alice', role: 'user' })
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(screen.queryByText('管理內容')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.map(c => c[0])).toEqual(['/api/me'])
})

test('身分查不到（後端錯誤）當成非管理員，不白屏', async () => {
  mount({ detail: 'boom' }, 500)
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
})

test('身分回來之前只畫骨架', () => {
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><RequireAdmin><div>管理內容</div></RequireAdmin></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByLabelText('確認權限中')).toHaveAttribute('aria-busy', 'true')
  expect(screen.queryByText('管理內容')).not.toBeInTheDocument()
})
