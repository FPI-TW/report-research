import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { MobileTabBar } from './MobileTabBar'

afterEach(() => vi.unstubAllGlobals())

test('五格導覽含觀點與帳號', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
  }), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}><MobileTabBar /></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /觀點/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})
