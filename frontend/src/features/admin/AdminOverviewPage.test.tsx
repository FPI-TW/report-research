import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import AdminOverviewPage from './AdminOverviewPage'
import { sinceForDays } from './analytics/analyticsFormat'
import { progressFixture } from './ops/pipeline/pipelineTestKit'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

const svc = (name: string, tier: string, summary: string) => ({
  name, kind: 'systemd', tier, target: `report-mark-${name}.service`, timer: null, actions: ['status'],
  description: `${name} 說明`, summary, error: null, systemd: null, container: null, timer_state: null,
})
const SERVICES = {
  environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
  items: [
    svc('web', 'critical', 'running'), svc('postgres', 'critical', 'running'), svc('nginx', 'critical', 'idle'),
    svc('sync', 'important', 'idle'), svc('backup', 'important', 'failed'), svc('freshness', 'supporting', 'idle'),
  ],
}
const section = { status: 'ok', available: true, stale: false, skipped: [], findings: [] }
const DATA_HEALTH = {
  generated_at: 'x', overall: 'ok', freshness: { status: 'ok', exit_code: 0, findings: [] },
  db_audit: section, r2_reconcile: { ...section, issues: [], issues_total: 0 },
}
const ALERTS = {
  state: 'ok', triggered: [], window_minutes: 15, login_failures: 0, login_failure_threshold: 20, accounts_over: [],
  max_account_failures: 0, account_failure_threshold: 5, elevate_failures: 0, elevate_failure_threshold: 5, audit_chain: 'ok',
}
const page = (total: number, extra: Record<string, unknown> = {}) =>
  ({ total, limit: 1, offset: 0, has_more: false, next_offset: null, items: [], ...extra })

const node = (name: string, kind: string, health: string, probe: string | null = null) => ({
  name, kind, tier: 'critical', target: null, description: `${name} 說明`, summary: null, health,
  health_reason: health === 'ok' ? '探針正常' : '沒有探針', probe, observed_at: null, depends_on: [], dependents: [],
  layer: 0, affected: false, impacted_by: [],
})
const DEPS = {
  environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
  nodes: [
    node('web', 'systemd', 'ok'),
    node('r2', 'external', 'ok', 'r2-reconcile'), node('deepseek', 'external', 'ok', 'llm-health'),
    node('nas', 'external', 'unknown'), node('slack', 'external', 'unknown'),
  ],
  edges: [], down: [], root_causes: [], affected: [],
}
const TOTP = {
  users_total: 12, users_enabled: 5, admins_total: 3, admins_enabled: 1, policy_required: false,
  admins_without_totp: [{ id: 'a1', username: 'amy.chen', is_super: false }, { id: 'a2', username: 'ops.bot', is_super: false }],
}

const day = (d: string, questions: number | null) => ({
  day: d, source: 'live', has_data: true, questions, askers: 2, active_users: 9, reading: 31, report_file: 1, search: 2,
  latency_p50_ms: 1200, latency_p95_ms: 4000, thinking_p50_ms: 300, thinking_p95_ms: 900,
})
const USAGE = {
  range: {
    since: '2026-10-05', until: '2026-10-07', today: '2026-10-07', live_since: '2026-07-10', timezone: 'Asia/Taipei',
    min_users: 3, spans: [{ since: '2026-10-05', until: '2026-10-07', source: 'live' }],
  },
  totals: { questions: 900, stopped: 2, reading: 50, report_file: 7, search: 30, active_users_peak: 9,
    distinct_users_live: 11, missing_days: 0 },
  latency: { p50_ms: 4200, p95_ms: 11800, thinking_p50_ms: 420, thinking_p95_ms: null, n: 98 },
  daily: [day('2026-10-05', 288), day('2026-10-06', 200), day('2026-10-07', 412)],
}
const auditEntry = (id: number, action: string, detail: Record<string, unknown>, actor: string | null = 'kashionz') => ({
  id, actor_user_id: actor ? 'u1' : null, actor_username: actor, action, target_type: 'user', target_id: null, detail,
  created_at: '2026-10-07T07:12:00Z',
})
const AUDIT = {
  total: 40, limit: 6, offset: 0, has_more: true, next_offset: 6,
  items: [
    auditEntry(6, 'user.disable', { username: 'jason.wu' }),
    auditEntry(5, 'user.create', { username: 'ops.bot', role: 'admin' }, null),
  ],
}

