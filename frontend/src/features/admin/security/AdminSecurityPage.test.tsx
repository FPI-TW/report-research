import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import AdminSecurityPage from './AdminSecurityPage'
import { eventLabel, eventTone } from './securityLabels'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

const ADMIN_SCOPES = ['admin', 'accounts.manage', 'audit.read', 'ops.read']

const ALERTS = {
  state: 'login_failures', triggered: ['login_failures'], window_minutes: 15, login_failures: 23,
  login_failure_threshold: 20, accounts_over: [], max_account_failures: 2, account_failure_threshold: 5,
  elevate_failures: 0, elevate_failure_threshold: 3, audit_chain: 'ok', events_error: null,
}
const CHAIN = {
  live: { state: 'ok', total: 42, head_id: 42, broken_count: 0, checked_at: '2026-10-07T01:00:00Z', error: null,
    cache_seconds: 3600 },
  anchor: { state: 'stale', at: '2026-10-04T04:15:00+08:00', written_at: '2026-10-03T20:15:00Z', age_hours: 77,
    head_id: 40, total: 40, anchors_checked: 3, message: '先前 3 個錨點全部相符，已追加今天的錨點' },
}
const TOTP = {
  users_total: 12, users_enabled: 5, admins_total: 3, admins_enabled: 1,
  admins_without_totp: [{ id: 'a2', username: 'nototp-admin', is_super: true }], policy_required: false,
}
const EVENTS = {
  items: [
    { id: 9, occurred_at: '2026-10-07T01:00:00Z', event: 'login.failure', reason: 'unknown_user', user_id: null,
      username: null, session_id: null, ip: '203.0.113.9', user_agent: 'curl/8', count: 1 },
    { id: 8, occurred_at: '2026-10-07T00:59:00Z', event: 'login.locked', reason: null, user_id: null, username: null,
      session_id: null, ip: '203.0.113.9', user_agent: null, count: 37 },
  ],
  next_before_id: 8,
}
const IPS = {
  hours: 24, min_failures: 5,
  items: [{ ip: '203.0.113.9', failures: 6, locked: 37, insecure: 0, successes: 0, distinct_users: 0,
    first_seen: '2026-10-07T00:50:00Z', last_seen: '2026-10-07T01:00:00Z' }],
}
const SESSION = {
  id: 's-alice', user_id: 'u-alice', username: 'alice', created_at: '2026-10-07T00:00:00Z',
  last_seen_at: '2026-10-07T00:30:00Z', expires_at: '2026-11-06T00:00:00Z', revoked_at: null, ip: '10.0.0.5',
  user_agent: 'Mozilla/5.0', elevated_until: null, active: true, current: false,
}
const MINE = { ...SESSION, id: 's-root', user_id: 'u-root', username: 'root', current: true }
const RISK = {
  days: 30, next_before_id: null,
  items: [{ id: 5, category: 'privilege', action: 'user.set_privileges', actor_user_id: 'u-root', actor_username: 'root',
    target_type: 'user', target_id: 'u-alice', detail: { username: 'alice', is_super: { from: false, to: true } },
    created_at: '2026-10-06T02:00:00Z' }],
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** 目前的 query。不用 <output>：它隱含 role="status"，會跟 toast 撞。 */
function Where() {
  return <span data-testid="where">{useLocation().search}</span>
}

interface MountOptions {
  role?: string
  scopes?: string[]
  /** 後端認為這個 session 已經重新驗證過。 */
  elevated?: boolean
  /** `/api/me` 回報的到期時間：前端據此決定要不要先問。 */
  elevatedUntil?: string | null
  search?: string
  alerts?: Record<string, unknown>
}

function mount({ role = 'admin', scopes = ADMIN_SCOPES, elevated = false, elevatedUntil = null, search = '', alerts = ALERTS }: MountOptions = {}) {
  let isElevated = elevated
  let revoked = false
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x')
    const path = url.pathname
    if (path === '/api/me') {
      return json({ id: 'u-root', username: 'root', role, scopes, is_super: false, totp_enabled: false, elevated_until: elevatedUntil })
    }
    if (path === '/api/admin/security/alerts') return json(alerts)
    if (path === '/api/admin/security/audit-chain') return json(CHAIN)
    if (path === '/api/admin/security/totp-adoption') return json(TOTP)
    if (path === '/api/admin/security/events') return json(url.searchParams.get('before_id') ? { items: [], next_before_id: null } : EVENTS)
    if (path === '/api/admin/security/suspicious-ips') return json(IPS)
    if (path === '/api/admin/security/high-risk') return json(RISK)
    if (path === '/api/admin/security/sessions') return json({ items: [revoked ? { ...SESSION, active: false, revoked_at: '2026-10-07T02:00:00Z' } : SESSION, MINE] })
    if (path === '/api/admin/security/sessions/s-alice/revoke' && init?.method === 'POST') {
      if (!isElevated) return json({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403)
      revoked = true
      return json({ ...SESSION, active: false, revoked_at: '2026-10-07T02:00:00Z' })
    }
    if (path === '/api/me/elevate' && init?.method === 'POST') {
      isElevated = true
      return json({ elevated_until: new Date(Date.now() + 600_000).toISOString() })
    }
    return json({ detail: 'not mocked' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/admin/security${search}`]}>
        <AdminSecurityPage />
        <Where />
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const called = (fetchMock: ReturnType<typeof mount>, prefix: string) =>
  fetchMock.mock.calls.some(([p]) => String(p).startsWith(prefix))
const eventCalls = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.map(([p]) => String(p)).filter(p => p.startsWith('/api/admin/security/events'))
const revokeCalls = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([p]) => String(p) === '/api/admin/security/sessions/s-alice/revoke')
const where = () => screen.getByTestId('where').textContent

test('概況：觸發中的告警橫幅、計數磚（達門檻標紅）、稽核鏈與錨定、TOTP 採用率（未開的管理員標出）', async () => {
  mount()
  expect(await screen.findByRole('heading', { level: 1, name: '安全' })).toBeInTheDocument()
  const banner = await screen.findByRole('alert', { name: '觸發中的告警' })
  expect(within(banner).getByText('全站登入失敗過多')).toBeInTheDocument()
  expect(banner).toHaveAttribute('data-tone', 'bad')

  const tiles = await screen.findByRole('region', { name: '告警狀態' })
  expect(within(tiles).getByText('15 分鐘內登入失敗')).toBeInTheDocument()
  expect(within(tiles).getByText('23')).toHaveAttribute('data-tone', 'bad')
  expect(within(tiles).getByText('門檻 20')).toBeInTheDocument()
  expect(within(tiles).getByText('2')).not.toHaveAttribute('data-tone')
  expect(await within(tiles).findByText('1／3')).toBeInTheDocument()

  const chain = screen.getByRole('region', { name: '稽核鏈' })
  expect(await within(chain).findByText('完整')).toBeInTheDocument()
  expect(within(chain).getByText('42 列')).toBeInTheDocument()
  expect(within(chain).getByText('超過 48 小時沒有新的錨定')).toBeInTheDocument()
  expect(within(chain).getByText('先前 3 個錨點全部相符，已追加今天的錨點')).toBeInTheDocument()

  const totp = screen.getByRole('region', { name: '兩步驟驗證採用率' })
  expect(await within(totp).findByText('nototp-admin')).toHaveAttribute('data-tone', 'bad')
  expect(within(totp).getByText('super')).toBeInTheDocument()
  expect(within(totp).getByText('5／12（42%）')).toBeInTheDocument()
  expect(within(totp).getByText(/不強制/)).toBeInTheDocument()

  // 長說明收在「說明」後面
  expect(screen.queryByText(/送 Slack 開場/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/送 Slack 開場/)).toBeInTheDocument()
})

test('沒有觸發的告警：不顯示橫幅；判斷不出來與事件查詢失敗另外講明', async () => {
  mount({ alerts: { ...ALERTS, state: 'unknown', triggered: [], login_failures: 0, events_error: 'OperationalError' } })
  const tiles = await screen.findByRole('region', { name: '告警狀態' })
  expect(within(tiles).getByText('15 分鐘內登入失敗')).toBeInTheDocument()
  expect(screen.queryByRole('alert', { name: '觸發中的告警' })).not.toBeInTheDocument()
  const partial = screen.getByRole('status', { name: '告警判斷不完整' })
  expect(within(partial).getByText('告警狀態判斷不出來')).toBeInTheDocument()
  expect(within(partial).getByText(/登入事件查詢失敗（OperationalError）/)).toBeInTheDocument()
})

test('連續失敗達門檻的帳號列在橫幅裡（只告警、不鎖帳號）', async () => {
  mount({ alerts: { ...ALERTS, state: 'account_failures', triggered: ['account_failures', 'login_failures'],
    accounts_over: [{ user_id: 'u-9', username: 'trader02' }, { user_id: 'abcdef0123456789', username: null }] } })
  const banner = await screen.findByRole('alert', { name: '觸發中的告警' })
  expect(within(banner).getByText('有帳號連續登入失敗、全站登入失敗過多')).toBeInTheDocument()
  expect(within(banner).getByText(/trader02、abcdef01/)).toHaveTextContent('只告警、不鎖帳號')
})

test('分頁放在 URL：事件、高風險各自的內容；沒有的分頁值退回概況', async () => {
  mount({ search: '?tab=nope' })
  expect(await screen.findByRole('tab', { name: '概況' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.click(screen.getByRole('tab', { name: '高風險' }))
  expect(where()).toBe('?tab=risk')
  const risk = await screen.findByRole('region', { name: '高風險操作' })
  expect(await within(risk).findByRole('cell', { name: '權限與角色' })).toBeInTheDocument()
  expect(within(risk).getByText('alice・設為 super admin')).toBeInTheDocument()
  expect(within(risk).getByRole('cell', { name: '調整權限' })).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '稽核鏈' })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: '事件' }))
  expect(where()).toBe('?tab=events')
  const events = await screen.findByRole('region', { name: '登入事件' })
  expect(await within(events).findByText(eventLabel('login.failure', 'unknown_user'))).toHaveAttribute('data-tone', 'bad')
  // 類型下拉也有同名的選項：只看表格裡的那一個。
  const eventTable = within(within(events).getByRole('table'))
  expect(eventTable.getByText(eventLabel('login.locked'))).toHaveAttribute('data-tone', 'warn')
  expect(eventTable.getByRole('cell', { name: '37' })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: '概況' }))
  expect(where()).toBe('')
})

test('高風險操作：分類與期間篩選帶進請求', async () => {
  const fetchMock = mount({ search: '?tab=risk' })
  const risk = await screen.findByRole('region', { name: '高風險操作' })
  await within(risk).findByRole('cell', { name: '權限與角色' })
  fireEvent.change(within(risk).getByLabelText('分類'), { target: { value: 'ops' } })
  fireEvent.click(within(risk).getByRole('button', { name: '90 天' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) =>
    String(p).startsWith('/api/admin/security/high-risk') && String(p).includes('category=ops') && String(p).includes('days=90'))).toBe(true))
})

test('登入事件：較舊／較新以 before_id 翻頁，翻到空頁還能翻回來', async () => {
  const fetchMock = mount({ search: '?tab=events' })
  const events = await screen.findByRole('region', { name: '登入事件' })
  await within(events).findByText(eventLabel('login.failure', 'unknown_user'))
  expect(within(events).getByRole('button', { name: '較新' })).toBeDisabled()
  fireEvent.click(within(events).getByRole('button', { name: '較舊' }))
  await within(events).findByText('沒有符合條件的事件')
  expect(eventCalls(fetchMock).some(p => p.includes('before_id=8'))).toBe(true)
  expect(within(events).getByRole('button', { name: '較舊' })).toBeDisabled()
  fireEvent.click(within(events).getByRole('button', { name: '較新' }))
  expect(await within(events).findByText(eventLabel('login.failure', 'unknown_user'))).toBeInTheDocument()
})

test('可疑 IP 的「看事件」把 IP 放進 URL 與登入事件的篩選標籤，可清除；翻頁回到第一頁', async () => {
  const fetchMock = mount({ search: '?tab=events' })
  const events = await screen.findByRole('region', { name: '登入事件' })
  await within(events).findByText(eventLabel('login.failure', 'unknown_user'))
  fireEvent.click(within(events).getByRole('button', { name: '較舊' }))
  await within(events).findByText('沒有符合條件的事件')

  const ips = screen.getByRole('region', { name: '可疑 IP' })
  fireEvent.click(await within(ips).findByRole('button', { name: '看事件' }))
  expect(where()).toBe('?tab=events&ip=203.0.113.9')
  expect(within(events).getByLabelText('IP')).toHaveValue('203.0.113.9')
  expect(within(events).getByText('IP 203.0.113.9')).toBeInTheDocument()
  await waitFor(() => expect(eventCalls(fetchMock).some(p => p.includes('ip=203.0.113.9') && !p.includes('before_id'))).toBe(true))

  const before = eventCalls(fetchMock).length
  fireEvent.click(within(events).getByRole('button', { name: '清除 IP 篩選' }))
  expect(where()).toBe('?tab=events')
  expect(within(events).getByLabelText('IP')).toHaveValue('')
  expect(within(events).queryByText('IP 203.0.113.9')).not.toBeInTheDocument()
  await waitFor(() => expect(eventCalls(fetchMock).length).toBeGreaterThanOrEqual(before))
  expect(eventCalls(fetchMock).at(-1)).not.toContain('ip=')
})

test('IP 輸入框：停手後才套用到 URL 與請求；連結帶 ?ip= 進來就直接套用', async () => {
  const fetchMock = mount({ search: '?tab=events&ip=198.51.100.7' })
  const events = await screen.findByRole('region', { name: '登入事件' })
  expect(within(events).getByLabelText('IP')).toHaveValue('198.51.100.7')
  await waitFor(() => expect(eventCalls(fetchMock).some(p => p.includes('ip=198.51.100.7'))).toBe(true))
  fireEvent.change(within(events).getByLabelText('IP'), { target: { value: ' 10.0.0.5 ' } })
  expect(where()).toBe('?tab=events&ip=198.51.100.7')
  await waitFor(() => expect(where()).toBe('?tab=events&ip=10.0.0.5'))
  await waitFor(() => expect(eventCalls(fetchMock).some(p => p.includes('ip=10.0.0.5'))).toBe(true))
  expect(within(events).getByText('IP 10.0.0.5')).toBeInTheDocument()
})

test('登入事件的類型篩選帶進請求', async () => {
  const fetchMock = mount({ search: '?tab=events' })
  const events = await screen.findByRole('region', { name: '登入事件' })
  fireEvent.change(within(events).getByLabelText('類型'), { target: { value: 'login.locked' } })
  await waitFor(() => expect(eventCalls(fetchMock).some(p => p.includes('event=login.locked'))).toBe(true))
})

test('撤銷單一 session：先確認，再重新驗證身分，成功後用 toast 回報', async () => {
  const fetchMock = mount({ search: '?tab=sessions' })
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('alice')
  expect(within(sessions).getByText('目前這個')).toBeInTheDocument()
  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[0])
  const confirm = await screen.findByRole('alertdialog', { name: '撤銷 alice 的這個 session？' })
  expect(within(confirm).getByText('只登出這一個裝置，其他裝置不受影響。')).toBeInTheDocument()
  expect(revokeCalls(fetchMock)).toHaveLength(0)
  fireEvent.click(within(confirm).getByRole('button', { name: '撤銷' }))
  const elevate = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(within(elevate).getByText(/「撤銷 session」需要重新驗證/)).toBeInTheDocument()
  expect(revokeCalls(fetchMock)).toHaveLength(0)
  fireEvent.change(within(elevate).getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate1' } })
  fireEvent.click(within(elevate).getByRole('button', { name: '驗證' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已撤銷 alice 的 session')
  expect(revokeCalls(fetchMock)).toHaveLength(1)
  expect(await within(sessions).findByText('已撤銷')).toBeInTheDocument()
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})

test('撤銷：前端以為還在驗證視窗內、後端卻要求重新驗證時，彈出驗證框，驗證後自動重試', async () => {
  const fetchMock = mount({ search: '?tab=sessions', elevatedUntil: new Date(Date.now() + 300_000).toISOString() })
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('alice')
  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[0])
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '撤銷' }))
  const elevate = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(revokeCalls(fetchMock)).toHaveLength(1)
  fireEvent.change(within(elevate).getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate2' } })
  fireEvent.click(within(elevate).getByRole('button', { name: '驗證' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已撤銷 alice 的 session')
  expect(revokeCalls(fetchMock)).toHaveLength(2)
})

test('撤銷：確認框按取消、或重新驗證按取消，都不送出', async () => {
  const fetchMock = mount({ search: '?tab=sessions' })
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('alice')
  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[0])
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()

  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[0])
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '撤銷' }))
  fireEvent.click(within(await screen.findByRole('dialog', { name: '重新驗證身分' })).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  expect(revokeCalls(fetchMock)).toHaveLength(0)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

test('撤銷自己目前的 session：確認框講明後果', async () => {
  mount({ search: '?tab=sessions' })
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('root')
  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[1])
  const confirm = await screen.findByRole('alertdialog', { name: '撤銷 root 的這個 session？' })
  expect(within(confirm).getByText(/下一個請求就會回到登入頁/)).toBeInTheDocument()
})

test('Session 範圍：切到「全部」改送 active_only=false', async () => {
  const fetchMock = mount({ search: '?tab=sessions' })
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('alice')
  expect(fetchMock.mock.calls.some(([p]) => String(p).includes('active_only=true'))).toBe(true)
  fireEvent.click(within(sessions).getByRole('button', { name: '全部' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p).includes('active_only=false'))).toBe(true))
})

test('沒有 accounts.manage：沒有 Session 分頁、不打 session API（網址帶 ?tab=sessions 也一樣）', async () => {
  const fetchMock = mount({ scopes: ['admin', 'audit.read'], search: '?tab=sessions' })
  await screen.findByRole('region', { name: '告警狀態' })
  expect(screen.queryByRole('tab', { name: 'Session' })).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Session' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '概況' })).toHaveAttribute('aria-selected', 'true')
  expect(called(fetchMock, '/api/admin/security/sessions')).toBe(false)
})

test('沒有 audit.read：只說明需要的權限，不打安全 API', async () => {
  const fetchMock = mount({ scopes: ['admin', 'accounts.manage'] })
  expect(await screen.findByRole('heading', { name: '需要「安全」權限' })).toBeInTheDocument()
  expect(called(fetchMock, '/api/admin/security')).toBe(false)
})

test('一般使用者：無權限頁，不打任何管理 API', async () => {
  const fetchMock = mount({ role: 'user', scopes: [] })
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(called(fetchMock, '/api/admin')).toBe(false)
})

test('權限提升給了驗證碼卻失敗的 reason 有自己的標籤', () => {
  expect(eventLabel('elevate.failure', 'bad_credentials')).toBe('重新驗證失敗（密碼或驗證碼錯誤）')
  expect(eventLabel('login.failure', 'bad_password')).toBe('登入失敗（密碼錯誤）')
})

test('事件的語氣色：失敗標紅、被擋下的彙總列標橘，其餘與未知事件不上色', () => {
  expect(eventTone('login.totp_failure')).toBe('bad')
  expect(eventTone('elevate.failure')).toBe('bad')
  expect(eventTone('login.insecure')).toBe('warn')
  expect(eventTone('login.success')).toBeUndefined()
  expect(eventTone('something.new')).toBeUndefined()
})
