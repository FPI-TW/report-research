import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import OperationsLayout from './OperationsLayout'
import OpsDataHealthPage from './OpsDataHealthPage'
import OpsLlmUsagePage from './OpsLlmUsagePage'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  dismissToast()
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

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

/** `me`：/api/me 的其他欄位（scopes、elevated_until）；handler 收到 URL 與 method，回應其餘請求。 */
function mount(
  path: string, routes: { path: string; element: React.ReactNode }[],
  handler: (url: string, method: string) => Response, me: Record<string, unknown> = {},
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/me') return json({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'ops.read'], ...me })
    return handler(url, init?.method ?? 'GET')
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
      <Toaster />
    </QueryClientProvider>,
  )
  return fetchMock
}

const HEALTH_ROUTE = [{ path: 'data-health', element: <OpsDataHealthPage /> }]
const USAGE_ROUTE = [{ path: 'llm-usage', element: <OpsLlmUsagePage /> }]
const urls = (m: ReturnType<typeof mount>, method = 'GET') => m.mock.calls
  .filter(([, init]) => ((init as RequestInit | undefined)?.method ?? 'GET') === method).map(([u]) => String(u))

test('資料健康：整體狀態、新鮮度表、稽核只展開有違反的說明、對帳還沒有結果檔', async () => {
  const m = mount('/admin/operations/data-health', HEALTH_ROUTE, () => json(HEALTH))
  // 頁首：H1 取導覽結構的頁名，kicker 是「領域 / 分組」；長說明收在「說明」後面
  expect(await screen.findByRole('heading', { level: 1, name: '資料健康' })).toBeInTheDocument()
  expect(screen.getByText('維運 / 資料')).toBeInTheDocument()
  expect(screen.queryByText(/讀的是兩支 timer 最後一次寫的結果檔/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/讀的是兩支 timer 最後一次寫的結果檔/)).toBeInTheDocument()
  const fresh = await screen.findByRole('table', { name: '批次新鮮度' })
  expect(within(fresh).getByText('管線執行')).toBeInTheDocument()
  expect(within(fresh).getByText('9 小時')).toBeInTheDocument()
  expect(within(fresh).getByText('不告警')).toBeInTheDocument()
  expect(within(fresh).getAllByText('新鮮')).toHaveLength(2)
  expect(within(fresh).getByText('只列出')).toBeInTheDocument()
  const audit = screen.getByRole('table', { name: '稽核檢查' })
  expect(within(audit).getByText('ingest 只跑了報告層')).toBeInTheDocument()
  expect(within(audit).queryByText('不該顯示的說明')).not.toBeInTheDocument()
  expect(screen.getByText('這次跳過：norm_drift')).toBeInTheDocument()
  expect(screen.getByText(/還沒有結果檔。每週一 07:00/)).toBeInTheDocument()
  expect(screen.getAllByText('異常').length).toBeGreaterThanOrEqual(2)
  // 數字磚：整體與三段各一塊
  expect(screen.getByRole('group', { name: '整體' })).toHaveTextContent('異常')
  expect(screen.getByRole('group', { name: '整體' })).toHaveTextContent('快取 60 秒')
  expect(screen.getByRole('group', { name: '新鮮度' })).toHaveTextContent('正常')
  expect(screen.getByRole('group', { name: '完整性稽核' })).toHaveTextContent('異常')
  expect(screen.getByRole('group', { name: 'R2 對帳' })).toHaveTextContent('未知')
  expect(screen.getByRole('group', { name: 'R2 對帳' })).toHaveTextContent('還沒有結果檔')
  // 各段卡片標題列的狀態
  expect(within(screen.getByRole('region', { name: '資料完整性稽核' })).getByText('異常')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: '批次新鮮度' })).getByText('正常')).toBeInTheDocument()
  // 沒有 ops.operate：不顯示「立即執行」，也不去問維運代理有哪些服務可以跑
  expect(screen.queryByRole('button', { name: /立即執行/ })).not.toBeInTheDocument()
  expect(urls(m).some(u => u.startsWith('/api/admin/ops/'))).toBe(false)
})

