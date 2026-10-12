import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { SideRail } from './SideRail'

afterEach(() => vi.unstubAllGlobals())

function renderRail(collapsed: boolean) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/conversations')
      ? new Response(JSON.stringify([]), { status: 200 })
      : url === '/api/me'
      ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin' }), { status: 200 })
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

test('展開態：圖示軌導覽常駐，右側面板顯示站名與歷史對話', () => {
  renderRail(false)
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByText('歷史對話')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /檢索/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /觀點/ })).toBeInTheDocument()
  // 導入監控已搬進管理後台，主平台導覽不再有入口
  expect(screen.queryByRole('link', { name: /監控/ })).not.toBeInTheDocument()
})

test('收合態：圖示軌導覽仍可用，歷史對話面板移出無障礙樹', () => {
  renderRail(true)
  // 面板恆掛載（供寬度過渡動畫），但收合時 aria-hidden＋inert → 不在無障礙樹
  expect(screen.getByRole('button', { name: '展開側欄' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /問答/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '收合側欄' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /新對話/ })).not.toBeInTheDocument()
})

test('展開態：收合鈕在面板標頭，圖示軌不再重複一顆展開鈕', () => {
  renderRail(false)
  expect(screen.getByRole('button', { name: '收合側欄' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '展開側欄' })).not.toBeInTheDocument()
})

test('搜尋收成標頭的單一 icon：點了才出現搜尋框，再點一次收起', () => {
  renderRail(false)
  expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
  const btn = screen.getByRole('button', { name: '搜尋對話' })
  fireEvent.click(btn)
  expect(btn).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('searchbox', { name: '搜尋歷史對話' })).toHaveFocus()
  fireEvent.click(btn)
  expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
})

test('管理後台與研報平台分開：即使是管理員，側欄也沒有「管理」入口', async () => {
  renderRail(false)
  // 帳號在圖示軌是頭像鈕，名稱落在 title：等 /api/me 回來再斷言，確保管理員身分已生效
  await screen.findByTitle('analyst')
  expect(screen.queryByRole('link', { name: /管理/ })).not.toBeInTheDocument()
})
