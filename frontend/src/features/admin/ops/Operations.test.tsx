import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Navigate, Route, Routes, createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { routes } from '../../../App'
import { Toaster, dismissToast } from '../../../components/console'
import OperationsLayout from './OperationsLayout'
import OpsLogsPage from './OpsLogsPage'
import OpsHostPage from './OpsHostPage'
import OpsJobsPage from './OpsJobsPage'
import OpsIncidentsPage from './OpsIncidentsPage'
import OpsServicesPage from './OpsServicesPage'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

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

// 依賴圖（服務頁的「依賴」欄與抽屜的依賴關係讀它）：web 依賴 postgres 與外部的 r2，nginx 依賴 web；backup 壞了。
const depNode = (name: string, over: Record<string, unknown> = {}) => ({
  name, kind: 'systemd', tier: 'critical', target: null, description: '', summary: 'running', health: 'ok',
  health_reason: '執行中', probe: null, observed_at: null, depends_on: [], dependents: [], layer: 0, affected: false,
  impacted_by: [], ...over,
})
const GRAPH = {
  environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
  nodes: [
    depNode('postgres', { kind: 'container', dependents: ['web'] }),
    depNode('r2', { kind: 'external', health: 'unknown', health_reason: '未監控（沒有探針）', dependents: ['web'] }),
    depNode('web', { depends_on: ['postgres', 'r2'], dependents: ['nginx'], layer: 1 }),
    depNode('nginx', { kind: 'container', depends_on: ['web'], layer: 2 }),
    depNode('sync', { tier: 'important' }),
    depNode('backup', { tier: 'important', health: 'down', health_reason: '服務失敗' }),
    depNode('freshness', { tier: 'supporting' }),
  ],
  edges: [
    { dependent: 'web', dependency: 'postgres', broken: false },
    { dependent: 'web', dependency: 'r2', broken: false },
    { dependent: 'nginx', dependency: 'web', broken: false },
  ],
  down: ['backup'], root_causes: ['backup'], affected: [],
}

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

const incident = (over: Record<string, unknown> = {}) => ({
  incident_id: 'office-host:web:1791252000', host: 'office-host', component: 'web', kind: 'service',
  probe_unit: 'report-mark-health.service', status: 'resolved', severity: 'CRITICAL', reason: 'probe_exit_1',
  summary: '探針回報失敗（exit=1 result=exit-code）', opened_at: '2026-10-06T02:00:00Z',
  last_event_at: '2026-10-06T02:10:00Z', resolved_at: '2026-10-06T02:10:00Z', duration_seconds: 600, event_count: 2,
  ...over,
})
const INCIDENTS = [
  incident({ incident_id: 'office-host:container:1791260000', component: 'container', status: 'firing',
    severity: 'WARNING', reason: 'probe_exit_9', summary: null, opened_at: '2026-10-06T04:13:20Z',
    last_event_at: '2026-10-06T04:13:20Z', resolved_at: null, duration_seconds: null, event_count: 1 }),
  incident(),
  incident({ incident_id: 'office-host:monitor:1791240000', component: 'monitor', kind: 'monitor_blind',
    status: 'lost', severity: 'WARNING', reason: 'observation_missed', summary: null, resolved_at: null,
    duration_seconds: null, event_count: 1, last_event_at: '2026-10-05T22:40:00Z', opened_at: '2026-10-05T22:40:00Z' }),
]
const INCIDENT_DETAIL = {
  ...incident(),
  events_truncated: false,
  events: [
    { event_id: 'office-host:web:1791252000:1791252000:FIRING', occurred_at: '2026-10-06T02:00:00Z', action: 'FIRING',
      severity: 'CRITICAL', reason: 'probe_exit_1', status: 'web_incident', summary: '探針回報失敗', notified: true,
      journal_excerpt: '2026-10-06T09:59:58+08:00 host check_web_health.sh[1]: status=down\n'
        + '2026-10-06T09:59:59+08:00 host uvicorn[2]: token=<redacted>\n',
      journal_truncated: true, journal_since: '2026-10-06T01:50:00Z', journal_until: '2026-10-06T02:00:00Z',
      journal_units: 'report-mark-health.service,report-mark-web.service' },
    { event_id: 'office-host:web:1791252000:1791252600:RESOLVED', occurred_at: '2026-10-06T02:10:00Z',
      action: 'RESOLVED', severity: 'RESOLVED', reason: 'healthy', status: 'ok', summary: '已恢復', notified: false,
      journal_excerpt: null, journal_truncated: false, journal_since: null, journal_until: null, journal_units: null },
  ],
}