test('資料健康：API 失敗時顯示後端 detail，不白屏', async () => {
  mount('/admin/operations/data-health', HEALTH_ROUTE,
    () => json({ detail: '需要權限：ops.read', code: 'missing_scope' }, 403))
  expect(await screen.findByRole('alert')).toHaveTextContent('資料健康載入失敗：需要權限：ops.read')
  expect(screen.getByRole('heading', { level: 1, name: '資料健康' })).toBeInTheDocument()
})

test('資料健康：結果檔過期要提示；對帳的計數、orphan 掃描與問題樣本', async () => {
  mount('/admin/operations/data-health', HEALTH_ROUTE, () => json({
    ...HEALTH,
    overall: 'warn',
    db_audit: { ...HEALTH.db_audit, status: 'warn', stale: true, findings: [], skipped: [] },
    r2_reconcile: {
      status: 'warn', available: true, unavailable_reason: null, finished_at: '2026-10-05T23:00:00Z', age_hours: 27,
      stale: false, exit_code: 0, mode: 'r2', dry_run: false, limit: null, orphan_scan: 'done',
      stats: { checked: 15060, errors: 0, unkeyed: 0, key_mismatch: 0, missing: 0, size_mismatch: 0, sha_mismatch: 0,
        sha_metadata_missing: 4, orphans: 3 },
      issues: [{ type: 'orphan', ref: 'reports/ab/abcdef.pdf' }, { type: 'weird', ref: 'reports/cd/cdef01.pdf' }],
      issues_total: 7,
    },
  }))
  const audit = within(await screen.findByRole('region', { name: '資料完整性稽核' }))
  expect(audit.getByText(/結果已超過排程週期沒有更新/)).toBeInTheDocument()
  expect(audit.getByText('退出碼', { exact: false })).toHaveTextContent('退出碼 1')
  expect(screen.getByRole('group', { name: '完整性稽核' })).toHaveTextContent('已過期')
  const r2 = within(screen.getByRole('region', { name: 'R2 物件儲存對帳' }))
  expect(r2.queryByText(/結果已超過排程週期沒有更新/)).not.toBeInTheDocument()
  const stats = r2.getByLabelText('對帳計數')
  expect(within(stats).getByText('orphan（R2 有、DB 沒有）').nextElementSibling).toHaveTextContent('3')
  expect(within(stats).getByText('已檢查').nextElementSibling).toHaveTextContent('15060')
  expect(within(stats).getByText('orphan 掃描').nextElementSibling).toHaveTextContent('已完成')
  expect(r2.getByText(/問題樣本 2 筆（共 7 筆，完整清單看該次 journal）/)).toBeInTheDocument()
  expect(r2.getByText('reports/ab/abcdef.pdf')).toBeInTheDocument()
  // 不認得的問題類型原樣顯示
  expect(r2.getByText('weird')).toBeInTheDocument()
})

test('資料健康：重新整理會重抓', async () => {
  const m = mount('/admin/operations/data-health', HEALTH_ROUTE, () => json(HEALTH))
  await screen.findByRole('table', { name: '批次新鮮度' })
  const health = () => urls(m).filter(u => u === '/api/admin/data-health').length
  expect(health()).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: '重新整理' }))
  await waitFor(() => expect(health()).toBe(2))
})

const svc = (name: string, actions: string[]) => ({
  name, kind: 'systemd', tier: 'supporting', target: `report-mark-${name}.service`, timer: `report-mark-${name}.timer`,
  actions, group: name, description: '', summary: 'idle', error: null, systemd: null, container: null, timer_state: null,
  checked_at: '2026-10-06T02:00:00Z',
})
// 這台主機的 catalog：freshness、audit 開放 run；r2-reconcile 只能看（正式環境的設定）。
const SERVICES = {
  environment: 'staging', host: 'ec2', checked_at: '2026-10-06T02:00:00Z',
  items: [svc('freshness', ['status', 'logs', 'run']), svc('audit', ['status', 'logs', 'run']),
    svc('r2-reconcile', ['status', 'logs']), svc('web', ['status', 'logs', 'restart'])],
}
const ACCEPTED = {
  ...svc('freshness', ['status', 'logs', 'run']), action: 'run', state: 'queued', execute_after_ms: 0,
  accepted_at: '2026-10-06T02:00:00Z',
}
const OPERATE = { scopes: ['admin', 'ops.read', 'ops.operate'] }
const ELEVATED = { ...OPERATE, elevated_until: '2099-01-01T00:00:00Z' }

