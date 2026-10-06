import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'
import { setLocale } from '../../lib/useLocale'

function stubStats(role: 'admin' | 'user' = 'user') {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url === '/api/me'
      ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role }), { status: 200 })
      : new Response(JSON.stringify({
        total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst',
      }), { status: 200 }),
  ))
}

beforeEach(() => { localStorage.clear(); setLocale('zh-Hant') })
afterEach(() => { vi.unstubAllGlobals(); setLocale('zh-Hant'); localStorage.clear() })

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

test('點帳號鈕開選單，顯示登出（原生 form action=/logout）', async () => {
  stubStats()
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const logout = screen.getByRole('button', { name: '登出' })
  expect(logout).toHaveAttribute('type', 'submit')
  expect(logout.closest('form')).toHaveAttribute('action', '/logout')
  expect(screen.getByRole('link', { name: /使用說明/ })).toHaveAttribute('href', '/help')
})

test('語言切換：預設中文 checked，點 English 切換', async () => {
  stubStats()
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const zh = screen.getByRole('radio', { name: '中文' })
  const en = screen.getByRole('radio', { name: 'English' })
  expect(zh).toHaveAttribute('aria-checked', 'true')
  expect(en).toHaveAttribute('aria-checked', 'false')
  fireEvent.click(en)
  expect(en).toHaveAttribute('aria-checked', 'true')
  expect(zh).toHaveAttribute('aria-checked', 'false')
  expect(localStorage.getItem('tf.locale')).toBe('en')
})

test('管理員的帳號選單多一個「管理後台」入口（主導覽刻意不放）', async () => {
  stubStats('admin')
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  expect(await screen.findByRole('link', { name: /管理後台/ })).toHaveAttribute('href', '/admin/users')
})

test('一般使用者的帳號選單沒有管理後台入口', async () => {
  stubStats('user')
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  await screen.findByRole('link', { name: /使用說明/ })
  expect(screen.queryByRole('link', { name: /管理後台/ })).not.toBeInTheDocument()
})

test('每個人的帳號選單都有「帳號安全」，點了開啟兩步驟驗證設定', async () => {
  stubStats('user')
  wrap(<AccountMenu variant="row" />)
  expect(await screen.findByText('analyst')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  fireEvent.click(screen.getByRole('button', { name: /帳號安全/ }))
  expect(await screen.findByRole('dialog', { name: '帳號安全' })).toBeInTheDocument()
})

test('收合側欄（mini）的選單 portal 到 body、以 fixed 定位，不被側欄的窄容器壓扁', async () => {
  stubStats()
  const { container } = wrap(<AccountMenu variant="mini" />)
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const menu = await screen.findByRole('menu')
  expect(container.contains(menu)).toBe(false)
  expect(document.body.contains(menu)).toBe(true)
  expect(menu.style.position).toBe('fixed')
  expect(menu.style.width).toBe('248px')
  expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
})

test('手機分頁列（mobile）同樣 portal 到 body；展開側欄（row）仍在原容器內', async () => {
  stubStats()
  const mobile = wrap(<AccountMenu variant="mobile" />)
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const menu = await screen.findByRole('menu')
  expect(mobile.container.contains(menu)).toBe(false)
  expect(menu.style.position).toBe('fixed')
  mobile.unmount()

  const row = wrap(<AccountMenu variant="row" />)
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  const rowMenu = await screen.findByRole('menu')
  expect(row.container.contains(rowMenu)).toBe(true)
  expect(rowMenu.style.position).toBe('')
})