type Over = (url: string) => { status?: number; body: unknown } | undefined

function mount(scopes: string[], over?: Over) {
  const fetchMock = vi.fn(async (url: string) => {
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
    const o = over?.(url)
    if (o) return json(o.body, o.status)
    if (url === '/api/me') return json({ id: 'me', username: 'root', role: 'admin', scopes })
    if (url === '/api/admin/ops/services') return json(SERVICES)
    if (url === '/api/admin/ops/dependencies') return json(DEPS)
    if (url === '/api/progress') return json(progressFixture())
    if (url.startsWith('/api/admin/incidents')) return json({ since: 'a', until: 'b', ...page(0) })
    if (url === '/api/admin/data-health') return json(DATA_HEALTH)
    if (url === '/api/admin/security/alerts') return json(ALERTS)
    if (url === '/api/admin/security/totp-adoption') return json(TOTP)
    if (url.startsWith('/api/review/queue')) {
      const kind = new URLSearchParams(url.split('?')[1]).get('kind')!
      return json(page({ faithfulness: 8, feedback: 3, extraction: 5 }[kind] ?? 0, { kind }))
    }
    if (url.startsWith('/api/admin/uploads')) {
      const state = new URLSearchParams(url.split('?')[1]).get('state')
      return json(page(state === 'failed' ? 1 : 3, { scanner: { pending: 0, scanning: 0 } }))
    }
    if (url.startsWith('/api/admin/analytics/overview')) return json(USAGE)
    if (url.startsWith('/api/admin/audit')) return json(AUDIT)
    return json({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin']}><AdminOverviewPage /></MemoryRouter>
      <Toaster />
    </QueryClientProvider>,
  )
  return fetchMock
}

const ALL = ['admin', 'accounts.manage', 'audit.read', 'review.manage', 'ops.read', 'reports.manage', 'analytics.read']
const tile = (name: RegExp) => within(screen.getByRole('region', { name: '狀態' })).getByRole('link', { name })
const urlsOf = (fetchMock: ReturnType<typeof mount>) => fetchMock.mock.calls.map(c => String(c[0]))
const unavailable = { status: 503, body: { detail: '資料庫暫時無法使用，請稍後再試', code: 'db_unavailable' } }

test('狀態磚：服務有失敗時亮紅並列出是哪幾個，其餘正常；摘要一句講完', async () => {
  mount(ALL)
  expect(await screen.findByText('4／6')).toBeInTheDocument()
  expect(tile(/服務/)).toHaveAttribute('href', '/admin/operations/services')
  expect(tile(/服務/)).toHaveTextContent('2 個需要處理：nginx、backup')
  expect(await screen.findByText('近 24 小時沒有排程失敗')).toBeInTheDocument()
  expect(tile(/管線/)).toHaveAttribute('href', '/admin/operations/pipeline')
  expect(await screen.findByText('沒有進行中的事件')).toBeInTheDocument()
  expect(await screen.findByText('沒有觸發中的告警')).toBeInTheDocument()
  expect(tile(/安全/)).toHaveAttribute('href', '/admin/security')
  expect(await screen.findByText('新鮮度、稽核、R2 對帳都正常')).toBeInTheDocument()
  expect(await screen.findByText('1 項異常、0 項注意・16 筆待複核・3 份待審草稿')).toBeInTheDocument()
})

test('頁首：日期 kicker、H1、更新時間；重新整理把這一頁的資料重取一次', async () => {
  const fetchMock = mount(ALL)
  expect(await screen.findByRole('heading', { level: 1, name: '總覽' })).toBeInTheDocument()
  expect(screen.getByText(/^\d{4}-\d{2}-\d{2}（[日一二三四五六]）$/)).toBeInTheDocument()
  expect(await screen.findByText('4／6')).toBeInTheDocument()
  expect(await screen.findByText(/^\d{2}:\d{2}:\d{2}$/)).toBeInTheDocument()
  await waitFor(() => expect(tile(/外部依賴/)).toHaveTextContent('2／4'))
  await within(screen.getByRole('region', { name: '最近操作' })).findAllByRole('listitem')
  const count = (url: string) => urlsOf(fetchMock).filter(u => u === url).length
  const before = { services: count('/api/admin/ops/services'), deps: count('/api/admin/ops/dependencies'), me: count('/api/me') }
  fireEvent.click(screen.getByRole('button', { name: '重新整理' }))
  expect(await screen.findByText('已更新')).toBeInTheDocument()
  expect(count('/api/admin/ops/services')).toBe(before.services + 1)
  expect(count('/api/admin/ops/dependencies')).toBe(before.deps + 1)
  expect(count('/api/me')).toBe(before.me)   // 登入身分不是這一頁的資料
})

test('外部依賴：磚只數後端給的健康狀態，未監控用中性色、不算異常；卡片下方用虛線框列出', async () => {
  mount(ALL)
  await waitFor(() => expect(tile(/外部依賴/)).toHaveTextContent('2／4'))
  expect(tile(/外部依賴/)).toHaveAttribute('href', '/admin/operations/dependencies')
  expect(tile(/外部依賴/)).toHaveTextContent('nas、slack 未監控')
  expect(within(tile(/外部依賴/)).getByRole('img', { name: '閒置' })).toBeInTheDocument()
  const ext = within(await screen.findByRole('group', { name: '外部依賴' }))
  expect(ext.getByText('r2').closest('span')).toHaveAttribute('data-health', 'ok')
  expect(within(ext.getByText('nas').closest('span')!).getByRole('img', { name: '未監控' })).toBeInTheDocument()
  expect(ext.queryByText('web')).not.toBeInTheDocument()   // 只列外部節點
  const alerts = within(screen.getByRole('region', { name: '異常與注意' }))
  expect(alerts.queryByText(/外部依賴|未監控/)).not.toBeInTheDocument()
})

test('外部依賴故障：磚亮紅並寫出是哪一個', async () => {
  const down = { ...DEPS, nodes: DEPS.nodes.map(n => (n.name === 'r2' ? { ...n, health: 'down', health_reason: '探針失敗' } : n)) }
  mount(ALL, url => (url === '/api/admin/ops/dependencies' ? { body: down } : undefined))
  await waitFor(() => expect(tile(/外部依賴/)).toHaveTextContent('r2 故障'))
  expect(tile(/外部依賴/)).toHaveTextContent('1／4')
  expect(within(tile(/外部依賴/)).getByRole('img', { name: '異常' })).toBeInTheDocument()
})

test('依賴圖讀不到：外部依賴寫「讀不到」，其他區塊照常', async () => {
  mount(ALL, url => (url === '/api/admin/ops/dependencies' ? unavailable : undefined))
  await waitFor(() => expect(tile(/外部依賴/)).toHaveTextContent('讀不到'))
  expect(within(await screen.findByRole('group', { name: '外部依賴' })).getByText('讀不到')).toBeInTheDocument()
  expect(await screen.findByText('4／6')).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '核心服務' })).getByRole('link', { name: /^web/ })).toBeInTheDocument()
})

