import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import c from '../../../components/console/Console.module.css'
import type { ObservationListResponse } from '../../../lib/generated/adminApi'
import { trendSeries } from './exec/hostSeries'
import { logWindowFor } from './exec/jobs'
import OperationsLayout from './OperationsLayout'
import OpsLogsPage from './OpsLogsPage'
import OpsHostPage from './OpsHostPage'
import OpsJobsPage from './OpsJobsPage'

// 維運 → 執行：日誌、排程工作、主機三頁（診斷在 OpsDiagnostics.test.tsx）。fixture 與 mount 從 Operations.test.tsx 複製一份。

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
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
  svc('sync', 'important', 'systemd', 'idle'),
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
const jobsBody = (items: unknown[], over: Record<string, unknown> = {}) => ({
  since: '2026-09-29T02:00:00Z', until: '2026-10-06T02:00:00Z', total: items.length, limit: 50, offset: 0,
  has_more: false, next_offset: null, items, ...over,
})

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
const obsBody = (items: unknown[], over: Record<string, unknown> = {}) => ({
  since: '2026-10-06T01:00:00Z', until: '2026-10-06T02:00:00Z', limit: 5000, truncated: false, resolution: 'raw',
  items, ...over,
})

type Reply = { status?: number; body: unknown }
type Override = (path: string) => Reply | Response | undefined

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
    if (o) return o instanceof Response ? o : json(o)
    if (url.startsWith('/api/admin/jobs')) {
      const p = new URLSearchParams(url.split('?')[1] ?? '')
      return json({ body: jobsBody(JOBS.filter(j => !p.get('state') || j.state === p.get('state'))) })
    }
    if (url.startsWith('/api/admin/observations')) return json({ body: obsBody(HOST_OBS) })
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
    return json({ status: 404, body: { detail: 'not found' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin/operations" element={<OperationsLayout />}>
            <Route path="logs" element={<OpsLogsPage />} />
            <Route path="jobs" element={<OpsJobsPage />} />
            <Route path="host" element={<OpsHostPage />} />
          </Route>
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const calls = (fetchMock: ReturnType<typeof mount>) => fetchMock.mock.calls.map(([u]) => String(u))
const opsCalls = (fetchMock: ReturnType<typeof mount>) => calls(fetchMock).filter(u => u.startsWith('/api/admin/ops'))

const UNAVAILABLE: Override = url => url.startsWith('/api/admin/ops')
  ? { status: 503, body: { detail: '維運代理不可用：維運代理未啟動（找不到 /run/x.sock）', code: 'ops_agent_unavailable' } }
  : undefined

/** 分段切換：一組按鈕（role=group），選中的那顆 aria-pressed。 */
const seg = (name: string) => within(screen.getByRole('group', { name }))
const pick = (group: string, option: string) => fireEvent.click(seg(group).getByRole('button', { name: option }))
const loc = () => screen.getByTestId('loc').textContent ?? ''

// ── 日誌 ────────────────────────────────────────────────────────────────────

test('日誌：頁首取導覽結構，摘要說明祕密已遮蔽；長說明收在「說明」後面', async () => {
  mount('/admin/operations/logs')
  expect(await screen.findByRole('heading', { level: 1, name: '日誌' })).toBeInTheDocument()
  expect(screen.getByText('維運 / 執行')).toBeInTheDocument()
  expect(screen.getByText(/形似祕密的片段已由維運代理遮成 <redacted>/)).toBeInTheDocument()
  expect(screen.queryByText(/docker logs/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/docker logs/)).toBeInTheDocument()
})

test('維運代理不可用（503）：日誌頁同樣降級，不畫選擇器', async () => {
  mount('/admin/operations/logs', { override: UNAVAILABLE })
  expect(await screen.findByRole('heading', { name: '維運代理目前無法使用' })).toBeInTheDocument()
  expect(screen.getByText(/找不到 \/run\/x.sock/)).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: '服務' })).not.toBeInTheDocument()
})

