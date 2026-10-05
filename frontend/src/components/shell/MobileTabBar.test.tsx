import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { MobileTabBar } from './MobileTabBar'

afterEach(() => vi.unstubAllGlobals())

function mount(role: 'admin' | 'user', path = '/search') {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('/api/me')
      ? { id: 'u1', username: 'analyst', role }
      : { total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' },
  ), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><MobileTabBar /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('五格導覽含觀點與帳號', () => {
  mount('user')
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /觀點/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})

test('一般使用者沒有「管理」格', async () => {
  mount('user')
  await new Promise(r => setTimeout(r, 0))
  expect(screen.queryByRole('link', { name: /管理/ })).not.toBeInTheDocument()
})

test('管理員多一格「管理」，在 /admin/* 底下都算目前頁', async () => {
  mount('admin', '/admin/reviews')
  const link = await screen.findByRole('link', { name: /管理/ })
  expect(link).toHaveAttribute('href', '/admin/users')
  expect(link).toHaveAttribute('aria-current', 'page')
})
