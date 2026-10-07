import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OpsDatabasePage from './OpsDatabasePage'
import OpsIncidentsPage from './OpsIncidentsPage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const OVERVIEW = {
  generated_at: '2026-10-07T02:00:00Z',
  statement_timeout_ms: 5000,
  database: { error: null, name: 'research', size_bytes: 9 * 1024 ** 3, server_version: '16.4' },
  tables: {
    error: null, table_count: 30, live_tuples: 900, dead_tuples: 100, dead_ratio: 0.1, limit: 50,
    items: [{
      schema_name: 'research', table: 'report_chunk', total_bytes: 8 * 1024 ** 3, row_estimate: 700000,
      live_tuples: 800, dead_tuples: 80, dead_ratio: 0.0909, last_autovacuum: '2026-10-06T01:00:00Z',
      last_vacuum: null, last_autoanalyze: null, last_analyze: null,
    }],
  },
  unused_indexes: { error: { code: 'permission_denied', message: '權限不足：DB 帳號缺少讀這段統計的權限' }, items: [] },
  connections: {
    error: null, max_connections: 100, reserved_connections: 3, usable_connections: 97, total: 12, this_database: 10,
    hidden: 2, by_state: { active: 3, idle: 6, 'idle in transaction': 1 }, usage_ratio: 12 / 97,
  },
  activity: { error: { code: 'timeout', message: '查詢逾時或等不到鎖（上限 5000 ms）' } },
}

const SLOW_OFF = {
  available: false, reason: 'not_preloaded', message: 'pg_stat_statements 已建立但沒有預載', stats_reset: null, items: [],
}

const SLOW_ON = {
  available: true, reason: null, message: null, stats_reset: '2026-10-01T00:00:00Z', extension_version: '1.10',
  sort: 'total', limit: 20, hidden_count: 1,
  items: [
    { queryid: '1', query: 'SELECT * FROM research.report_chunk WHERE id = $1', query_truncated: false,
      query_hidden: false, calls: 1200, total_ms: 64000, mean_ms: 53.3, rows: 1200, cache_hit_ratio: 0.99,
      temp_blks_written: 0 },
    { queryid: '2', query: null, query_truncated: false, query_hidden: true, calls: 3, total_ms: 10, mean_ms: 3.3,
      rows: 0, cache_hit_ratio: null, temp_blks_written: 0 },
  ],
}

const trend = (metric: string, points: { t: string; value: number | null }[], granularity = 'hour') => ({
  metric, kind: metric === 'temp_bytes' || metric === 'deadlocks' ? 'rate' : metric === 'cache_hit_ratio' ? 'ratio' : 'gauge',
  table: metric === 'table_bytes' ? 'research.report_chunk' : null, granularity,
  since: '2026-09-30T02:00:00Z', until: '2026-10-07T02:00:00Z',
  points: points.map(p => ({ ...p, min: null, max: null })), tables: ['research.report_chunk', 'research.qa_log'],
})

