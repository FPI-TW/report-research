import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { act, render, screen } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import * as api from '../../lib/api'
import type { ProgressResponse } from '../../lib/schemas'
import { theme } from '../../theme'
import MonitorPage from './MonitorPage'

const MINIMAL: ProgressResponse = {
  ts: '12:00:00',
  db: { reports: 1, chunks: 1, markets: [] },
  summary: { done: 0, total: 0, remaining: 0, pct: 0 },
  tagging: null,
  ingest: null,
  pipelines: null,
  orchestrator: null,
}

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={qc}>
        <MonitorPage />
      </QueryClientProvider>
    </MantineProvider>,
  )
  return { ...utils, qc }
}

test('渲染進度數據與 LIVE 標記', async () => {
  vi.spyOn(api, 'getProgress').mockResolvedValue({
    ts: '14:00:00',
    db: { reports: 11401, chunks: 360000, markets: [{ market: 'TW', count: 9000 }] },
    summary: { done: 100, total: 200, remaining: 100, pct: 50 },
    tagging: { pct: 99, done: 11000, total: 11100, fail: 100 },
    ingest: { ingested: 3, fail: 0 },
    pipelines: { web: true, ingest: false, tag: false, summaries: true },
    orchestrator: undefined,
  })
  const { qc, unmount } = renderPage()
  expect(await screen.findByText('研報導入監控')).toBeInTheDocument()
  expect(await screen.findByText('LIVE')).toBeInTheDocument()
  expect(await screen.findByText(/標註 TAGGING/)).toBeInTheDocument()
  expect(await screen.findByText('TW')).toBeInTheDocument()
  unmount()
  qc.clear()
})

test('idle 狀態（null runtime 欄位）正常渲染、不進入 error/重連中', async () => {
  vi.spyOn(api, 'getProgress').mockResolvedValue({
    ts: '00:00:00',
    db: { reports: 42, chunks: 500, markets: [] },
    summary: { done: 0, total: 0, remaining: 0, pct: 0 },
    tagging: null,
    ingest: null,
    pipelines: null,
    orchestrator: null,
  })
  const { qc, unmount } = renderPage()
  expect(await screen.findByText('研報導入監控')).toBeInTheDocument()
  // 應顯示 LIVE，不進入重連中
  expect(await screen.findByText('LIVE')).toBeInTheDocument()
  expect(screen.queryByText('重連中')).toBeNull()
  // db.reports 應顯示具體值 42
  expect(await screen.findByText('42')).toBeInTheDocument()
  unmount()
  qc.clear()
})

test('pending→success：先顯示連線中，資料到達後顯示 LIVE 與數值', async () => {
  let resolve!: (v: ProgressResponse) => void
  const pending = new Promise<ProgressResponse>((r) => {
    resolve = r
  })
  vi.spyOn(api, 'getProgress').mockReturnValue(pending)
  const { qc, unmount } = renderPage()

  // 尚未 resolve：顯示「連線中」，無 LIVE
  expect(await screen.findByText('連線中')).toBeInTheDocument()
  expect(screen.queryByText('LIVE')).toBeNull()

  // resolve 後轉為 LIVE 並顯示具體數值
  resolve({ ...MINIMAL, db: { reports: 7, chunks: 3, markets: [] } })
  expect(await screen.findByText('LIVE')).toBeInTheDocument()
  expect(await screen.findByText('7')).toBeInTheDocument()
  unmount()
  qc.clear()
})

test('顯示在地時鐘（HH:MM:SS）', async () => {
  vi.spyOn(api, 'getProgress').mockResolvedValue(MINIMAL)
  const { qc, unmount } = renderPage()
  expect(await screen.findByText('研報導入監控')).toBeInTheDocument()
  expect(screen.getByText(/^\d{2}:\d{2}:\d{2}$/)).toBeInTheDocument()
  unmount()
  qc.clear()
})

test('每 2 秒輪詢 /api/progress', async () => {
  vi.useFakeTimers()
  try {
    const spy = vi.spyOn(api, 'getProgress').mockResolvedValue(MINIMAL)
    const { qc, unmount } = renderPage()
    // 初次抓取（mount 後的 microtask）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(spy).toHaveBeenCalledTimes(1)
    // refetchInterval=2000：每推進 2 秒多一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(spy).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(spy).toHaveBeenCalledTimes(3)
    unmount()
    qc.clear()
  } finally {
    vi.useRealTimers()
  }
})
