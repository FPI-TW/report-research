import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { requestJSON } from '../../lib/api'
import { AdminShell } from './AdminShell'

afterEach(() => vi.unstubAllGlobals())

function mount(role: 'admin' | 'user', path = '/admin/reviews', scopes?: string[]) {
  const fetchMock = vi.fn(async (url: string) =>
    new Response(JSON.stringify(url === '/api/me' ? { id: 'me', username: 'root', role, scopes } : {}), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="reviews" element={<div>待複核內容</div>} />
            <Route path="*" element={<div>其他管理頁</div>} />
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

test('依 scope 顯示入口：有 reports.manage／ops.read 才看得到「研報管理」「維運」', async () => {
  mount('admin', '/admin/reviews', ['admin', 'reports.manage', 'ops.read'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /研報管理/ })).toHaveAttribute('href', '/admin/reports')
  expect(nav.getByRole('link', { name: /維運/ })).toHaveAttribute('href', '/admin/operations')
  expect(nav.getByRole('link', { name: /上傳研報/ })).toHaveAttribute('href', '/admin/uploads')
})

test('上傳詳情頁裡，「上傳研報」入口維持選取狀態，「研報管理」不會跟著亮', async () => {
  mount('admin', '/admin/uploads/u-1', ['admin', 'reports.manage'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /上傳研報/ })).toHaveAttribute('aria-current', 'page')
  expect(nav.getByRole('link', { name: /研報管理/ })).not.toHaveAttribute('aria-current')
})

test('維運子頁裡，「維運」入口維持選取狀態', async () => {
  mount('admin', '/admin/operations/logs', ['admin', 'ops.read'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /維運/ })).toHaveAttribute('aria-current', 'page')
})

test('Admin v2 入口依 scope 顯示：使用分析、安全、配額、功能旗標', async () => {
  mount('admin', '/admin/reviews', ['admin', 'analytics.read', 'audit.read', 'accounts.manage', 'ops.read'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /使用分析/ })).toHaveAttribute('href', '/admin/analytics')
  expect(nav.getByRole('link', { name: /安全/ })).toHaveAttribute('href', '/admin/security')
  expect(nav.getByRole('link', { name: /配額/ })).toHaveAttribute('href', '/admin/quota')
  expect(nav.getByRole('link', { name: /功能旗標/ })).toHaveAttribute('href', '/admin/flags')
})

test('沒有對應 scope 的管理員：不顯示研報管理與維運入口', async () => {
  mount('admin', '/admin/reviews', ['admin'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.queryByRole('link', { name: /研報管理/ })).not.toBeInTheDocument()
  expect(nav.queryByRole('link', { name: /上傳研報/ })).not.toBeInTheDocument()
  expect(nav.queryByRole('link', { name: /維運/ })).not.toBeInTheDocument()
  expect(nav.queryByRole('link', { name: /使用分析/ })).not.toBeInTheDocument()
  expect(nav.queryByRole('link', { name: /功能旗標/ })).not.toBeInTheDocument()
  expect(nav.getByRole('link', { name: /帳號管理/ })).toBeInTheDocument()
})

test('管理員 TOTP 強制：/api/me 說 mfa_enrollment_required 時，導覽與內容換成 TOTP 設定頁', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin'],
        totp_enabled: false, mfa_enrollment_required: true }), { status: 200 })
    }
    if (url === '/api/me/totp') return new Response(JSON.stringify({ enabled: false, pending: false }), { status: 200 })
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reviews']}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="reviews" element={<div>待複核內容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('heading', { name: '請先開啟兩步驟驗證' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理導覽' })).not.toBeInTheDocument()
  expect(screen.queryByText('待複核內容')).not.toBeInTheDocument()
  // 登出與回到研報平台仍在
  expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '設定兩步驟驗證' }))
  await waitFor(() => expect(fetchMock.mock.calls.map(c => c[0])).toContain('/api/me/totp'))
  // 不打任何管理 API
  expect(fetchMock.mock.calls.some(c => String(c[0]).startsWith('/api/admin/'))).toBe(false)
})

function ProbePage() {
  const q = useQuery({ queryKey: ['probe'], queryFn: () => requestJSON('/api/admin/probe', z.object({})), retry: false })
  return <div>{q.isError ? '探測失敗' : '探測內容'}</div>
}

test('任何管理 API 回 403 mfa_enrollment_required 時，立刻重取 /api/me 並切到 TOTP 設定頁', async () => {
  let meCalls = 0
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      meCalls += 1
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin'],
        mfa_enrollment_required: meCalls > 1 }), { status: 200 })
    }
    if (url === '/api/admin/probe') {
      return new Response(JSON.stringify({ detail: '管理員必須先開啟兩步驟驗證', code: 'mfa_enrollment_required' }),
        { status: 403 })
    }
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/probe']}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="probe" element={<ProbePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('heading', { name: '請先開啟兩步驟驗證' })).toBeInTheDocument()
  expect(meCalls).toBeGreaterThanOrEqual(2)
})