test('異常與注意：列出亮燈的頁面與原因，按鈕連到該頁', async () => {
  mount(ALL)
  const card = within(await screen.findByRole('region', { name: '異常與注意' }))
  expect(await card.findByText('2 個服務需要處理')).toBeInTheDocument()
  expect(card.getByText('nginx、backup')).toBeInTheDocument()
  expect(card.getByRole('link', { name: '看服務' })).toHaveAttribute('href', '/admin/operations/services')
})

test('待處理：四格只有數字，不算進異常', async () => {
  mount(ALL)
  const queues = within(await screen.findByRole('region', { name: '待處理' }))
  expect(await queues.findByText('16')).toBeInTheDocument()
  expect(queues.getByRole('link', { name: /待複核/ })).toHaveAttribute('href', '/admin/reviews')
  expect(queues.getByText('忠實度低分 8・倒讚 3・抽取品質 5')).toBeInTheDocument()
  expect(queues.getByRole('link', { name: /待審草稿/ })).toHaveAttribute('href', '/admin/uploads?tab=draft')
  const failed = await queues.findByRole('link', { name: /^1上傳處理失敗/ })
  expect(failed).toHaveAttribute('href', '/admin/uploads?tab=failed')
  const totp = await queues.findByRole('link', { name: /^2管理員未開啟兩步驟驗證/ })
  expect(totp).toHaveAttribute('href', '/admin/security')
  expect(totp).toHaveTextContent('amy.chen、ops.bot')
  const alerts = within(screen.getByRole('region', { name: '異常與注意' }))
  expect(alerts.queryByText(/待複核|上傳處理失敗|兩步驟驗證/)).not.toBeInTheDocument()
})

