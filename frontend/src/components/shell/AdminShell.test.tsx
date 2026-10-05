import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { AdminShell } from './AdminShell'

afterEach(() => vi.unstubAllGlobals())

function mount(role: 'admin' | 'user', path = '/admin/reviews') {
  const fetchMock = vi.fn(async (url: string) =>
    new Response(JSON.stringify(url === '/api/me' ? { id: 'me', username: 'root', role } : {}), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="reviews" element={<div>待複核內容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('管理員：獨立外殼有管理導覽、回到研報平台與登出，沒有研報側欄', async () => {
  mount('admin')
  expect(await screen.findByText('待複核內容')).toBeInTheDocument()
  const nav = within(screen.getByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /帳號管理/ })).toHaveAttribute('href', '/admin/users')
  expect(nav.getByRole('link', { name: /待複核/ })).toHaveAttribute('aria-current', 'page')
  expect(nav.getByRole('link', { name: /操作紀錄/ })).toHaveAttribute('href', '/admin/audit')
  expect(screen.getByRole('link', { name: /回到研報平台/ })).toHaveAttribute('href', '/search')
  expect(screen.getByRole('button', { name: '登出' }).closest('form')).toHaveAttribute('action', '/logout')
  // 研報平台的導覽與歷史對話不在這裡
  expect(screen.queryByRole('link', { name: /^檢索$/ })).not.toBeInTheDocument()
  expect(screen.queryByText('歷史對話')).not.toBeInTheDocument()
})

test('一般使用者：連管理導覽都不畫，只看到無權限頁，也不打任何管理 API', async () => {
  const fetchMock = mount('user')
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理導覽' })).not.toBeInTheDocument()
  expect(screen.queryByText('待複核內容')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.map(c => c[0])).toEqual(['/api/me'])
})