// 事件頁底部的趨勢區塊（Admin v2 DB lane；細節測試在 OpsDatabase.test.tsx）：這裡只給空的回應，不干擾清單的斷言。
const INCIDENT_TRENDS_EMPTY = {
  since: '2026-07-08T04:00:00Z', until: '2026-10-06T04:00:00Z', weeks: [],
  summary: { total: 0, resolved: 0, lost: 0, firing: 0, critical: 0, warning: 0, mttr_seconds: null, p50_seconds: null,
    p90_seconds: null },
  by_component: [], top_reasons: [], jobs_since: '2026-07-08T04:00:00Z', jobs_until: '2026-10-06T04:00:00Z', jobs: [],
}

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
    if (url.startsWith('/api/admin/incidents/trends')) return json({ body: INCIDENT_TRENDS_EMPTY })
    if (url.startsWith('/api/admin/incidents/')) {
      const id = decodeURIComponent(url.slice('/api/admin/incidents/'.length))
      if (id !== INCIDENT_DETAIL.incident_id) return json({ status: 404, body: { detail: '找不到這個事件', code: 'not_found' } })
      return json({ body: INCIDENT_DETAIL })
    }
    if (url.startsWith('/api/admin/incidents')) {
      const p = new URLSearchParams(url.split('?')[1] ?? '')
      const items = INCIDENTS.filter(i => (!p.get('status') || i.status === p.get('status'))
        && (!p.get('component') || i.component === p.get('component')))
      return json({ body: { since: '2026-09-06T04:00:00Z', until: '2026-10-06T04:00:00Z', total: items.length,
        limit: 50, offset: 0, has_more: false, next_offset: null, items } })
    }
    if (url.startsWith('/api/admin/observations')) {
      return json({ body: { since: '2026-10-06T01:00:00Z', until: '2026-10-06T02:00:00Z', limit: 5000,
        truncated: false, resolution: 'raw', items: HOST_OBS } })
    }
    if (url === '/api/me') return json({ body: { id: 'me', username: 'root', role: 'admin', scopes } })
    if (url === '/api/admin/ops/services') return json({ body: LIST })
    if (url === '/api/admin/ops/dependencies') return json({ body: GRAPH })
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
            <Route index element={<Navigate to="services" replace />} />
            <Route path="services" element={<OpsServicesPage />} />
            <Route path="logs" element={<OpsLogsPage />} />
            <Route path="jobs" element={<OpsJobsPage />} />
            <Route path="incidents" element={<OpsIncidentsPage />} />
            <Route path="host" element={<OpsHostPage />} />
          </Route>
        </Routes>
        <LocationProbe />
        <Toaster />
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

test('/admin/operations 導向服務；切頁交給外殼的次級面板（這裡沒有子導覽）', async () => {
  mount('/admin/operations')
  expect(await screen.findByRole('region', { name: '核心服務' })).toBeInTheDocument()
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/services')
  expect(screen.queryByRole('navigation', { name: '維運子導覽' })).not.toBeInTheDocument()
})

const rowOf = (region: ReturnType<typeof within>, name: string) =>
  within(region.getByRole('link', { name }).closest('tr') as HTMLElement)