test('待處理的來源讀不到：那一格寫「讀不到」而不是 0，其他格照常', async () => {
  mount(ALL, url => (url === '/api/admin/security/totp-adoption' ? unavailable : undefined))
  const queues = within(await screen.findByRole('region', { name: '待處理' }))
  const totp = await queues.findByRole('link', { name: /管理員未開啟兩步驟驗證.*讀不到/ })
  expect(totp).toHaveTextContent('—')
  expect(await queues.findByRole('link', { name: /^1上傳處理失敗/ })).toBeInTheDocument()
})

test('服務：依層分組的晶片，各層正常／總數（排程服務閒置算正常）；需要處理的用紅框，點了開該服務的抽屜', async () => {
  mount(ALL)
  const core = within(await screen.findByRole('group', { name: '核心服務' }))
  expect(core.getByText('2／3')).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '重要服務' })).getByText('1／2')).toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '輔助服務' })).getByText('1／1')).toBeInTheDocument()
  const svcCard = within(screen.getByRole('region', { name: '服務' }))
  // 核心服務停著也算需要處理。
  const nginx = svcCard.getByRole('link', { name: 'nginx：閒置' })
  expect(nginx).toHaveAttribute('href', '/admin/operations/services?service=nginx')
  expect(nginx).toHaveAttribute('data-bad')
  expect(svcCard.getByRole('link', { name: 'backup：失敗' })).toHaveAttribute('data-bad')
  expect(svcCard.getByRole('link', { name: 'web：運作中' })).not.toHaveAttribute('data-bad')
  expect(svcCard.getByRole('link', { name: 'sync：閒置' })).not.toHaveAttribute('data-bad')
  expect(svcCard.getByRole('link', { name: '4／6 正常' })).toHaveAttribute('href', '/admin/operations/services')
})

test('全部正常：沒有異常', async () => {
  mount(ALL, url => url === '/api/admin/ops/services'
    ? { body: { ...SERVICES, items: [svc('web', 'critical', 'running')] } } : undefined)
  expect(await screen.findByText('沒有失敗的服務')).toBeInTheDocument()
  expect(tile(/服務/)).toHaveTextContent('1／1')
  expect(screen.getByText('沒有異常')).toBeInTheDocument()
})

test('維運代理不可用：服務磚寫「讀不到」，不講正常；其他磚照常', async () => {
  mount(ALL, url => url === '/api/admin/ops/services'
    ? { status: 503, body: { detail: '維運代理不可用', code: 'ops_agent_unavailable' } } : undefined)
  await waitFor(() => expect(tile(/服務/)).toHaveTextContent('讀不到'))
  expect(tile(/服務/)).toHaveTextContent('維運代理連不上')
  expect(await screen.findByText('近 24 小時沒有排程失敗')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '服務' })).not.toBeInTheDocument()
})