test('日誌：選服務才查；預設 1 小時、200 行；改時間範圍與行數會重查，截斷時有提示', async () => {
  const fetchMock = mount('/admin/operations/logs')
  expect(await screen.findByText('請先選擇服務')).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual(['/api/admin/ops/services'])
  // freshness 沒開放 logs，不在分段裡
  expect(seg('服務').getAllByRole('button').map(b => b.textContent)).toEqual(['web', 'postgres', 'sync'])
  expect(seg('時間範圍').getByRole('button', { name: '最近 1 小時' })).toHaveAttribute('aria-pressed', 'true')
  expect(seg('行數').getByRole('button', { name: '200' })).toHaveAttribute('aria-pressed', 'true')

  pick('服務', 'web')
  expect(await screen.findByLabelText('web 的日誌')).toHaveTextContent('host web[1]: hello')
  expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=1h&lines=200')
  expect(seg('服務').getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByText(/2 行/)).toBeInTheDocument()
  expect(screen.getByText('report-mark-web.service')).toBeInTheDocument()
  expect(screen.queryByRole('note')).not.toBeInTheDocument()

  pick('時間範圍', '最近 15 分鐘')
  await waitFor(() => expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=15m&lines=200'))
  pick('行數', '1000')
  await waitFor(() => expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/web/logs?since=15m&lines=1000'))
  expect(await screen.findByRole('note')).toHaveTextContent('已從較舊的一端截掉')
  expect(loc()).toContain('service=web&since=15m&lines=1000')
})

test('日誌：網址帶不在清單內的 since／lines 時退回預設值，不把任意字串送給後端', async () => {
  const fetchMock = mount('/admin/operations/logs?service=postgres&since=99y&lines=5')
  expect(await screen.findByLabelText('postgres 的日誌')).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toContain('/api/admin/ops/services/postgres/logs?since=1h&lines=200')
  expect(seg('時間範圍').getByRole('button', { name: '最近 1 小時' })).toHaveAttribute('aria-pressed', 'true')
  expect(seg('行數').getByRole('button', { name: '200' })).toHaveAttribute('aria-pressed', 'true')
})

test('日誌：查日誌本身失敗（504）顯示後端訊息，選擇器還在', async () => {
  mount('/admin/operations/logs?service=web', {
    override: url => url.includes('/logs?')
      ? { status: 504, body: { detail: 'journalctl 逾時（8 秒）', code: 'ops_timeout' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('日誌載入失敗：journalctl 逾時（8 秒）')
  expect(seg('服務').getByRole('button', { name: 'web' })).toHaveAttribute('aria-pressed', 'true')
})

test('日誌：「重新整理」重查同一份日誌；這段時間沒有日誌時說明', async () => {
  let empty = false
  const fetchMock = mount('/admin/operations/logs?service=web', {
    override: url => url.includes('/logs?') && empty
      ? { body: { name: 'web', kind: 'systemd', tier: 'critical', target: 'report-mark-web.service',
        since: '2026-10-06T01:00:00Z', lines: 200, truncated: false, entries: [], checked_at: '2026-10-06T02:05:00Z' } }
      : undefined,
  })
  expect(await screen.findByLabelText('web 的日誌')).toBeInTheDocument()
  const logCalls = () => opsCalls(fetchMock).filter(u => u.includes('/logs?')).length
  expect(logCalls()).toBe(1)
  empty = true
  fireEvent.click(screen.getByRole('button', { name: '重新整理' }))
  expect(await screen.findByText('這段時間沒有日誌')).toBeInTheDocument()
  expect(logCalls()).toBe(2)
  expect(screen.queryByLabelText('web 的日誌')).not.toBeInTheDocument()
})

test('日誌：catalog 裡沒有任何服務開放 logs 時說明，不畫選擇器', async () => {
  mount('/admin/operations/logs', {
    override: url => url === '/api/admin/ops/services'
      ? { body: { ...LIST, items: [svc('freshness', 'supporting', 'systemd', 'idle', { actions: ['status'] })] } } : undefined,
  })
  expect(await screen.findByText('沒有開放查看日誌的服務')).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: '服務' })).not.toBeInTheDocument()
})

// ── 排程工作 ────────────────────────────────────────────────────────────────

test('排程工作：列出執行紀錄（成功、失敗、執行中、結果不明），耗時與 Result；可依狀態篩選', async () => {
  const fetchMock = mount('/admin/operations/jobs')
  const table = within(await screen.findByRole('table', { name: '排程工作執行紀錄' }))
  const rows = table.getAllByRole('row').slice(1)
  expect(rows).toHaveLength(4)
  expect(within(rows[0]).getByText('成功')).toBeInTheDocument()
  expect(rows[0]).toHaveTextContent('2 分 30 秒')
  expect(rows[0]).toHaveTextContent('report-mark-sync.service')
  expect(within(rows[1]).getByText('失敗')).toBeInTheDocument()
  expect(rows[1]).toHaveTextContent('exit-code・exit 2')
  expect(within(rows[2]).getByText('執行中')).toBeInTheDocument()
  expect(within(rows[3]).getByText('結果不明')).toBeInTheDocument()
  // 只有失敗的列用 rowBad；執行中與結果不明不算失敗
  expect(rows.map(r => r.classList.contains(c.rowBad))).toEqual([false, true, false, false])
  expect(screen.getByText(/共/)).toHaveTextContent('共 4 筆（每 5 分鐘匯入一次）')
  expect(screen.getByRole('heading', { level: 1, name: '排程工作' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '匯出 CSV' })).toBeInTheDocument()
  // 資料來自 DB 投影，不經維運代理
  expect(opsCalls(fetchMock)).toEqual([])
  expect(seg('狀態').getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true')
  pick('狀態', '結果不明')
  await waitFor(() => expect(within(screen.getByRole('table', { name: '排程工作執行紀錄' })).getAllByRole('row')).toHaveLength(2))
  expect(calls(fetchMock)).toContain('/api/admin/jobs?state=lost&limit=50&offset=0')
})

test('排程工作：後端錯誤原樣顯示，不白屏', async () => {
  mount('/admin/operations/jobs', {
    override: url => url.startsWith('/api/admin/jobs')
      ? { status: 400, body: { detail: '時間範圍最多 90 天', code: 'invalid_params' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('時間範圍最多 90 天')
  expect(screen.getByRole('heading', { level: 1, name: '排程工作' })).toBeInTheDocument()
})

test('排程工作：這段期間沒有紀錄時說明要檢查的 unit', async () => {
  mount('/admin/operations/jobs', {
    override: url => url.startsWith('/api/admin/jobs') ? { body: jobsBody([]) } : undefined,
  })
  expect(await screen.findByText(/這段期間沒有執行紀錄/)).toHaveTextContent('report-mark-load-observations.timer')
})

// 從 OpsInsights.test.tsx 搬來：驗的是排程工作頁
test('排程工作的匯出按鈕帶目前的篩選條件', async () => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
  const fetchMock = mount('/admin/operations/jobs', {
    override: url => {
      if (url.startsWith('/api/admin/export/')) {
        return new Response('\uFEFFhost\r\n', { headers: { 'X-Export-Rows': '0', 'X-Export-Truncated': 'false' } })
      }
      return url.startsWith('/api/admin/jobs') ? { body: jobsBody([]) } : undefined
    },
  })
  expect(await screen.findByText(/這段期間沒有執行紀錄/)).toBeInTheDocument()
  pick('狀態', '結果不明')
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已下載 0 筆')
  const exportCalls = calls(fetchMock).filter(u => u.startsWith('/api/admin/export/'))
  expect(exportCalls).toEqual(['/api/admin/export/jobs.csv?state=lost'])
})

test('排程工作：點列開詳情抽屜並記在網址（?job=）；列出該次執行的欄位，附「看日誌」；關閉後拿掉參數', async () => {
  mount('/admin/operations/jobs')
  const table = within(await screen.findByRole('table', { name: '排程工作執行紀錄' }))
  fireEvent.click(table.getAllByRole('row')[2])
  const drawer = within(await screen.findByRole('dialog', { name: 'backup' }))
  expect(loc()).toBe(`/admin/operations/jobs?job=${'b'.repeat(32)}`)
  expect(drawer.getByText('失敗')).toBeInTheDocument()
  const field = (label: string, name: string) => {
    const dt = within(drawer.getByLabelText(label)).getByText(name)
    return dt.nextElementSibling as HTMLElement
  }
  expect(field('執行', '耗時')).toHaveTextContent('2 分 30 秒')
  expect(field('結果', 'Result')).toHaveTextContent('exit-code')
  expect(field('結果', '結束碼')).toHaveTextContent('2')
  expect(field('結果', '結束方式')).toHaveTextContent('exited')
  expect(field('識別', '主機')).toHaveTextContent('office-host')
  expect(field('識別', 'unit')).toHaveTextContent('report-mark-backup.service')
  expect(field('識別', 'invocation ID')).toHaveTextContent('b'.repeat(32))
  // 開始時間距清單查詢時間剛好 1 小時：日誌頁選得到的最小範圍是 1h
  expect(drawer.getByRole('link', { name: '看日誌' }))
    .toHaveAttribute('href', '/admin/operations/logs?service=backup&since=1h')
  fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(loc()).toBe('/admin/operations/jobs')
})

test('排程工作：列內的服務名是按鈕（鍵盤可操作），同樣開抽屜；結果不明的那筆說明原因', async () => {
  mount('/admin/operations/jobs')
  const table = within(await screen.findByRole('table', { name: '排程工作執行紀錄' }))
  fireEvent.click(table.getByRole('button', { name: 'freshness' }))
  const drawer = within(await screen.findByRole('dialog', { name: 'freshness' }))
  expect(loc()).toContain(`job=${'d'.repeat(32)}`)
  expect(drawer.getByText(/沒觀測到結束，之後已有新一輪開始/)).toBeInTheDocument()
  expect(within(drawer.getByLabelText('結果')).getByText('Result').nextElementSibling).toHaveTextContent('—')
  // Esc 也能關
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('排程工作：分享的連結直接開抽屜；unit 不在 catalog 時「看日誌」停用並說明原因', async () => {
  mount(`/admin/operations/jobs?job=${'e'.repeat(32)}`, {
    override: url => url.startsWith('/api/admin/jobs')
      ? { body: jobsBody([job({ service: null, unit: 'report-mark-oneoff.service', invocation_id: 'e'.repeat(32) })]) }
      : undefined,
  })
  const drawer = within(await screen.findByRole('dialog', { name: 'report-mark-oneoff.service' }))
  expect(drawer.queryByRole('link', { name: '看日誌' })).not.toBeInTheDocument()
  const disabled = drawer.getByRole('button', { name: '看日誌' })
  expect(disabled).toBeDisabled()
  expect(disabled).toHaveAttribute('title', '這個 unit 不在 Service Catalog，沒有對應的日誌')
})

test('排程工作：網址的 job 不在這一頁的清單裡時說明，不白屏', async () => {
  mount('/admin/operations/jobs?job=nope')
  const drawer = within(await screen.findByRole('dialog', { name: '找不到這次執行' }))
  expect(drawer.getByText(/這一頁的清單裡沒有這次執行/)).toBeInTheDocument()
  expect(screen.getByRole('table', { name: '排程工作執行紀錄' })).toBeInTheDocument()
})

test('排程工作：分頁（較舊／較新）；換狀態篩選會回到第一頁', async () => {
  const older = job({ service: 'older', unit: 'report-mark-older.service', invocation_id: 'f'.repeat(32) })
  const fetchMock = mount('/admin/operations/jobs', {
    override: url => {
      if (!url.startsWith('/api/admin/jobs')) return undefined
      const p = new URLSearchParams(url.split('?')[1] ?? '')
      if (p.get('state')) return undefined
      return p.get('offset') === '50'
        ? { body: jobsBody([older], { total: 51, offset: 50 }) }
        : { body: jobsBody(JOBS, { total: 51, has_more: true, next_offset: 50 }) }
    },
  })
  await screen.findByRole('table', { name: '排程工作執行紀錄' })
  expect(screen.getByText('第 1–4 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '較新' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '較舊' }))
  expect(await screen.findByText('第 51–51 筆')).toBeInTheDocument()
  expect(calls(fetchMock)).toContain('/api/admin/jobs?limit=50&offset=50')
  expect(screen.getByRole('button', { name: '較舊' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '較新' })).toBeEnabled()
  pick('狀態', '執行中')
  await waitFor(() => expect(calls(fetchMock)).toContain('/api/admin/jobs?state=running&limit=50&offset=0'))
})

test('logWindowFor：挑涵蓋得到開始時間的最小範圍；超過 1 天就標示涵蓋不到', () => {
  const now = '2026-10-06T02:00:00Z'
  expect(logWindowFor('2026-10-06T01:50:00Z', now)).toEqual({ since: '15m', covered: true })
  expect(logWindowFor('2026-10-06T01:00:00Z', now)).toEqual({ since: '1h', covered: true })
  expect(logWindowFor('2026-10-05T22:00:00Z', now)).toEqual({ since: '6h', covered: true })
  expect(logWindowFor('2026-10-05T03:00:00Z', now)).toEqual({ since: '1d', covered: true })
  expect(logWindowFor('2026-10-03T03:00:00Z', now)).toEqual({ since: '1d', covered: false })
  expect(logWindowFor('not-a-date', now)).toEqual({ since: '1h', covered: true })
})

// ── 主機 ────────────────────────────────────────────────────────────────────

test('主機：最新值與一小時最高值、檔案系統用量、粒度', async () => {
  const fetchMock = mount('/admin/operations/host')
  const cpu = await screen.findByRole('group', { name: 'CPU 使用率' })
  expect(cpu).toHaveTextContent('12.5%')
  expect(within(cpu).getByText(/1 小時最高 80\.0%/)).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '記憶體使用率' })).getByText(/可用 8\.0 GiB/)).toBeInTheDocument()
  expect(screen.getByRole('group', { name: 'Load' })).toHaveTextContent('2.70')
  expect(screen.getByRole('group', { name: '磁碟讀寫' })).toHaveTextContent('2.0KiB/s')
  // 沒有觀測到的指標顯示「—」，不帶單位
  expect(screen.getByRole('group', { name: 'CPU／記憶體壓力（PSI，60 秒）' })).toHaveTextContent(/—記憶體 some —/)
  const fs = within(screen.getByRole('table', { name: '檔案系統' }))
  expect(fs.getByText('/home/kashionz/projects/report-mark')).toBeInTheDocument()
  expect(fs.getByText('24.6%')).toBeInTheDocument()
  expect(fs.getByText('700.0 GiB')).toBeInTheDocument()
  expect(screen.getByText(/粒度/)).toHaveTextContent('原始觀測（每 60 秒）')
  expect(screen.getByText('office-host')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '主機' })).toBeInTheDocument()
  expect(opsCalls(fetchMock)).toEqual([])
})

test('主機：有觀測的指標畫趨勢圖；資料不帶門檻，所以沒有門檻線也沒有 warn 色的柱子', async () => {
  mount('/admin/operations/host')
  // CPU 有兩筆（01:30 與 02:00），其餘各一筆
  expect(await screen.findByRole('img', { name: 'CPU 使用率趨勢：2 個資料點' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: '記憶體使用率趨勢：1 個資料點' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Load（1 分鐘）趨勢：1 個資料點' })).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'I/O 壓力（PSI some，60 秒）趨勢：1 個資料點' })).toBeInTheDocument()
  expect(document.querySelector('[title^="門檻"]')).toBeNull()
  expect(document.querySelector('[data-tone="warn"]')).toBeNull()
})

test('主機：資料超過上限時提示；聚合的粒度照後端回的標示', async () => {
  mount('/admin/operations/host', {
    override: url => url.startsWith('/api/admin/observations')
      ? { body: obsBody(HOST_OBS, { truncated: true, resolution: '5m' }) } : undefined,
  })
  expect(await screen.findByText('資料超過上限，只顯示最近的部分')).toBeInTheDocument()
  expect(screen.getByText(/粒度/)).toHaveTextContent('5 分鐘聚合')
})

test('主機：最近一小時沒有觀測時給出明確說明', async () => {
  mount('/admin/operations/host', {
    override: url => url.startsWith('/api/admin/observations') ? { body: obsBody([]) } : undefined,
  })
  expect(await screen.findByText(/最近一小時沒有主機觀測/)).toHaveTextContent('report-mark-load-observations.timer')
})

test('主機：後端錯誤原樣顯示，不白屏', async () => {
  mount('/admin/operations/host', {
    override: url => url.startsWith('/api/admin/observations')
      ? { status: 400, body: { detail: '時間範圍最多 90 天', code: 'invalid_params' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('主機觀測載入失敗：時間範圍最多 90 天')
})

test('trendSeries：依粒度切格、一格取最高值（聚合看 value_max），沒有觀測的格子是 null', () => {
  const data = obsBody([
    obs('cpu_pct', 10, { observed_at: '2026-10-06T01:00:10Z' }),
    obs('cpu_pct', 30, { observed_at: '2026-10-06T01:00:50Z' }),
    obs('cpu_pct', 20, { observed_at: '2026-10-06T01:59:30Z', value_max: 55 }),
    obs('cpu_pct', 99, { observed_at: '2026-10-06T00:59:00Z' }), // 區間外
    obs('cpu_pct', 99, { subject: 'fs:/' }), // 別的 subject
    obs('load1', 99), // 別的 metric
  ]) as unknown as ObservationListResponse
  const bars = trendSeries(data, 'host', 'cpu_pct')
  expect(bars).toHaveLength(60)
  expect(bars[0].value).toBe(30)
  expect(bars[59].value).toBe(55)
  expect(bars.filter(b => b.value != null)).toHaveLength(2)
  expect(trendSeries({ ...data, resolution: '5m' }, 'host', 'cpu_pct')).toHaveLength(12)
  expect(trendSeries({ ...data, until: data.since }, 'host', 'cpu_pct')).toEqual([])
})

test('沒有 ops.read：只顯示需要權限的說明，不打這幾頁的 API', async () => {
  const fetchMock = mount('/admin/operations/jobs', { scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「維運」權限' })).toBeInTheDocument()
  expect(calls(fetchMock).filter(u => u.startsWith('/api/admin'))).toEqual([])
})
