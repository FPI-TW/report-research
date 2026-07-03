import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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
  expect(await screen.findByLabelText('搜尋研報', {}, { timeout: 5000 })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '廷豐智能研報' })).toBeInTheDocument()
  expect(screen.getByTitle('收合側欄')).toBeInTheDocument()
})
