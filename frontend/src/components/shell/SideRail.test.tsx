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

test('展開態顯示站名與四導覽 label', () => {
  renderRail(false)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /觀點雷達/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /監控/ })).toBeInTheDocument()
})

test('收合態：迷你軌可存取、完整態內容移出無障礙樹', () => {
  renderRail(true)
  // 兩態層皆恆掛載（供跨態寬度／淡入淡出動畫），但完整態層 aria-hidden＋inert → 不在無障礙樹
  expect(screen.getByRole('button', { name: '展開側欄' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '收合側欄' })).not.toBeInTheDocument()
})

test('展開態：完整態可存取、迷你軌移出無障礙樹', () => {
  renderRail(false)
  expect(screen.getByRole('button', { name: '收合側欄' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '展開側欄' })).not.toBeInTheDocument()
})
