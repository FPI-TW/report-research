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
  expect(screen.getByTitle('收合側欄')).toBeInTheDocument()
}, 15000)
