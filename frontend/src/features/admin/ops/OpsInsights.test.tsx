import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsDataHealthPage from './OpsDataHealthPage'
import OpsJobsPage from './OpsJobsPage'
import OpsLlmUsagePage from './OpsLlmUsagePage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const finding = (asset: string, label: string, state: string, over: Record<string, unknown> = {}) => ({
  asset, label, state, latest: '2026-10-06T01:00:00Z', age_days: 0.1, threshold_days: 3, detail: `${label} 說明`, ...over,
})

const HEALTH = {
  generated_at: '2026-10-06T02:00:00Z',
  overall: 'fail',
  freshness: {
    status: 'ok', exit_code: 0, error: null,
    findings: [
      finding('pipeline', '管線執行', 'fresh', { threshold_days: 9 }),
      finding('corpus', '語料入庫', 'disabled', { threshold_days: 0 }),
      finding('takeaway', '重點摘錄', 'fresh'),
    ],
  },
  db_audit: {
    status: 'fail', available: true, unavailable_reason: null, finished_at: '2026-10-06T00:45:00Z', age_hours: 1.2,
    stale: false, exit_code: 1, error: null, skipped: ['norm_drift'],
    findings: [
      { key: 'null_embedding', label: 'chunk 缺 embedding', severity: 'error', count: 0, detail: '不該顯示的說明' },
      { key: 'chunkless_report', label: '有全文卻沒有任何 chunk 的研報', severity: 'warn', count: 3, detail: 'ingest 只跑了報告層' },
    ],
  },
  r2_reconcile: {
    status: 'unknown', available: false, unavailable_reason: 'missing', finished_at: null, age_hours: null,
    stale: false, exit_code: null, mode: null, dry_run: null, limit: null, orphan_scan: null, stats: null,
    issues: [], issues_total: 0,
  },
}

const totals = (calls: number, over: Record<string, unknown> = {}) => ({
  calls, failures: 0, prompt_hit_tokens: 100, prompt_miss_tokens: 900, completion_tokens: 200, reasoning_tokens: 0,
  calls_without_tokens: 0, total_ms: calls * 2000, cost: null, ...over,
})

const USAGE = {
  since: '2026-09-06T02:00:00Z', until: '2026-10-06T02:00:00Z', timezone: 'Asia/Taipei',
  source: { exists: true, size_bytes: 4096, scanned_bytes: 4096, truncated: true, lines_scanned: 12, lines_invalid: 1,
    lines_in_range: 11, earliest_ts: '2026-09-20T00:00:00Z', latest_ts: '2026-10-06T01:00:00Z' },
  totals: totals(11, { failures: 2 }),
  by_day: [{ day: '2026-10-05', ...totals(5) }, { day: '2026-10-06', ...totals(6) }],
  by_task: [{ task: 'summary', ...totals(8) }, { task: 'title', ...totals(3, { failures: 2 }) }],
  by_model: [{ model: 'deepseek-flash', ...totals(11) }],
  rows: [], rows_truncated: false, cost_available: false,
}

function mount(path: string, routes: { path: string; element: React.ReactNode }[], handler: (url: string) => Response) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'ops.read'] }))
    return handler(url)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            {routes.map(r => <Route key={r.path} path={r.path} element={r.element} />)}
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

test('資料健康：整體狀態、新鮮度表、稽核只展開有違反的說明、對帳還沒有結果檔', async () => {
  mount('/admin/operations/data-health', [{ path: 'data-health', element: <OpsDataHealthPage /> }],
    () => new Response(JSON.stringify(HEALTH)))
  expect(await screen.findByRole('heading', { name: '資料健康' })).toBeInTheDocument()
  const fresh = screen.getByRole('table', { name: '批次新鮮度' })
  expect(within(fresh).getByText('管線執行')).toBeInTheDocument()
  expect(within(fresh).getByText('9 小時')).toBeInTheDocument()
  expect(within(fresh).getByText('不告警')).toBeInTheDocument()
  const audit = screen.getByRole('table', { name: '稽核檢查' })
  expect(within(audit).getByText('ingest 只跑了報告層')).toBeInTheDocument()
  expect(within(audit).queryByText('不該顯示的說明')).not.toBeInTheDocument()
  expect(screen.getByText('這次跳過：norm_drift')).toBeInTheDocument()
  expect(screen.getByText(/還沒有結果檔。每週一 07:00/)).toBeInTheDocument()
  expect(screen.getAllByText('異常').length).toBeGreaterThanOrEqual(2)
})

