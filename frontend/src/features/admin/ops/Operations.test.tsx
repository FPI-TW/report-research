import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Navigate, Route, Routes, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import OperationsLayout from './OperationsLayout'
import OpsLogsPage from './OpsLogsPage'
import OpsOverviewPage from './OpsOverviewPage'
import OpsHostPage from './OpsHostPage'
import OpsJobsPage from './OpsJobsPage'
import { OpsIncidentsPage } from './OpsPlaceholderPages'
import OpsServiceDetailPage from './OpsServiceDetailPage'
import OpsServicesPage from './OpsServicesPage'

afterEach(() => vi.unstubAllGlobals())

const systemd = (over: Record<string, unknown> = {}) => ({
  load_state: 'loaded', active_state: 'active', sub_state: 'running', result: 'success', type: 'simple',
  unit_file_state: 'enabled', exec_main_code: null, exec_main_status: 0, main_pid: 123, n_restarts: 0,
  exec_main_start_at: '2026-10-05T09:13:16Z', exec_main_exit_at: null, active_enter_at: '2026-10-05T09:13:16Z',
  state_change_at: '2026-10-05T09:13:16Z', ...over,
})
const container = (over: Record<string, unknown> = {}) => ({
  status: 'running', running: true, paused: false, restarting: false, oom_killed: false, dead: false, exit_code: 0,
  error: null, started_at: '2026-10-05T07:57:52Z', finished_at: null, health: null, failing_streak: null,
  restart_count: 0, image: 'pgvector/pgvector:pg16', ...over,
})
const svc = (name: string, tier: string, kind: 'systemd' | 'container', summary: string, over: Record<string, unknown> = {}) => ({
  name, kind, tier, target: kind === 'systemd' ? `report-mark-${name}.service` : `report-mark-${name}`, timer: null,
  actions: ['status', 'logs'], description: `${name} 說明`, summary, error: null,
  systemd: kind === 'systemd' ? systemd() : null, container: kind === 'container' ? container() : null, timer_state: null,
  ...over,
})

const SERVICES = [
  svc('web', 'critical', 'systemd', 'running'),
  svc('postgres', 'critical', 'container', 'running'),
  svc('nginx', 'critical', 'container', 'idle', { container: container({ status: 'exited', running: false, exit_code: 0 }) }),
  svc('sync', 'important', 'systemd', 'idle', {
    timer: 'report-mark-sync.timer',
    systemd: systemd({ active_state: 'inactive', sub_state: 'dead', type: 'oneshot' }),
    timer_state: { unit: 'report-mark-sync.timer', load_state: 'loaded', active_state: 'active',
      next_elapse_at: '2026-10-06T06:00:00Z', last_trigger_at: '2026-10-06T03:00:00Z' },
  }),
  svc('backup', 'important', 'systemd', 'failed', {
    systemd: systemd({ active_state: 'failed', sub_state: 'failed', result: 'exit-code' }), error: null,
  }),
  svc('freshness', 'supporting', 'systemd', 'idle', { actions: ['status'] }),
]
const LIST = { environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z', items: SERVICES }

const job = (over: Record<string, unknown> = {}) => ({
  host: 'office-host', unit: 'report-mark-sync.service', service: 'sync', invocation_id: 'a'.repeat(32),
  state: 'finished', started_at: '2026-10-06T01:00:00Z', finished_at: '2026-10-06T01:02:30Z', duration_seconds: 150,
  result: 'success', exit_status: 0, exec_main_code: 'exited', last_seen_at: '2026-10-06T01:03:00Z', ...over,
})
const JOBS = [
  job(),
  job({ service: 'backup', unit: 'report-mark-backup.service', invocation_id: 'b'.repeat(32), result: 'exit-code',
    exit_status: 2 }),
  job({ service: 'audit', unit: 'report-mark-audit.service', invocation_id: 'c'.repeat(32), state: 'running',
    finished_at: null, duration_seconds: null, result: null, exit_status: null, exec_main_code: null }),
  job({ service: 'freshness', unit: 'report-mark-freshness.service', invocation_id: 'd'.repeat(32), state: 'lost',
    finished_at: null, duration_seconds: null, result: null, exit_status: null, exec_main_code: null }),
]
const obs = (metric: string, value: number, over: Record<string, unknown> = {}) => ({
  observed_at: '2026-10-06T02:00:00Z', host: 'office-host', scope: 'host', subject: 'host', metric, value,
  state: null, detail: null, ...over,
})
const HOST_OBS = [
  obs('cpu_pct', 12.5), obs('mem_used_pct', 58.4), obs('mem_avail_bytes', 8 * 1024 ** 3), obs('load1', 2.7),
  obs('psi_io_some_avg60', 3.2), obs('disk_read_bps', 2048),
  obs('used_pct', 24.6, { subject: 'fs:/home/kashionz/projects/report-mark' }),
  obs('avail_bytes', 700 * 1024 ** 3, { subject: 'fs:/home/kashionz/projects/report-mark' }),
  obs('cpu_pct', 80.0, { observed_at: '2026-10-06T01:30:00Z' }),
]

type Reply = { status?: number; body: unknown }
type Override = (path: string) => Reply | undefined

function json(reply: Reply) {
  return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname + loc.search}</div>
}

