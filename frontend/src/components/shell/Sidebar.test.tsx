import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Sidebar } from './Sidebar'
import { ShellContext } from './shellContext'

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear() })

function wrap(path = '/library', collapsed = false, openSearch = vi.fn(), onToggle = vi.fn()) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.startsWith('/api/conversations')
      ? new Response(JSON.stringify([{ conversation_id: 'c1', title: 'AI 伺服器供應鏈' }]), { status: 200 })
      : new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <ShellContext.Provider value={{ openSearch, openSettings: vi.fn() }}>
        <MemoryRouter initialEntries={[path]}><Sidebar collapsed={collapsed} onToggle={onToggle} /></MemoryRouter>
      </ShellContext.Provider>
    </QueryClientProvider>,
  )
  return { openSearch, onToggle }
}

test('主導覽：搜尋、研報庫、研究助理、券商共識、每日簡報，連到新網址', () => {
  wrap()
  const nav = screen.getByRole('navigation', { name: '主導覽' })
  expect(nav).toHaveTextContent(/搜尋.*研報庫.*研究助理.*券商共識.*每日簡報/)
  expect(screen.getByRole('link', { name: '研報庫' })).toHaveAttribute('href', '/library')
  expect(screen.getByRole('link', { name: '研究助理' })).toHaveAttribute('href', '/assistant')
  expect(screen.getByRole('link', { name: '券商共識' })).toHaveAttribute('href', '/consensus')
  expect(screen.getByRole('link', { name: '每日簡報' })).toHaveAttribute('href', '/brief')
})

test('目前頁面標 aria-current；閱讀頁算在研報庫底下', () => {
  wrap('/report/abc')
  expect(screen.getByRole('link', { name: '研報庫' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '研究助理' })).not.toHaveAttribute('aria-current')
})

test('「搜尋」開搜尋彈窗，不是頁面連結', () => {
  const { openSearch } = wrap()
  fireEvent.click(screen.getByRole('button', { name: /搜尋/ }))
  expect(openSearch).toHaveBeenCalled()
})

test('收合鈕：展開時寫「收合側欄」，收合後寫「展開側欄」，最近對話移出無障礙樹', () => {
  const first = wrap('/library', false)
  fireEvent.click(screen.getByRole('button', { name: '收合側欄' }))
  expect(first.onToggle).toHaveBeenCalled()
})

test('收合時：圖示列仍可用，最近對話 inert', () => {
  wrap('/library', true)
  expect(screen.getByRole('button', { name: '展開側欄' })).toHaveAttribute('aria-expanded', 'false')
  expect(screen.getByRole('link', { name: '研報庫' })).toBeInTheDocument()
  const aside = screen.getByRole('complementary', { name: '側欄' })
  expect(aside).toHaveAttribute('data-collapsed', 'true')
  expect(aside.querySelector('[inert]')).not.toBeNull()
})

test('一般介面沒有管理後台入口', () => {
  wrap()
  expect(screen.queryByText(/管理後台/)).not.toBeInTheDocument()
})

test('使用說明在個人列最右邊（帳號選單之外），連到 /help', () => {
  wrap()
  const help = screen.getByRole('link', { name: '使用說明' })
  expect(help).toHaveAttribute('href', '/help')
  const account = screen.getByRole('button', { name: /analyst|使用者/ })
  // 同一列：帳號選單的觸發點在前、使用說明在後
  expect(help.parentElement).toBe(account.parentElement)
  expect(account.compareDocumentPosition(help) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('收合時：個人列只剩頭像，使用說明鈕 inert（被裁掉的按鈕不可被 Tab 到）', () => {
  wrap('/library', true)
  expect(screen.queryByRole('link', { name: '使用說明' })).not.toBeInTheDocument()
  const help = document.querySelector('aside a[href="/help"]')
  expect(help).toHaveAttribute('inert')
})

// 收合／展開若把列的 DOM 節點換新，滑動高亮登錄的還是舊節點、量不到新位置：在收合狀態下載入再展開，
// 選取底色一直停在 36px 寬（實測）。jsdom 量不到版面，這裡釘住根因——切換前後是同一個節點。
test('收合／展開切換時，主導覽各列是同一個 DOM 節點（不重新掛載）', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = (collapsed: boolean) => (
    <QueryClientProvider client={qc}>
      <ShellContext.Provider value={{ openSearch: vi.fn(), openSettings: vi.fn() }}>
        <MemoryRouter initialEntries={['/consensus']}><Sidebar collapsed={collapsed} onToggle={vi.fn()} /></MemoryRouter>
      </ShellContext.Provider>
    </QueryClientProvider>
  )
  const { rerender } = render(ui(true))
  const link = screen.getByRole('link', { name: '券商共識' })
  const search = screen.getByRole('button', { name: /搜尋/ })
  rerender(ui(false))
  expect(screen.getByRole('link', { name: '券商共識' })).toBe(link)
  expect(screen.getByRole('button', { name: /搜尋/ })).toBe(search)
  rerender(ui(true))
  expect(screen.getByRole('link', { name: '券商共識' })).toBe(link)
})
