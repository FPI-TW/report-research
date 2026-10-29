import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AccountMenu } from './AccountMenu'
import { ShellContext } from './shellContext'

const STATS = { total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function stub(role: 'admin' | 'user' = 'user', status: { status: string; message: string } = { status: 'ok', message: '系統運作正常' }) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role }), { status: 200 })
    if (url === '/api/status') return new Response(JSON.stringify(status), { status: 200 })
    return new Response(JSON.stringify(STATS), { status: 200 })
  }))
}

function wrap(openSettings = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <ShellContext.Provider value={{ openSearch: vi.fn(), openSettings }}>
        <MemoryRouter initialEntries={['/library']}>
          <Routes>
            <Route path="/library" element={<AccountMenu variant="sidebar" />} />
            <Route path="/help" element={<div>說明頁</div>} />
          </Routes>
        </MemoryRouter>
      </ShellContext.Provider>
    </QueryClientProvider>,
  )
  return openSettings
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('analyst')
  await user.click(screen.getByRole('button', { name: /analyst/ }))
  return screen.findByRole('menu')
}

test('個人列顯示名稱與角色；選單只有設定、使用說明、登出', async () => {
  stub('user')
  const user = userEvent.setup()
  wrap()
  expect(await screen.findByText('使用者')).toBeInTheDocument()
  await openMenu(user)
  expect(screen.getAllByRole('menuitem')).toHaveLength(3)
  for (const name of ['設定', '使用說明', '登出']) expect(screen.getByRole('menuitem', { name })).toBeInTheDocument()
})

test('管理員也看不到「管理後台」入口（避免資安疑慮，管理員直接輸入網址）', async () => {
  stub('admin')
  const user = userEvent.setup()
  wrap()
  expect(await screen.findByText('管理員')).toBeInTheDocument()
  await openMenu(user)
  expect(screen.queryByText(/管理後台/)).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /管理/ })).not.toBeInTheDocument()
})

test('設定：開啟設定彈窗的「帳號」分頁', async () => {
  stub()
  const user = userEvent.setup()
  const openSettings = wrap()
  await openMenu(user)
  await user.click(screen.getByRole('menuitem', { name: '設定' }))
  expect(openSettings).toHaveBeenCalledWith('account')
})

test('使用說明：導到 /help', async () => {
  stub()
  const user = userEvent.setup()
  wrap()
  await openMenu(user)
  await user.click(screen.getByRole('menuitem', { name: '使用說明' }))
  expect(await screen.findByText('說明頁')).toBeInTheDocument()
})

test('登出：送出 POST /logout 的表單', async () => {
  stub()
  const user = userEvent.setup()
  const submit = vi.spyOn(HTMLFormElement.prototype, 'requestSubmit').mockImplementation(function (this: HTMLFormElement) {
    expect(this.getAttribute('action')).toBe('/logout')
    expect(this.getAttribute('method')).toBe('post')
  })
  wrap()
  await openMenu(user)
  await user.click(screen.getByRole('menuitem', { name: '登出' }))
  expect(submit).toHaveBeenCalledTimes(1)
})

test('系統正常時不顯示任何狀態；部分異常時頭像 title 寫一句', async () => {
  stub('user', { status: 'degraded', message: '部分核心服務異常' })
  wrap()
  await waitFor(() => expect(screen.getByRole('button', { name: /analyst/ })).toHaveAttribute('title', 'analyst（系統狀態：部分異常）'))
})

test('系統正常：頭像沒有 title 提示', async () => {
  stub('user')
  wrap()
  await screen.findByText('analyst')
  expect(screen.getByRole('button', { name: /analyst/ })).not.toHaveAttribute('title')
})
