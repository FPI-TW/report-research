import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { routes } from '../../../App'

afterEach(() => vi.unstubAllGlobals())

// 路由表是 lazy chunk：首次動態載入在全套並行時可能超過預設的 timeout（同 App.test.tsx 的說明），兩個 timeout 都放寬。
test('舊的上傳詳情路徑導向清單頁，id 放進 ?upload=，其他 query 原樣保留', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.includes('/api/me')
    ? new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', 'reports.manage'] }), { status: 200 })
    : new Response(JSON.stringify({ detail: 'not found', code: 'not_found' }), { status: 404 }))))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/uploads/u-1?tab=draft'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  await waitFor(
    () => expect(router.state.location.pathname + router.state.location.search).toBe('/admin/uploads?tab=draft&upload=u-1'),
    { timeout: 15000 },
  )
}, 15000)