test('今日用量：今天的問答、活躍、閱讀與延遲 p95，每日問答的趨勢最後一根是今天', async () => {
  const fetchMock = mount(ALL)
  const usage = within(await screen.findByRole('region', { name: '今日用量' }))
  expect(await usage.findByRole('group', { name: '問答' })).toHaveTextContent('412')
  expect(usage.getByRole('group', { name: '活躍使用者' })).toHaveTextContent('9')
  expect(usage.getByRole('group', { name: '延遲 p95' })).toHaveTextContent('11.8秒')
  expect(usage.getByRole('group', { name: '閱讀' })).toHaveTextContent('31')
  expect(usage.getByRole('img', { name: '每日問答：3 個資料點' })).toBeInTheDocument()
  expect(usage.getByTitle('10/07・412 則').firstElementChild).toHaveAttribute('data-tone', 'gold')
  expect(usage.getByRole('link', { name: '使用分析' })).toHaveAttribute('href', '/admin/analytics')
  expect(urlsOf(fetchMock)).toContain(`/api/admin/analytics/overview?since=${sinceForDays(14)}`)
})

test('最近操作：最新的幾筆，連到操作紀錄', async () => {
  const fetchMock = mount(ALL)
  const recent = within(await screen.findByRole('region', { name: '最近操作' }))
  const rows = await recent.findAllByRole('listitem')
  expect(rows).toHaveLength(2)
  expect(rows[0]).toHaveTextContent('kashionz')
  expect(rows[0]).toHaveTextContent('停用帳號')
  expect(rows[0]).toHaveTextContent('jason.wu')
  expect(rows[1]).toHaveTextContent('指令列（CLI）')
  expect(recent.getByRole('link', { name: '全部紀錄' })).toHaveAttribute('href', '/admin/audit')
  expect(urlsOf(fetchMock)).toContain('/api/admin/audit?limit=6&offset=0')
})

test('一個來源壞掉只影響那一塊：用量與操作紀錄各自顯示錯誤，狀態磚照常', async () => {
  mount(ALL, url => (url.startsWith('/api/admin/analytics') || url.startsWith('/api/admin/audit') ? unavailable : undefined))
  const usage = within(await screen.findByRole('region', { name: '今日用量' }))
  expect(await usage.findByRole('alert')).toHaveTextContent('今日用量載入失敗：資料庫暫時無法使用')
  const recent = within(screen.getByRole('region', { name: '最近操作' }))
  expect(await recent.findByRole('alert')).toHaveTextContent('操作紀錄載入失敗：資料庫暫時無法使用')
  expect(await screen.findByText('4／6')).toBeInTheDocument()
  expect(await screen.findByText('16')).toBeInTheDocument()
})

test('沒有對應 scope 的區塊不畫，也不送那些請求', async () => {
  const fetchMock = mount(['admin', 'review.manage'])
  expect(await screen.findByText('0 項異常、0 項注意・16 筆待複核')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '狀態' })).not.toBeInTheDocument()
  expect(screen.queryByText('待審草稿')).not.toBeInTheDocument()
  expect(screen.queryByText('上傳處理失敗')).not.toBeInTheDocument()
  expect(screen.queryByText('管理員未開啟兩步驟驗證')).not.toBeInTheDocument()
  for (const name of ['服務', '今日用量', '最近操作']) {
    expect(screen.queryByRole('region', { name })).not.toBeInTheDocument()
  }
  expect(urlsOf(fetchMock).some(u => u.startsWith('/api/admin/') || u === '/api/progress')).toBe(false)
})

test('一般使用者：無權限頁，不送任何管理請求', async () => {
  const fetchMock = mount([], url => url === '/api/me'
    ? { body: { id: 'u', username: 'someone', role: 'user' } } : undefined)
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.map(c => c[0])).toEqual(['/api/me'])
})
