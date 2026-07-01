import { test, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { routes } from './App'

const renderAt = (path: string) => {
  const router = createMemoryRouter(routes, { initialEntries: [path], basename: '/app' })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MantineProvider>,
  )
  return { router, ...view }
}

test('/app 落地重導向到 /app/search（顯示導覽，非 stub）', async () => {
  // basename 為 '/app'，MemoryRouter 的 initialEntries 需為完整路徑（含 basename）
  // 才能被 stripBasename 匹配；純 '/' 不含 basename 前綴會導致 router 不匹配任何路由。
  const { router } = renderAt('/app')
  // 導覽恆在 → 品牌可見；且不再出現舊 stub 文案
  expect(await screen.findByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
  await waitFor(() => expect(router.state.location.pathname).toBe('/app/search'))
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('aria-current', 'page')
  expect(screen.queryByText('SPA 地基已就緒。')).not.toBeInTheDocument()
})