test('資料健康：API 失敗時顯示後端 detail，不白屏', async () => {
  mount('/admin/operations/data-health', [{ path: 'data-health', element: <OpsDataHealthPage /> }],
    () => new Response(JSON.stringify({ detail: '需要權限：ops.read', code: 'missing_scope' }), { status: 403 }))
  expect(await screen.findByRole('alert')).toHaveTextContent('資料健康載入失敗：需要權限：ops.read')
})

test('LLM 用量：總計、依任務／模型／日期，截斷提示；換範圍會帶新的 since 重抓', async () => {
  const fetchMock = mount('/admin/operations/llm-usage', [{ path: 'llm-usage', element: <OpsLlmUsagePage /> }],
    () => new Response(JSON.stringify(USAGE)))
  expect(await screen.findByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(within(screen.getByLabelText('呼叫次數')).getByText('11')).toBeInTheDocument()
  expect(screen.getByText(/檔案超過讀取上限/)).toBeInTheDocument()
  const byDay = screen.getByRole('table', { name: '依日期' })
  const days = within(byDay).getAllByRole('row').slice(1).map(r => (r as HTMLTableRowElement).cells[0].textContent)
  expect(days).toEqual(['2026-10-06', '2026-10-05'])
  expect(screen.queryByRole('columnheader', { name: '費用' })).not.toBeInTheDocument()
  const usageCalls = () => fetchMock.mock.calls.map(([u]) => String(u)).filter(u => u.startsWith('/api/admin/llm-usage'))
  expect(usageCalls()).toHaveLength(1)
  expect(usageCalls()[0]).toMatch(/^\/api\/admin\/llm-usage\?since=/)
  fireEvent.change(screen.getByLabelText('範圍'), { target: { value: '7' } })
  await waitFor(() => expect(usageCalls()).toHaveLength(2))
  const since = new Date(decodeURIComponent(usageCalls()[1].split('since=')[1]))
  expect(Date.now() - since.getTime()).toBeGreaterThan(6.9 * 86_400_000)
  expect(Date.now() - since.getTime()).toBeLessThan(7.1 * 86_400_000)
})

test('LLM 用量：檔案不存在時說明，不是錯誤', async () => {
  mount('/admin/operations/llm-usage', [{ path: 'llm-usage', element: <OpsLlmUsagePage /> }],
    () => new Response(JSON.stringify({ ...USAGE, source: { ...USAGE.source, exists: false, truncated: false },
      totals: totals(0), by_day: [], by_task: [], by_model: [] })))
  expect(await screen.findByText(/還沒有用量紀錄/)).toBeInTheDocument()
})

test('排程工作的匯出按鈕帶目前的篩選條件', async () => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
  const fetchMock = mount('/admin/operations/jobs', [{ path: 'jobs', element: <OpsJobsPage /> }], url => {
    if (url.startsWith('/api/admin/export/')) {
      return new Response('﻿host\r\n', { headers: { 'X-Export-Rows': '0', 'X-Export-Truncated': 'false' } })
    }
    return new Response(JSON.stringify({ since: '2026-09-29T02:00:00Z', until: '2026-10-06T02:00:00Z', total: 0,
      limit: 50, offset: 0, has_more: false, next_offset: null, items: [] }))
  })
  expect(await screen.findByText(/這段期間沒有執行紀錄/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('狀態'), { target: { value: 'lost' } })
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已下載 0 筆')
  const exportCalls = fetchMock.mock.calls.map(([u]) => String(u)).filter(u => u.startsWith('/api/admin/export/'))
  expect(exportCalls).toEqual(['/api/admin/export/jobs.csv?state=lost'])
})
