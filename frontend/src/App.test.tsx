import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { routes } from './App'

afterEach(() => vi.unstubAllGlobals())

test('/search 落在檢索頁且側欄可見', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/conversations')
      ? new Response(JSON.stringify([]), { status: 200 })
      : new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 }),
  ))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/search'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  // SearchPage 為 lazy chunk（今引入完整檢索元件樹），首次動態載入可能超過預設 1000ms timeout。
  // 放寬到 15s 而非 5s：本測試驗的是「路由有掛上」而非載入效能，而動態 import 的耗時
  // 受測試檔總數與檔案系統影響（WSL 掛載的 /mnt/c 上，全套並行時 5s 會穩定撞線，單跑則
  // 一秒內完成）。放寬門檻不改變本測試驗證的內容。
  //
  // **兩個 timeout 都要放寬，只放寬 findBy 的那個沒有用**：下面第三個引數是 vitest 的
  // testTimeout，它才是決定整個測試何時被砍掉的那一個；vitest.config.ts 沒有設，故預設
  // 5000ms。少了它，findBy 的 15000 永遠等不到——測試會在第 5 秒被砍，錯誤訊息是
  // 「Test timed out in 5000ms」而不是找不到元素，看起來還很像是產品壞了。
  expect(await screen.findByLabelText('搜尋研報', {}, { timeout: 15000 })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '廷豐智能研報' })).toBeInTheDocument()
  // 側欄預設收合：只有圖示軌，展開鈕可按
  expect(screen.getByRole('button', { name: '展開側欄' })).toBeInTheDocument()
}, 15000)

function stubAs(role: 'admin' | 'user') {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes('/api/conversations')) return new Response(JSON.stringify([]), { status: 200 })
    if (url.includes('/api/me')) return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role }), { status: 200 })
    if (url.startsWith('/api/admin/users')) return new Response(JSON.stringify({ items: [] }), { status: 200 })
    if (url.startsWith('/api/admin/audit')) {
      return new Response(JSON.stringify({ total: 0, limit: 20, offset: 0, has_more: false, next_offset: null, items: [] }), { status: 200 })
    }
    return new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('/admin 導向帳號管理（管理員）', async () => {
  stubAs('admin')
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { level: 2, name: '帳號清單' }, { timeout: 15000 })).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/admin/users')
}, 15000)

test('一般使用者直接開 /admin/reviews：無權限頁，不打待複核與管理 API', async () => {
  const fetchMock = stubAs('user')
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/reviews'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { name: '需要管理員權限' }, { timeout: 15000 })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/review') || String(u).startsWith('/api/admin'))).toBe(false)
}, 15000)

test('/admin/operations 導向維運總覽（有 ops.read 的管理員；代理不可用時顯示降級說明）', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/api/me')) {
      return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', 'ops.read'] }), { status: 200 })
    }
    if (url.startsWith('/api/admin/ops')) {
      return new Response(JSON.stringify({ detail: '維運代理不可用', code: 'ops_agent_unavailable' }), { status: 503 })
    }
    return new Response(JSON.stringify({}), { status: 200 })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/operations'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' }, { timeout: 15000 })).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/admin/operations/overview')
}, 15000)

test.each([
  ['/admin/analytics', '使用分析', 'analytics.read'],
  ['/admin/flags', '功能旗標', 'ops.read'],
])('Admin v2 佔位頁 %s：有 scope 時顯示「建置中」、不打任何管理 API', async (path, title, scope) => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes('/api/me')) {
      return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', scope] }), { status: 200 })
    }
    return new Response(JSON.stringify({}), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { level: 1, name: title }, { timeout: 15000 })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 2, name: '建置中' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith('/api/admin'))).toBe(false)
}, 15000)

test('配額頁（Quota lane）：/admin/quota 有 accounts.manage 時載入配額 API', async () => {
  const fetchMock = vi.fn(async (url: string) =>
    url.includes('/api/me')
      ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', 'accounts.manage'] }), { status: 200 })
      : new Response(JSON.stringify({ detail: '測試不回資料', code: 'x' }), { status: 503 }))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/quota'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { level: 1, name: '配額' }, { timeout: 15000 })).toBeInTheDocument()
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/admin/quota')).toBe(true))
}, 15000)

test('Admin v2 佔位頁：沒有對應 scope 時只顯示需要的權限', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/me')
      ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin'] }), { status: 200 })
      : new Response(JSON.stringify({}), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/analytics'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { name: '需要「使用分析」權限' }, { timeout: 15000 })).toBeInTheDocument()
}, 15000)

test('維運 → 資料庫分頁（Admin v2 佔位）', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    url.includes('/api/me')
      ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', 'ops.read'] }), { status: 200 })
      : new Response(JSON.stringify({}), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/operations/database'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  expect(await screen.findByRole('heading', { level: 2, name: '資料庫' }, { timeout: 15000 })).toBeInTheDocument()
}, 15000)
