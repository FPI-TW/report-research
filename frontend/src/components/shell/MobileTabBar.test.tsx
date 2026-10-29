import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { MobileTabBar } from './MobileTabBar'

afterEach(() => vi.unstubAllGlobals())

test('五格導覽：四個頁面與帳號', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
  }), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/library']}><MobileTabBar /></MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByRole('link', { name: /研報庫/ })).toHaveAttribute('href', '/library')
  expect(screen.getByRole('link', { name: /研究助理/ })).toHaveAttribute('href', '/assistant')
  expect(screen.getByRole('link', { name: /券商共識/ })).toHaveAttribute('href', '/consensus')
  expect(screen.getByRole('link', { name: /每日簡報/ })).toHaveAttribute('href', '/brief')
  expect(screen.getByRole('button', { name: /帳號選單/ })).toBeInTheDocument()
  // 導入監控已搬進管理後台，主平台導覽不再有入口
  expect(screen.queryByRole('link', { name: /監控/ })).not.toBeInTheDocument()
})