function mount(node: React.ReactNode, handler: (url: string) => { status?: number; body: unknown }) {
  const fetchMock = vi.fn(async (url: string) => {
    const r = handler(url)
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)
  return fetchMock
}

function dbHandler(slow: unknown = SLOW_OFF) {
  return (url: string) => {
    if (url === '/api/admin/db/overview') return { body: OVERVIEW }
    if (url.startsWith('/api/admin/db/slow-queries')) return { body: slow }
    if (url.startsWith('/api/admin/db/trends')) {
      const metric = new URLSearchParams(url.split('?')[1]).get('metric') ?? ''
      return { body: trend(metric, [
        { t: '2026-10-06T00:00:00Z', value: 100 }, { t: '2026-10-06T01:00:00Z', value: null },
        { t: '2026-10-06T02:00:00Z', value: 300 }, { t: '2026-10-06T03:00:00Z', value: 200 },
      ]) }
    }
    return { status: 404, body: { detail: 'not found' } }
  }
}

test('資料庫：快照卡片；權限不足／逾時的段落只顯示說明，其餘照常', async () => {
  mount(<OpsDatabasePage />, dbHandler())
  expect(await screen.findByRole('group', { name: '資料庫大小' })).toHaveTextContent('9.0 GiB')
  expect(screen.getByRole('group', { name: '連線數' })).toHaveTextContent('12 / 97')
  expect(screen.getByRole('group', { name: '連線數' })).toHaveTextContent('上限 100（保留 3）')
  const states = screen.getByText(/連到本庫/).closest('p')!
  expect(states).toHaveTextContent('執行中 3')
  expect(states).toHaveTextContent('交易中閒置 1')
  expect(states).toHaveTextContent('看不到狀態 2')
  expect(screen.getByRole('group', { name: '快取命中率' })).toHaveTextContent('—')
  expect(screen.getByText(/快取與交易統計：查詢逾時/)).toBeInTheDocument()
  const tables = within(screen.getByRole('table', { name: '各表統計' }))
  expect(tables.getByText('report_chunk')).toBeInTheDocument()
  expect(tables.getByText('9.1%')).toBeInTheDocument()
  const idx = within(screen.getByRole('region', { name: '未使用的索引' }))
  expect(idx.getByText(/索引統計：權限不足/)).toBeInTheDocument()
  expect(screen.queryByRole('table', { name: '未使用的索引' })).not.toBeInTheDocument()
})

test('資料庫：慢查詢不可用時顯示原因與啟用步驟的出處，不顯示表格', async () => {
  mount(<OpsDatabasePage />, dbHandler(SLOW_OFF))
  const slow = within(await screen.findByRole('region', { name: '慢查詢（pg_stat_statements）' }))
  expect(await slow.findByText(/沒有預載它/)).toBeInTheDocument()
  expect(slow.getByText(/網頁不會自動建立擴充/)).toBeInTheDocument()
  expect(slow.queryByRole('table')).not.toBeInTheDocument()
})

test('資料庫：慢查詢可用時列出語句、權限不足的語句文字隱藏；換排序重新查詢', async () => {
  const fetchMock = mount(<OpsDatabasePage />, dbHandler(SLOW_ON))
  const table = within(await screen.findByRole('table', { name: '慢查詢' }))
  const rows = table.getAllByRole('row').slice(1)
  expect(rows[0]).toHaveTextContent('SELECT * FROM research.report_chunk WHERE id = $1')
  expect(rows[0]).toHaveTextContent('64.0 秒')
  expect(rows[1]).toHaveTextContent('權限不足，已隱藏')
  expect(screen.getByText(/1 筆因權限不足隱藏查詢文字/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('排序'), { target: { value: 'mean' } })
  await waitFor(() => expect(fetchMock.mock.calls.map(([u]) => String(u)))
    .toContain('/api/admin/db/slow-queries?sort=mean&limit=20'))
})

test('資料庫：趨勢圖（null 斷開）、切換指標與區間；單一表大小要先選表才查', async () => {
  const fetchMock = mount(<OpsDatabasePage />, dbHandler())
  const chart = await screen.findByRole('img', { name: /資料庫大小趨勢，共 4 點，最新 200 B/ })
  expect(chart.querySelectorAll('polyline')).toHaveLength(1)  // 100 單獨一段（點）＋ 300→200 一段（線）
  expect(screen.getByText('粒度')).toHaveTextContent('逐時')
  const trendCalls = () => fetchMock.mock.calls.map(([u]) => String(u)).filter(u => u.startsWith('/api/admin/db/trends'))
  expect(trendCalls()[0]).toMatch(/^\/api\/admin\/db\/trends\?metric=db_size_bytes&since=/)
  fireEvent.change(screen.getByLabelText('區間'), { target: { value: '30' } })
  fireEvent.change(screen.getByLabelText('指標'), { target: { value: 'table_bytes' } })
  expect(await screen.findByText(/選一張表看它的大小變化/)).toBeInTheDocument()
  expect(trendCalls().some(u => u.includes('metric=table_bytes'))).toBe(false)
  fireEvent.change(screen.getByLabelText('表'), { target: { value: 'research.report_chunk' } })
  await waitFor(() => expect(trendCalls().some(u => u.includes('metric=table_bytes')
    && u.includes('table=research.report_chunk'))).toBe(true))
  const since = new URLSearchParams(trendCalls().at(-1)!.split('?')[1]).get('since')!
  const days = (Date.now() - new Date(since).getTime()) / 86_400_000
  expect(days).toBeGreaterThan(29.9)
  expect(days).toBeLessThan(30.1)
})

test('資料庫：快照 API 失敗時顯示錯誤，不白屏', async () => {
  mount(<OpsDatabasePage />, url => url === '/api/admin/db/overview'
    ? { status: 403, body: { detail: '缺少權限：ops.read', code: 'missing_scope' } }
    : dbHandler()(url))
  expect(await screen.findByText(/資料庫快照載入失敗：缺少權限：ops.read/)).toBeInTheDocument()
})

const INCIDENT_LIST = {
  since: '2026-09-06T04:00:00Z', until: '2026-10-06T04:00:00Z', total: 0, limit: 50, offset: 0, has_more: false,
  next_offset: null, items: [],
}

const TRENDS = {
  since: '2026-07-08T04:00:00Z', until: '2026-10-06T04:00:00Z',
  weeks: [
    { week_start: '2026-09-21', component: 'web', severity: 'CRITICAL', total: 2, resolved: 2, lost: 0, firing: 0 },
    { week_start: '2026-09-28', component: 'web', severity: 'WARNING', total: 1, resolved: 0, lost: 1, firing: 0 },
    { week_start: '2026-09-28', component: 'container', severity: 'CRITICAL', total: 1, resolved: 0, lost: 0, firing: 1 },
  ],
  summary: { total: 4, resolved: 2, lost: 1, firing: 1, critical: 3, warning: 1, mttr_seconds: 1200, p50_seconds: 1200,
    p90_seconds: 1680 },
  by_component: [
    { component: 'web', total: 3, resolved: 2, lost: 1, firing: 0, critical: 2, warning: 1, mttr_seconds: 1200,
      p50_seconds: 1200, p90_seconds: 1680 },
    { component: 'container', total: 1, resolved: 0, lost: 0, firing: 1, critical: 1, warning: 0, mttr_seconds: null,
      p50_seconds: null, p90_seconds: null },
  ],
  top_reasons: [{ reason: 'probe_exit_1', total: 3, components: ['container', 'web'] }],
  jobs_since: '2026-07-08T04:00:00Z', jobs_until: '2026-10-06T04:00:00Z',
  jobs: [
    { unit: 'report-mark-sync.service', runs: 8, finished: 8, failed: 1, lost: 0, running: 0, failure_rate: 0.125,
      last_failure_at: '2026-10-05T03:00:00Z', last_started_at: '2026-10-06T03:00:00Z' },
    { unit: 'report-mark-backup.service', runs: 3, finished: 3, failed: 0, lost: 0, running: 0, failure_rate: 0,
      last_failure_at: null, last_started_at: '2026-10-06T03:00:00Z' },
  ],
}

test('事件趨勢：MTTR 只算已恢復、每週長條依嚴重度、各元件與常見原因、批次失敗率；換區間重新查詢', async () => {
  const fetchMock = mount(<OpsIncidentsPage />, url => {
    if (url.startsWith('/api/admin/incidents/trends')) return { body: TRENDS }
    if (url.startsWith('/api/admin/incidents')) return { body: INCIDENT_LIST }
    return { status: 404, body: { detail: 'not found' } }
  })
  const section = within(await screen.findByRole('region', { name: '事件趨勢' }))
  expect(await section.findByRole('group', { name: '平均恢復時間（MTTR）' })).toHaveTextContent('20 分 0 秒')
  expect(section.getByRole('group', { name: '平均恢復時間（MTTR）' })).toHaveTextContent('只算已恢復的 2 件')
  expect(section.getByRole('group', { name: '持續時間 p50／p90' })).toHaveTextContent('20 分 0 秒／28 分 0 秒')
  expect(section.getByRole('group', { name: '結束不明／進行中' })).toHaveTextContent('1／1')
  expect(section.getByRole('img', { name: /2026-09-21 嚴重 2、警告 0；2026-09-28 嚴重 1、警告 1/ })).toBeInTheDocument()
  const comps = within(section.getByRole('table', { name: '各元件事件統計' })).getAllByRole('row').slice(1)
  expect(comps[0]).toHaveTextContent('20 分 0 秒')
  expect(comps[1]).toHaveTextContent('—')
  expect(section.getByRole('table', { name: '最常見的原因' })).toHaveTextContent('probe_exit_1')
  const jobs = within(section.getByRole('table', { name: '排程工作失敗率' })).getAllByRole('row').slice(1)
  expect(jobs[0]).toHaveTextContent('report-mark-sync.service')
  expect(jobs[0]).toHaveTextContent('12.5%')
  expect(jobs[1]).toHaveTextContent('0.0%')
  fireEvent.change(section.getByLabelText('區間'), { target: { value: '365' } })
  await waitFor(() => expect(fetchMock.mock.calls.filter(([u]) => String(u).startsWith('/api/admin/incidents/trends')))
    .toHaveLength(2))
})
