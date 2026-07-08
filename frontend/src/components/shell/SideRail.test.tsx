import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { SideRail } from './SideRail'

afterEach(() => vi.unstubAllGlobals())

function renderRail(collapsed: boolean) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/conversations')
      ? new Response(JSON.stringify([]), { status: 200 })
      : new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 }),
  ))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/search']}>
        <SideRail collapsed={collapsed} onToggle={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('展開態顯示站名與三導覽 label', () => {
  renderRail(false)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})

test('收合態不顯示站名文字（僅 glyph）', () => {
  renderRail(true)
  expect(screen.queryByText('廷豐智能研報')).not.toBeInTheDocument()
})