test('服務：依 tier 分組，顯示 ActiveState、Result、最近執行、排程與依賴，連到詳情抽屜與日誌', async () => {
  mount('/admin/operations/services')
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  const webRow = rowOf(critical, 'web')
  expect(critical.getByRole('link', { name: 'web' })).toHaveAttribute('href', '/admin/operations/services?service=web')
  expect(webRow.getByText('運作中')).toBeInTheDocument()
  expect(webRow.getByText('active／running')).toBeInTheDocument()
  expect(webRow.getByText('success')).toBeInTheDocument()
  expect(webRow.getByText('常駐')).toBeInTheDocument()
  // 依賴欄讀依賴圖：web 依賴 postgres 與 r2
  expect(await webRow.findByText('postgres')).toBeInTheDocument()
  expect(webRow.getByText('r2')).toBeInTheDocument()
  const nginxRow = rowOf(critical, 'nginx')
  expect(nginxRow.getByText('exit 0')).toBeInTheDocument()
  const important = within(screen.getByRole('region', { name: '重要服務' }))
  const syncRow = rowOf(important, 'sync')
  expect(syncRow.getByText('inactive／dead')).toBeInTheDocument()
  expect(syncRow.getByTitle('上次觸發')).toHaveTextContent('上次')
  expect(syncRow.getByTitle('下次觸發')).toHaveTextContent('下次')
  const backupRow = rowOf(important, 'backup')
  expect(backupRow.getByText('失敗')).toBeInTheDocument()
  expect(backupRow.getByText('exit-code')).toBeInTheDocument()
  // 不開放 logs 的服務沒有日誌按鈕
  const supporting = within(screen.getByRole('region', { name: '輔助服務' }))
  expect(rowOf(supporting, 'freshness').queryByRole('button', { name: '日誌' })).not.toBeInTheDocument()
  expect(screen.getByText(/檢查時間/)).toHaveTextContent('每 30 秒更新')
  // 日誌是列的主要動作：帶著服務名到日誌頁
  fireEvent.click(webRow.getByRole('button', { name: '日誌' }))
  await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/logs?service=web'))
})

test('服務：頁首摘要與 tier 摘要用 needsAttention 算（排程閒置算正常），需要注意的列標紅', async () => {
  mount('/admin/operations/services')
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  // nginx 是核心卻沒在跑、backup 失敗：6 個裡 4 個正常；sync、freshness 的閒置不算。
  expect(screen.getByText('6 個服務，4 個正常。')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '服務' })).toBeInTheDocument()
  expect(screen.getByRole('group', { name: '核心層級' })).toHaveTextContent('2／3')
  expect(screen.getByRole('group', { name: '核心層級' })).toHaveTextContent('1 個需要注意')
  expect(screen.getByRole('group', { name: '重要層級' })).toHaveTextContent('1／2')
  expect(screen.getByRole('group', { name: '輔助層級' })).toHaveTextContent('全部正常')
  expect(critical.getByText('2／3 正常')).toBeInTheDocument()
  const important = within(screen.getByRole('region', { name: '重要服務' }))
  expect(important.getByRole('link', { name: 'backup' }).closest('tr')).toHaveClass(/rowBad/)
  expect(critical.getByRole('link', { name: 'nginx' }).closest('tr')).toHaveClass(/rowBad/)
  expect(important.getByRole('link', { name: 'sync' }).closest('tr')).not.toHaveClass(/rowBad/)
  expect(critical.getByRole('link', { name: 'web' }).closest('tr')).not.toHaveClass(/rowBad/)
})

test('服務：篩選分段帶數量，選了寫進網址；沒有符合的層級不畫', async () => {
  mount('/admin/operations/services')
  await screen.findByRole('region', { name: '核心服務' })
  const seg = within(screen.getByRole('group', { name: '篩選服務' }))
  expect(seg.getByRole('button', { name: /全部/ })).toHaveAttribute('aria-pressed', 'true')
  expect(seg.getByRole('button', { name: /全部/ })).toHaveTextContent('6')
  expect(seg.getByRole('button', { name: /需要注意/ })).toHaveTextContent('2')
  expect(seg.getByRole('button', { name: /常駐/ })).toHaveTextContent('5')
  expect(seg.getByRole('button', { name: /排程/ })).toHaveTextContent('1')
  const names = () => screen.getAllByRole('row').flatMap(r => within(r).queryAllByRole('link').map(a => a.textContent))

  fireEvent.click(seg.getByRole('button', { name: /需要注意/ }))
  await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/services?filter=attention'))
  expect(names()).toEqual(['nginx', 'backup'])
  expect(screen.queryByRole('region', { name: '輔助服務' })).not.toBeInTheDocument()
  // 摘要與 tier 摘要算的是全部，不跟著篩選變
  expect(screen.getByText('6 個服務，4 個正常。')).toBeInTheDocument()
  expect(screen.getByRole('group', { name: '輔助層級' })).toHaveTextContent('1／1')

  fireEvent.click(seg.getByRole('button', { name: /排程/ }))
  await waitFor(() => expect(names()).toEqual(['sync']))

  fireEvent.click(seg.getByRole('button', { name: /全部/ }))
  await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent(/services$/))
  expect(names()).toHaveLength(6)
})

