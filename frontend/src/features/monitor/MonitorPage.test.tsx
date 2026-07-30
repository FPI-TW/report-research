import { afterEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import MonitorPage from './MonitorPage'

const fixture = {
  ts: '12:00:00',
  db: { reports: 1000, chunks: 50000, markets: [{ market: 'TW', count: 600 }, { market: 'US', count: 400 }] },
  summary: { done: 900, total: 1000, remaining: 100, pct: 90 },
  tagging: { done: 800, total: 1000, fail: 200, pct: 80 },
  ingest: { ingested: 5, chunks: 300, fail: 0 },
  pipelines: { web: true, ingest: true, tag: true, summaries: false },
  orchestrator: null,
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>
}
afterEach(() => vi.restoreAllMocks())

test('成功輪詢 → 頁首副字 + LIVE + 面板', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => fixture })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('1,000 篇已導入 · 3/4 條管線執行中')).toBeInTheDocument())
  expect(screen.getByText('LIVE')).toBeInTheDocument()
  expect(screen.getByText('研報導入監控')).toBeInTheDocument()
  expect(screen.getByText('處理管線')).toBeInTheDocument()
  expect(screen.getByText('市場分佈')).toBeInTheDocument()
  expect(screen.getByText('已導入報告')).toBeInTheDocument()   // KPI label（唯一）
  expect(screen.getByText('Web 服務')).toBeInTheDocument()     // 管線列（唯一）
  // 「語意標註」「摘要生成」各出現於「面板標題」+「管線列名稱」共 2 處 → 用 getAllByText
  expect(screen.getAllByText('語意標註').length).toBeGreaterThanOrEqual(2)
  expect(screen.getAllByText('摘要生成').length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText('資料每 5 秒自動更新 · 廷豐智能研報導入管線')).toBeInTheDocument()
})

test('首抓失敗 → LIVE 顯「重連中」', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 500, ok: false, json: async () => ({}) })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('重連中')).toBeInTheDocument())
})
