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
  pipelines: {
    web: true, ingest: true, sync_import: true, tag: true,
    summaries: false, titles: true, takeaways: true, signals: true,
  },
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
  // 7/8＝web+ingest+sync_import+tag+titles+takeaways+signals。這個 7 同時守著 zod：
  // takeaways／signals／sync_import／titles 任一沒宣告在 pipelinesSchema 上都會被
  // strip 掉、分子少 1——正是先前 takeaway/signal 被靜默剝除的同一種失效。
  await waitFor(() => expect(screen.getByText('1,000 篇已導入 · 7/8 條管線執行中')).toBeInTheDocument())
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
  // 排程健康卡在舊後端（fixture 沒有 sync／unit_failures）要降級而不是消失
  expect(screen.getByText('排程健康')).toBeInTheDocument()
  expect(screen.getByText('尚無同步紀錄')).toBeInTheDocument()
  expect(screen.getByText('此版後端未提供失敗紀錄')).toBeInTheDocument()
  // 券商分佈同理：fixture 的 db 沒有 sources
  expect(screen.getByText('券商分佈')).toBeInTheDocument()
  expect(screen.getByText('此版後端未提供券商統計')).toBeInTheDocument()
})

test('後端有回 db.sources → 券商名進 DOM', async () => {
  // 同一種 strip 陷阱的第四次：schema 沒宣告 db.sources 的話，後端送了也會被
  // zod 安靜丟掉，畫面只會停在「此版後端未提供券商統計」而沒有任何錯誤。
  // 這題釘的是「宣告 + 渲染」整條都通，不只是 schema 單元測試。
  const withSources = {
    ...fixture,
    db: {
      ...fixture.db,
      sources: [
        { source: 'kgi', display: '凱基', count: 600, latest: '2026-08-04' },
        { source: 'goldman_sachs', display: '高盛', count: 300, latest: '2026-08-05' },
        { source: null, display: null, count: 100, latest: '2026-07-31' },
      ],
    },
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => withSources })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('凱基')).toBeInTheDocument())
  expect(screen.getByText('高盛')).toBeInTheDocument()
  expect(screen.getByText('未辨識')).toBeInTheDocument()
  expect(screen.getByText(/共 2 家券商 · 1,000 篇/)).toBeInTheDocument()
  expect(screen.queryByText('此版後端未提供券商統計')).toBeNull()
})

test('後端有回 sync／unit_failures → 進 DOM 並亮紅點', async () => {
  // zod 物件預設 strip：schema 沒宣告的鍵會被安靜丟掉（takeaway/signal 就這樣從
  // P4 起一路送到前端卻從未進 DOM）。這題釘的是「宣告 + 渲染」整條都通。
  const withSchedule = {
    ...fixture,
    sync: { raw: '[2026-07-30 12:00:09] 增量匯入 delta…', timestamp: '2026-07-30 12:00:09', status: 'running', label: '同步執行中' },
    unit_failures: {
      latest: '2026-07-30T11:00:00+08:00',
      count_24h: 1,
      count_7d: 3,
      recent: [{ ts: '2026-07-30T11:00:00+08:00', unit: 'report-mark-freshness.service', stage: null, rc: 1 }],
    },
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 200, ok: true, json: async () => withSchedule })))
  const { container } = render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('同步執行中')).toBeInTheDocument())
  expect(screen.getByText('近 24 小時 1 筆')).toBeInTheDocument()
  expect(screen.getByText('report-mark-freshness.service · rc=1')).toBeInTheDocument()
  expect(container.querySelector('[class*="alertDot"]')).not.toBeNull()
})

test('首抓失敗 → LIVE 顯「重連中」', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 500, ok: false, json: async () => ({}) })))
  render(wrap(<MonitorPage />))
  await waitFor(() => expect(screen.getByText('重連中')).toBeInTheDocument())
})