test('服務：網址帶不認得的 filter 時當成全部', async () => {
  mount('/admin/operations/services?filter=nope')
  await screen.findByRole('region', { name: '輔助服務' })
  expect(within(screen.getByRole('group', { name: '篩選服務' })).getByRole('button', { name: /全部/ }))
    .toHaveAttribute('aria-pressed', 'true')
})

test('服務：依賴圖讀不到時依賴欄留空，服務清單照常顯示', async () => {
  mount('/admin/operations/services', {
    override: url => url === '/api/admin/ops/dependencies'
      ? { status: 502, body: { detail: '代理回應格式錯誤', code: 'ops_bad_gateway' } } : undefined,
  })
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  expect(await rowOf(critical, 'web').findByTitle('依賴關係載入失敗')).toHaveTextContent('—')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('服務列的「更多」：沒有 ops.operate 只有詳情；有的話多了 catalog 開放的寫入操作', async () => {
  mount('/admin/operations/services')
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  fireEvent.click(critical.getByRole('button', { name: 'web 的更多操作' }))
  expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(m => m.textContent)).toEqual(['詳情'])
  // 選單不冒泡到列：抽屜沒有被打開
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('menuitem', { name: '詳情' }))
  expect(await screen.findByRole('dialog', { name: 'web' })).toBeInTheDocument()
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/services?service=web')
})