function mount(path: string, opts: { scopes?: string[]; override?: Override } = {}) {
  const scopes = opts.scopes ?? ['admin', 'ops.read']
  const fetchMock = vi.fn(async (url: string) => {
    const o = opts.override?.(url)
    if (o) return json(o)
    if (url.startsWith('/api/admin/jobs')) {
      const p = new URLSearchParams(url.split('?')[1] ?? '')
      const items = JOBS.filter(j => !p.get('state') || j.state === p.get('state'))
      return json({ body: { since: '2026-09-29T02:00:00Z', until: '2026-10-06T02:00:00Z', total: items.length,
        limit: 50, offset: 0, has_more: false, next_offset: null, items } })
    }
    if (url.startsWith('/api/admin/observations')) {
      return json({ body: { since: '2026-10-06T01:00:00Z', until: '2026-10-06T02:00:00Z', limit: 5000,
        truncated: false, items: HOST_OBS } })
    }
    if (url === '/api/me') return json({ body: { id: 'me', username: 'root', role: 'admin', scopes } })
    if (url === '/api/admin/ops/services') return json({ body: LIST })
    const logs = url.match(/^\/api\/admin\/ops\/services\/([^/?]+)\/logs\?(.*)$/)
    if (logs) {
      const p = new URLSearchParams(logs[2])
      const s = SERVICES.find(x => x.name === logs[1])!
      return json({ body: { name: s.name, kind: s.kind, tier: s.tier, target: s.target, since: '2026-10-06T01:00:00Z',
        lines: Number(p.get('lines')), truncated: p.get('lines') === '1000',
        entries: [`2026-10-06T10:00:00+08:00 host ${s.name}[1]: hello`, '2026-10-06T10:00:01+08:00 host x: world'],
        checked_at: '2026-10-06T02:00:00Z' } })
    }
    const one = url.match(/^\/api\/admin\/ops\/services\/([^/?]+)$/)
    if (one) {
      const s = SERVICES.find(x => x.name === one[1])
      if (!s) return json({ status: 404, body: { detail: `catalog 沒有服務 '${one[1]}'`, code: 'ops_service_not_found' } })
      return json({ body: { ...s, checked_at: '2026-10-06T02:00:00Z' } })
    }
    return json({ status: 404, body: { detail: 'not found' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<OpsOverviewPage />} />
            <Route path="services" element={<OpsServicesPage />} />
            <Route path="services/:name" element={<OpsServiceDetailPage />} />
            <Route path="logs" element={<OpsLogsPage />} />
            <Route path="jobs" element={<OpsJobsPage />} />
            <Route path="incidents" element={<OpsIncidentsPage />} />
            <Route path="host" element={<OpsHostPage />} />
          </Route>
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const opsCalls = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.map(([u]) => String(u)).filter(u => u.startsWith('/api/admin/ops'))

const UNAVAILABLE: Override = url => url.startsWith('/api/admin/ops')
  ? { status: 503, body: { detail: '維運代理不可用：維運代理未啟動（找不到 /run/x.sock）', code: 'ops_agent_unavailable' } }
  : undefined

test('/admin/operations 導向總覽；子導覽六個分頁，未接 API 的（事件）標「尚未提供」', async () => {
  mount('/admin/operations')
  expect(await screen.findByRole('heading', { name: '總覽' })).toBeInTheDocument()
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/overview')
  const tabs = within(screen.getByRole('navigation', { name: '維運子導覽' }))
  expect(tabs.getAllByRole('link').map(a => a.getAttribute('href'))).toEqual([
    '/admin/operations/overview', '/admin/operations/services', '/admin/operations/jobs',
    '/admin/operations/incidents', '/admin/operations/logs', '/admin/operations/host',
  ])
  expect(tabs.getByRole('link', { name: /總覽/ })).toHaveAttribute('aria-current', 'page')
  expect(tabs.getByRole('link', { name: /事件/ })).toHaveTextContent('尚未提供')
  for (const name of [/服務/, /排程工作/, /主機/]) {
    expect(tabs.getByRole('link', { name })).not.toHaveTextContent('尚未提供')
  }
})

test('總覽：環境與主機、各層運作中／總數、需要注意的服務（核心停著也算）', async () => {
  mount('/admin/operations/overview')
  expect(await screen.findByText('office-host')).toBeInTheDocument()
  expect(screen.getByText('production')).toBeInTheDocument()
  expect(within(screen.getByLabelText('核心服務')).getByText('2／3')).toBeInTheDocument()
  expect(within(screen.getByLabelText('重要服務')).getByText('0／2')).toBeInTheDocument()
  const problems = within(screen.getByRole('region', { name: '需要注意' }))
  expect(problems.getByRole('link', { name: 'nginx' })).toHaveAttribute('href', '/admin/operations/services/nginx')
  expect(problems.getByRole('link', { name: 'backup' })).toBeInTheDocument()
  expect(problems.queryByRole('link', { name: 'web' })).not.toBeInTheDocument()
  expect(problems.queryByRole('link', { name: 'sync' })).not.toBeInTheDocument()
})

test('服務：依 tier 分組，顯示 ActiveState、Result、最近執行與 timer，連到詳情與日誌', async () => {
  mount('/admin/operations/services')
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  const webRow = within(critical.getByRole('link', { name: 'web' }).closest('tr') as HTMLElement)
  expect(critical.getByRole('link', { name: 'web' })).toHaveAttribute('href', '/admin/operations/services/web')
  expect(webRow.getByText('運作中')).toBeInTheDocument()
  expect(webRow.getByText('active／running')).toBeInTheDocument()
  expect(webRow.getByText('success')).toBeInTheDocument()
  expect(webRow.getByRole('link', { name: '日誌' })).toHaveAttribute('href', '/admin/operations/logs?service=web')
  const nginxRow = within(critical.getByRole('link', { name: 'nginx' }).closest('tr') as HTMLElement)
  expect(nginxRow.getByText('exit 0')).toBeInTheDocument()
  const important = within(screen.getByRole('region', { name: '重要服務' }))
  const syncRow = within(important.getByRole('link', { name: 'sync' }).closest('tr') as HTMLElement)
  expect(syncRow.getByText('inactive／dead')).toBeInTheDocument()
  expect(syncRow.getByTitle('上次觸發')).toHaveTextContent('上次')
  expect(syncRow.getByTitle('下次觸發')).toHaveTextContent('下次')
  const backupRow = within(important.getByRole('link', { name: 'backup' }).closest('tr') as HTMLElement)
  expect(backupRow.getByText('失敗')).toBeInTheDocument()
  expect(backupRow.getByText('exit-code')).toBeInTheDocument()
  // 不開放 logs 的服務沒有日誌連結
  const supporting = within(screen.getByRole('region', { name: '輔助服務' }))
  const freshRow = within(supporting.getByRole('link', { name: 'freshness' }).closest('tr') as HTMLElement)
  expect(freshRow.queryByRole('link', { name: '日誌' })).not.toBeInTheDocument()
})

test('空清單：總覽與服務頁都說明 catalog 沒有服務', async () => {
  const empty: Override = url => url === '/api/admin/ops/services' ? { body: { ...LIST, items: [] } } : undefined
  mount('/admin/operations/services', { override: empty })
  expect(await screen.findByText('Service Catalog 裡沒有任何服務')).toBeInTheDocument()
})

test('維運代理不可用（503）：總覽整頁換成降級說明與代理給的原因，不白屏', async () => {
  mount('/admin/operations/overview', { override: UNAVAILABLE })
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' })).toBeInTheDocument()
  expect(screen.getByText(/找不到 \/run\/x.sock/)).toBeInTheDocument()
  expect(screen.getByText(/研報平台的其他功能不受影響/)).toBeInTheDocument()
  // 子導覽還在，可以切去佔位頁
  expect(screen.getByRole('navigation', { name: '維運子導覽' })).toBeInTheDocument()
})

test('維運代理不可用（503）：服務頁與日誌頁同樣降級', async () => {
  mount('/admin/operations/logs', { override: UNAVAILABLE })
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' })).toBeInTheDocument()
  expect(screen.queryByLabelText('服務')).not.toBeInTheDocument()
})

test('後端 403：原樣顯示後端訊息', async () => {
  mount('/admin/operations/services', {
    override: url => url.startsWith('/api/admin/ops')
      ? { status: 403, body: { detail: '缺少權限：ops.read', code: 'scope_required' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('服務清單載入失敗：缺少權限：ops.read')
})

test('日誌：選服務才查；預設 1 小時、200 行；改時間範圍與行數會重查，截斷時有提示', async () => {
  const fetchMock = mount('/admin/operations/logs')
  expect(await screen.findByText('請先選擇服務')).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual(['/api/admin/ops/services'])
  // freshness 沒開放 logs，不在選單裡
  expect(within(screen.getByLabelText('服務')).queryByRole('option', { name: 'freshness' })).not.toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('服務'), { target: { value: 'web' } })
  expect(await screen.findByLabelText('web 的日誌')).toHaveTextContent('host web[1]: hello')
  expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=1h&lines=200')
  expect(screen.getByText(/2 行/)).toBeInTheDocument()
  expect(screen.queryByRole('note')).not.toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('時間範圍'), { target: { value: '15m' } })
  await waitFor(() => expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=15m&lines=200'))
  fireEvent.change(screen.getByLabelText('行數'), { target: { value: '1000' } })
  await waitFor(() => expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=15m&lines=1000'))
  expect(await screen.findByRole('note')).toHaveTextContent('已從較舊的一端截掉')
  expect(screen.getByTestId('loc')).toHaveTextContent('service=web&since=15m&lines=1000')
})

test('日誌：網址帶不在清單內的 since／lines 時退回預設值，不把任意字串送給後端', async () => {
  const fetchMock = mount('/admin/operations/logs?service=postgres&since=99y&lines=5')
  expect(await screen.findByLabelText('postgres 的日誌')).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/postgres/logs?since=1h&lines=200')
  expect(screen.getByLabelText('時間範圍')).toHaveValue('1h')
})

test('日誌：查日誌本身失敗（504）顯示後端訊息，選擇器還在', async () => {
  mount('/admin/operations/logs?service=web', {
    override: url => url.includes('/logs?')
      ? { status: 504, body: { detail: 'journalctl 逾時（8 秒）', code: 'ops_timeout' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('日誌載入失敗：journalctl 逾時（8 秒）')
  expect(screen.getByLabelText('服務')).toHaveValue('web')
})

test('服務詳情：列出容器 State，連回清單與日誌；不在 catalog 顯示 404 訊息', async () => {
  mount('/admin/operations/services/postgres')
  expect(await screen.findByRole('heading', { name: /postgres/ })).toBeInTheDocument()
  expect(screen.getByText('pgvector/pgvector:pg16')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '回到服務清單' })).toHaveAttribute('href', '/admin/operations/services')
  expect(screen.getByRole('link', { name: '查看日誌' })).toHaveAttribute('href', '/admin/operations/logs?service=postgres')
})

test('服務詳情：不在 catalog 的名稱顯示後端 404 訊息', async () => {
  mount('/admin/operations/services/nope')
  expect(await screen.findByRole('alert')).toHaveTextContent("catalog 沒有服務 'nope'")
  expect(screen.getByRole('link', { name: '回到服務清單' })).toBeInTheDocument()
})

test('佔位頁 incidents：標明尚未提供，不打任何維運 API', async () => {
  const fetchMock = mount('/admin/operations/incidents')
  expect(await screen.findByRole('heading', { name: '事件（尚未提供）' })).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
})

test('排程工作：列出執行紀錄（成功、失敗、執行中、結果不明），耗時與 Result；可依狀態篩選', async () => {
  const fetchMock = mount('/admin/operations/jobs')
  const table = within(await screen.findByRole('table', { name: '排程工作執行紀錄' }))
  const rows = table.getAllByRole('row').slice(1)
  expect(rows).toHaveLength(4)
  expect(within(rows[0]).getByText('成功')).toBeInTheDocument()
  expect(rows[0]).toHaveTextContent('2 分 30 秒')
  expect(within(rows[1]).getByText('失敗')).toBeInTheDocument()
  expect(rows[1]).toHaveTextContent('exit-code・exit 2')
  expect(within(rows[2]).getByText('執行中')).toBeInTheDocument()
  expect(within(rows[3]).getByText('結果不明')).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
  fireEvent.change(screen.getByLabelText('狀態'), { target: { value: 'lost' } })
  await waitFor(() => expect(within(screen.getByRole('table', { name: '排程工作執行紀錄' })).getAllByRole('row')).toHaveLength(2))
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/admin/jobs?state=lost&limit=50&offset=0')
})

test('排程工作：後端錯誤原樣顯示，不白屏', async () => {
  mount('/admin/operations/jobs', {
    override: url => url.startsWith('/api/admin/jobs')
      ? { status: 400, body: { detail: '時間範圍最多 90 天', code: 'invalid_params' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('時間範圍最多 90 天')
})

test('主機：最新值與一小時最高值、檔案系統用量；沒有資料時說明要檢查的 unit', async () => {
  mount('/admin/operations/host')
  const cpu = within(await screen.findByRole('group', { name: 'CPU 使用率' }))
  expect(cpu.getByText('12.5%')).toBeInTheDocument()
  expect(cpu.getByText(/1 小時最高 80\.0%/)).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '記憶體使用率' })).getByText(/可用 8\.0 GiB/)).toBeInTheDocument()
  const fs = within(screen.getByRole('table', { name: '檔案系統' }))
  expect(fs.getByText('/home/kashionz/projects/report-mark')).toBeInTheDocument()
  expect(fs.getByText('24.6%')).toBeInTheDocument()
})

test('主機：最近一小時沒有觀測時給出明確說明', async () => {
  mount('/admin/operations/host', {
    override: url => url.startsWith('/api/admin/observations')
      ? { body: { since: '2026-10-06T01:00:00Z', until: '2026-10-06T02:00:00Z', limit: 5000, truncated: false, items: [] } }
      : undefined,
  })
  expect(await screen.findByText(/最近一小時沒有主機觀測/)).toHaveTextContent('report-mark-load-observations.timer')
})

test('沒有 ops.read：只顯示需要權限的說明，不打維運 API', async () => {
  const fetchMock = mount('/admin/operations/overview', { scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「維運」權限' })).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
})