function healthWithOps(run: () => Response = () => json(ACCEPTED, 202), services: () => Response = () => json(SERVICES)) {
  return (url: string, method: string) => {
    if (url === '/api/admin/ops/services') return services()
    if (method === 'POST' && url === '/api/admin/ops/services/freshness/run') return run()
    if (method === 'POST' && url === '/api/me/elevate') return json({ elevated_until: '2099-01-01T00:00:00Z' })
    if (method === 'POST') return json({ detail: 'not found', code: 'not_found' }, 404)
    return json(HEALTH)
  }
}

test('資料健康：有 ops.operate 時，頁首只列這台主機 catalog 開放 run 的「立即執行」', async () => {
  mount('/admin/operations/data-health', HEALTH_ROUTE, healthWithOps(), ELEVATED)
  expect(await screen.findByRole('button', { name: '立即執行 freshness' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '立即執行 audit' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '立即執行 r2-reconcile' })).not.toBeInTheDocument()
})

test('資料健康：立即執行要先確認；取消不送出，確認後送出並提示', async () => {
  const m = mount('/admin/operations/data-health', HEALTH_ROUTE, healthWithOps(), ELEVATED)
  const posts = () => urls(m, 'POST')
  fireEvent.click(await screen.findByRole('button', { name: '立即執行 freshness' }))
  const confirm = within(screen.getByRole('alertdialog', { name: '立即執行 freshness' }))
  fireEvent.click(confirm.getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  expect(posts()).toEqual([])
  fireEvent.click(screen.getByRole('button', { name: '立即執行 freshness' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  expect(await screen.findByText('已送出 freshness，等同 timer 觸發一次')).toBeInTheDocument()
  // 還在驗證視窗內：不再詢問
  expect(posts()).toEqual(['/api/admin/ops/services/freshness/run'])
})

test('資料健康：不在驗證視窗內，立即執行要先重新驗證身分', async () => {
  const m = mount('/admin/operations/data-health', HEALTH_ROUTE, healthWithOps(), OPERATE)
  fireEvent.click(await screen.findByRole('button', { name: '立即執行 freshness' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  const dialog = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(dialog).toHaveTextContent('「立即執行 freshness」需要重新驗證')
  expect(urls(m, 'POST')).toEqual([])
  fireEvent.change(dialog.querySelector('input[type="password"]') as HTMLInputElement,
    { target: { value: 'fixed-test-secret-ops1' } })
  fireEvent.submit(dialog.querySelector('form') as HTMLFormElement)
  expect(await screen.findByText('已送出 freshness，等同 timer 觸發一次')).toBeInTheDocument()
  expect(urls(m, 'POST')).toEqual(['/api/me/elevate', '/api/admin/ops/services/freshness/run'])
})

test('資料健康：同一組工作正在跑（409）→ 提示原因，不當成成功', async () => {
  mount('/admin/operations/data-health', HEALTH_ROUTE,
    healthWithOps(() => json({ detail: '同一組工作執行中', code: 'already_running' }, 409)), ELEVATED)
  fireEvent.click(await screen.findByRole('button', { name: '立即執行 freshness' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '立即執行' }))
  expect(await screen.findByText(/同一組工作正在執行中/)).toBeInTheDocument()
})

test('資料健康：維運代理不可用 → 不顯示立即執行，其餘照常', async () => {
  mount('/admin/operations/data-health', HEALTH_ROUTE,
    healthWithOps(undefined, () => json({ detail: '維運代理不可用', code: 'ops_agent_unavailable' }, 503)), ELEVATED)
  expect(await screen.findByRole('table', { name: '批次新鮮度' })).toBeInTheDocument()
  await new Promise(r => setTimeout(r, 20))
  expect(screen.queryByRole('button', { name: /立即執行/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重新整理' })).toBeInTheDocument()
})

const row = (table: HTMLElement) => within(table).getAllByRole('row').slice(1)
  .map(r => (r as HTMLTableRowElement).cells[0].textContent)

test('LLM 用量：總計、依任務／模型／日期（分頁籤），截斷提示；換範圍會帶新的 since 重抓', async () => {
  const fetchMock = mount('/admin/operations/llm-usage', USAGE_ROUTE, () => json(USAGE))
  expect(await screen.findByRole('table', { name: '依任務' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'LLM 用量' })).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '呼叫次數' })).getByText('11')).toBeInTheDocument()
  expect(screen.getByRole('group', { name: '呼叫次數' })).toHaveTextContent('失敗 2・無用量 0')
  expect(screen.getByText(/檔案超過讀取上限/)).toBeInTheDocument()
  expect(row(screen.getByRole('table', { name: '依任務' }))).toEqual(['summary', 'title'])
  const tabs = within(screen.getByRole('tablist', { name: '用量明細' }))
  expect(tabs.getByRole('tab', { name: '依任務' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.click(tabs.getByRole('tab', { name: '依模型' }))
  expect(row(screen.getByRole('table', { name: '依模型' }))).toEqual(['deepseek-flash'])
  fireEvent.click(tabs.getByRole('tab', { name: '依日期' }))
  expect(row(screen.getByRole('table', { name: '依日期' }))).toEqual(['2026-10-06', '2026-10-05'])
  expect(screen.queryByRole('columnheader', { name: '費用' })).not.toBeInTheDocument()
  const usageCalls = () => fetchMock.mock.calls.map(([u]) => String(u)).filter(u => u.startsWith('/api/admin/llm-usage'))
  expect(usageCalls()).toHaveLength(1)
  expect(usageCalls()[0]).toMatch(/^\/api\/admin\/llm-usage\?since=/)
  const range = within(screen.getByRole('group', { name: '範圍' }))
  expect(range.getByRole('button', { name: '30 天' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(range.getByRole('button', { name: '7 天' }))
  await waitFor(() => expect(usageCalls()).toHaveLength(2))
  const since = new Date(decodeURIComponent(usageCalls()[1].split('since=')[1]))
  expect(Date.now() - since.getTime()).toBeGreaterThan(6.9 * 86_400_000)
  expect(Date.now() - since.getTime()).toBeLessThan(7.1 * 86_400_000)
})

test('LLM 用量：每日呼叫長條圖一天一根；用量檔截斷時，沒讀到的日子留空不畫成 0', async () => {
  mount('/admin/operations/llm-usage', USAGE_ROUTE, () => json(USAGE))
  const daily = within(await screen.findByRole('region', { name: '每日呼叫' }))
  // 2026-09-06 ～ 2026-10-06（台北）共 31 天；截斷後只涵蓋 09-20 起的 17 天
  expect(daily.getByRole('img', { name: '每日呼叫次數：17 個資料點' })).toBeInTheDocument()
  expect(daily.getByTitle('2026-09-19・沒有資料')).toBeInTheDocument()
  expect(daily.getByTitle('2026-09-20・0 次')).toBeInTheDocument()
  expect(daily.getByTitle('2026-10-05・5 次')).toBeInTheDocument()
  // 最後一根是今天（還沒過完），用金色標出來
  expect(daily.getByTitle('2026-10-06・6 次').firstElementChild).toHaveAttribute('data-tone', 'gold')
  expect(daily.getByTitle('2026-10-05・5 次').firstElementChild).not.toHaveAttribute('data-tone')
  expect(daily.getByText('只有批次')).toBeInTheDocument()
  expect(daily.getByText('單日最高', { exact: false })).toHaveTextContent('單日最高 6 次')
})

test('LLM 用量：檔案不存在時說明，不是錯誤', async () => {
  mount('/admin/operations/llm-usage', USAGE_ROUTE,
    () => json({ ...USAGE, source: { ...USAGE.source, exists: false, truncated: false },
      totals: totals(0), by_day: [], by_task: [], by_model: [] }))
  expect(await screen.findByText(/還沒有用量紀錄/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  // 沒有任何呼叫：不畫空的趨勢圖
  expect(screen.queryByRole('region', { name: '每日呼叫' })).not.toBeInTheDocument()
})

test('LLM 用量：API 失敗時顯示後端 detail，頁首與範圍切換還在', async () => {
  mount('/admin/operations/llm-usage', USAGE_ROUTE, () => json({ detail: '需要權限：ops.read', code: 'missing_scope' }, 403))
  expect(await screen.findByRole('alert')).toHaveTextContent('LLM 用量載入失敗：需要權限：ops.read')
  expect(screen.getByRole('group', { name: '範圍' })).toBeInTheDocument()
})