test('服務列的「更多」→ 重新啟動：確認、重新驗證後送出，結果用 toast', async () => {
  const withRestart = { ...LIST, items: SERVICES.map(s => (s.name === 'web' ? { ...s, actions: ['status', 'logs', 'restart'] } : s)) }
  const fetchMock = mount('/admin/operations/services', {
    scopes: ['admin', 'ops.read', 'ops.operate'],
    override: url => {
      if (url === '/api/admin/ops/services') return { body: withRestart }
      if (url === '/api/me/elevate') return { body: { elevated_until: '2099-01-01T00:00:00Z' } }
      if (url === '/api/admin/ops/services/web/restart') {
        return { status: 202, body: { ...withRestart.items[0], action: 'restart', state: 'scheduled', execute_after_ms: 1500,
          accepted_at: '2026-10-06T02:00:00Z', checked_at: '2026-10-06T02:00:00Z' } }
      }
      return undefined
    },
  })
  const critical = within(await screen.findByRole('region', { name: '核心服務' }))
  // 唯讀的服務（postgres）沒有寫入操作
  fireEvent.click(critical.getByRole('button', { name: 'postgres 的更多操作' }))
  expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(m => m.textContent)).toEqual(['詳情'])
  fireEvent.keyDown(document, { key: 'Escape' })

  fireEvent.click(critical.getByRole('button', { name: 'web 的更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '重新啟動' }))
  const confirm = within(screen.getByRole('alertdialog', { name: '重新啟動 web' }))
  expect(confirm.getByText(/確定要重新啟動「web」/)).toBeInTheDocument()
  fireEvent.click(confirm.getByRole('button', { name: '確定重新啟動' }))
  // /api/me 沒有 elevated_until：先重新驗證
  const elevate = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(elevate).toHaveTextContent('「重新啟動 web」需要重新驗證')
  fireEvent.change(elevate.querySelector('input[type="password"]') as HTMLInputElement,
    { target: { value: 'fixed-test-secret-ops2' } })
  fireEvent.submit(elevate.querySelector('form') as HTMLFormElement)
  expect(await screen.findByRole('status')).toHaveTextContent('已排程重新啟動 web，約 1.5 秒後執行')
  const posts = fetchMock.mock.calls.filter(([u]) => String(u) === '/api/admin/ops/services/web/restart')
  expect(posts).toHaveLength(1)
  expect((posts[0] as unknown as [string, RequestInit])[1].method).toBe('POST')
})

test('空清單：服務頁說明 catalog 沒有服務', async () => {
  const empty: Override = url => url === '/api/admin/ops/services' ? { body: { ...LIST, items: [] } } : undefined
  mount('/admin/operations/services', { override: empty })
  expect(await screen.findByText('Service Catalog 裡沒有任何服務')).toBeInTheDocument()
})

test('維運代理不可用（503）：服務頁整頁換成降級說明與代理給的原因，不白屏', async () => {
  mount('/admin/operations/services', { override: UNAVAILABLE })
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' })).toBeInTheDocument()
  expect(screen.getByText(/找不到 \/run\/x.sock/)).toBeInTheDocument()
  expect(screen.getByText(/研報平台的其他功能不受影響/)).toBeInTheDocument()
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

test('服務詳情抽屜（?service=）：屬性、依賴與被依賴、最近日誌，連到日誌頁；關閉後網址拿掉參數', async () => {
  const fetchMock = mount('/admin/operations/services?service=postgres')
  const drawer = within(await screen.findByRole('dialog', { name: 'postgres' }))
  expect(await drawer.findByText('pgvector/pgvector:pg16')).toBeInTheDocument()
  expect(drawer.getByText('report-mark-postgres')).toBeInTheDocument()
  expect(drawer.getByText('postgres 說明')).toBeInTheDocument()
  expect(drawer.getByText('運作中')).toBeInTheDocument()
  // 依賴與被依賴（附燈號）：postgres 沒有依賴，被 web 依賴
  expect(await drawer.findByRole('group', { name: '依賴' })).toHaveTextContent('無')
  const dependents = within(drawer.getByRole('group', { name: '被依賴' }))
  expect(dependents.getByRole('link', { name: /web/ })).toHaveAttribute('href', '/admin/operations/services?service=web')
  expect(dependents.getByRole('img', { name: '正常' })).toBeInTheDocument()
  // 最近日誌：1 小時、100 行
  expect(await drawer.findByLabelText('postgres 的最近日誌')).toHaveTextContent('host postgres[1]: hello')
  expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/postgres/logs?since=1h&lines=100')
  expect(drawer.getByRole('link', { name: '開啟日誌頁' })).toHaveAttribute('href', '/admin/operations/logs?service=postgres')
  // 沒有 ops.operate：底部沒有操作
  expect(drawer.queryByRole('group', { name: '服務操作' })).not.toBeInTheDocument()
  fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByTestId('loc')).toHaveTextContent(/services$/)
})

test('服務詳情抽屜：systemd 與 timer 的屬性都列出來；外部依賴不是連結', async () => {
  mount('/admin/operations/services?service=sync')
  const drawer = within(await screen.findByRole('dialog', { name: 'sync' }))
  expect(await drawer.findByText('oneshot')).toBeInTheDocument()
  expect(drawer.getByText('report-mark-sync.service')).toBeInTheDocument()
  expect(drawer.getByText('report-mark-sync.timer')).toBeInTheDocument()
  for (const k of ['層級', 'unit', 'ActiveState', 'Result', '最近執行', 'LoadState', 'UnitFileState', 'Type', 'MainPID',
    '重啟次數', '結束碼', '結束時間', '狀態變更', '排程', 'Timer 狀態', '上次觸發', '下次觸發', '檢查時間']) {
    expect(drawer.getByText(k, { selector: 'dt' })).toBeInTheDocument()
  }
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

  // 換開 web：依賴 postgres（連結）與外部的 r2（不是連結、標未監控）
  fireEvent.click(within(screen.getByRole('region', { name: '核心服務' })).getByRole('link', { name: 'web' }))
  const web = within(await screen.findByRole('dialog', { name: 'web' }))
  const depends = within(await web.findByRole('group', { name: '依賴' }))
  expect(depends.getByRole('link', { name: /postgres/ })).toBeInTheDocument()
  expect(depends.queryByRole('link', { name: /r2/ })).not.toBeInTheDocument()
  expect(depends.getByRole('img', { name: '未監控' })).toBeInTheDocument()
})

test('服務詳情抽屜：點整列打開並寫進網址；不開放 logs 的服務不查日誌', async () => {
  const fetchMock = mount('/admin/operations/services')
  const supporting = within(await screen.findByRole('region', { name: '輔助服務' }))
  fireEvent.click(supporting.getByText('freshness 說明'))
  const drawer = within(await screen.findByRole('dialog', { name: 'freshness' }))
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/operations/services?service=freshness')
  expect(await drawer.findByText('檢查時間', { selector: 'dt' })).toBeInTheDocument()
  expect(drawer.queryByText('最近日誌')).not.toBeInTheDocument()
  expect(opsCalls(fetchMock).some(u => u.includes('/logs'))).toBe(false)
})

test('服務詳情抽屜：不在 catalog 的名稱顯示後端 404 訊息', async () => {
  mount('/admin/operations/services?service=nope')
  const drawer = within(await screen.findByRole('dialog', { name: 'nope' }))
  expect(await drawer.findByRole('alert')).toHaveTextContent("服務「nope」載入失敗：catalog 沒有服務 'nope'")
  expect(drawer.queryByText('屬性')).not.toBeInTheDocument()
})

test('舊的服務詳情路徑導到服務頁的抽屜（/admin/operations/services/:name → ?service=）', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/api/me')) {
      return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'admin', scopes: ['admin', 'ops.read'] }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: '維運代理不可用', code: 'ops_agent_unavailable' }), { status: 503 })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: ['/admin/operations/services/web?filter=attention'] })
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>)
  await waitFor(() => expect(router.state.location.pathname + router.state.location.search)
    .toBe('/admin/operations/services?filter=attention&service=web'), { timeout: 15000 })
}, 15000)

test('事件：列出進行中、已恢復、結束不明，不經維運代理；可依狀態與元件篩選', async () => {
  const fetchMock = mount('/admin/operations/incidents')
  const table = within(await screen.findByRole('table', { name: '事件清單' }))
  const rows = table.getAllByRole('row').slice(1)
  expect(rows).toHaveLength(3)
  expect(within(rows[0]).getByText('進行中')).toBeInTheDocument()
  expect(rows[0]).toHaveTextContent('容器')
  expect(rows[0]).toHaveTextContent('WARNING')
  expect(within(rows[1]).getByText('已恢復')).toBeInTheDocument()
  expect(rows[1]).toHaveTextContent('10 分 0 秒')
  expect(within(rows[2]).getByText('結束不明')).toBeInTheDocument()
  expect(rows[2]).toHaveTextContent('監控失明')
  expect(rows[2]).toHaveTextContent('不明（最後轉換')
  expect(screen.getByText(/即時告警以 Slack 為準/)).toHaveTextContent('3 筆事件')
  expect(screen.getByRole('button', { name: /匯出 CSV/ })).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
  const status = within(screen.getByRole('group', { name: '狀態' }))
  expect(status.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(status.getByRole('button', { name: '進行中' }))
  await waitFor(() => expect(within(screen.getByRole('table', { name: '事件清單' })).getAllByRole('row')).toHaveLength(2))
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/admin/incidents?status=firing&limit=50&offset=0')
  fireEvent.change(screen.getByLabelText('元件'), { target: { value: 'web' } })
  expect(await screen.findByText(/這段期間沒有事件/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.map(([u]) => String(u)))
    .toContain('/api/admin/incidents?status=firing&component=web&limit=50&offset=0')
})

test('事件詳情抽屜：狀態轉換舊→新，journal 片段等寬、預設收合、標示截斷；開關寫在網址（?incident=）', async () => {
  const fetchMock = mount('/admin/operations/incidents')
  const table = within(await screen.findByRole('table', { name: '事件清單' }))
  fireEvent.click(within(table.getAllByRole('row')[2]).getByRole('button', { name: '詳情' }))
  const detail = within(await screen.findByRole('dialog', { name: /Web/ }))
  expect(screen.getByTestId('loc')).toHaveTextContent(`incident=${encodeURIComponent('office-host:web:1791252000')}`)
  expect(await detail.findByText('狀態轉換（舊→新）')).toBeInTheDocument()
  expect(fetchMock.mock.calls.map(([u]) => String(u)))
    .toContain(`/api/admin/incidents/${encodeURIComponent('office-host:web:1791252000')}`)
  expect(detail.getByText('report-mark-health.service')).toBeInTheDocument()
  expect(detail.getByText('10 分 0 秒')).toBeInTheDocument()
  const events = detail.getAllByRole('listitem')
  expect(events).toHaveLength(2)
  expect(events[0]).toHaveTextContent('開始')
  expect(events[0]).toHaveTextContent('已通知')
  expect(events[1]).toHaveTextContent('已恢復')
  expect(events[1]).toHaveTextContent('未送出通知')
  const excerpt = detail.getByLabelText('journal 片段')
  expect(excerpt.tagName).toBe('PRE')
  expect(excerpt).toHaveTextContent('token=<redacted>')
  const box = excerpt.closest('details')!
  expect(box).not.toHaveAttribute('open')
  expect(within(box).getByText(/片段已截斷/)).toBeInTheDocument()
  expect(within(box).getByText(/report-mark-web.service/)).toBeInTheDocument()
  expect(within(events[1]).queryByLabelText('journal 片段')).not.toBeInTheDocument()
  fireEvent.click(detail.getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByTestId('loc')).toHaveTextContent(/incidents$/)
})

test('事件詳情抽屜：點整列也能開；網址直接帶 incident 就打開，找不到的 id 顯示後端訊息', async () => {
  mount(`/admin/operations/incidents?incident=${encodeURIComponent('office-host:nope:1')}`)
  const missing = within(await screen.findByRole('dialog', { name: '事件詳情' }))
  expect(await missing.findByRole('alert')).toHaveTextContent('事件詳情載入失敗：找不到這個事件')
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const table = within(screen.getByRole('table', { name: '事件清單' }))
  fireEvent.click(within(table.getAllByRole('row')[2]).getByText('probe_exit_1'))
  expect(await screen.findByRole('dialog', { name: /Web/ })).toBeInTheDocument()
})

test('事件：超過一頁時可以翻頁，篩選後回到第一頁', async () => {
  const fetchMock = mount('/admin/operations/incidents', {
    override: url => {
      if (!url.startsWith('/api/admin/incidents?')) return undefined
      const offset = Number(new URLSearchParams(url.split('?')[1]).get('offset'))
      return { body: { since: '2026-09-06T04:00:00Z', until: '2026-10-06T04:00:00Z', total: 53, limit: 50, offset,
        has_more: offset === 0, next_offset: offset === 0 ? 50 : null, items: offset === 0 ? INCIDENTS : INCIDENTS.slice(0, 1) } }
    },
  })
  await screen.findByRole('table', { name: '事件清單' })
  expect(screen.getByText('第 1–3 筆，共 53 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '較新' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '較舊' }))
  expect(await screen.findByText('第 51–51 筆，共 53 筆')).toBeInTheDocument()
  expect(fetchMock.mock.calls.map(([u]) => String(u))).toContain('/api/admin/incidents?limit=50&offset=50')
  expect(screen.getByRole('button', { name: '較舊' })).toBeDisabled()
  fireEvent.click(within(screen.getByRole('group', { name: '狀態' })).getByRole('button', { name: '已恢復' }))
  await waitFor(() => expect(fetchMock.mock.calls.map(([u]) => String(u)))
    .toContain('/api/admin/incidents?status=resolved&limit=50&offset=0'))
})

test('事件：後端錯誤原樣顯示，不白屏', async () => {
  mount('/admin/operations/incidents', {
    override: url => url.startsWith('/api/admin/incidents') && !url.startsWith('/api/admin/incidents/trends')
      ? { status: 400, body: { detail: '時間範圍最多 366 天', code: 'invalid_params' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('時間範圍最多 366 天')
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
  expect(screen.getByText(/粒度/)).toHaveTextContent('原始觀測（每 60 秒）')
})

test('主機：最近一小時沒有觀測時給出明確說明', async () => {
  mount('/admin/operations/host', {
    override: url => url.startsWith('/api/admin/observations')
      ? { body: { since: '2026-10-06T01:00:00Z', until: '2026-10-06T02:00:00Z', limit: 5000, truncated: false,
        resolution: 'raw', items: [] } }
      : undefined,
  })
  expect(await screen.findByText(/最近一小時沒有主機觀測/)).toHaveTextContent('report-mark-load-observations.timer')
})

test('沒有 ops.read：只顯示需要權限的說明，不打維運 API', async () => {
  const fetchMock = mount('/admin/operations/services', { scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「維運」權限' })).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
})
