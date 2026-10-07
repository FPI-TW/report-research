import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminSecurityPage from './AdminSecurityPage'
import { eventLabel } from './securityLabels'

afterEach(() => vi.unstubAllGlobals())

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

function mount({ role = 'admin', scopes = ADMIN_SCOPES, elevated = false } = {}) {
  let isElevated = elevated
  let revoked = false
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x')
    const path = url.pathname
    if (path === '/api/me') return json({ id: 'u-root', username: 'root', role, scopes, is_super: false, totp_enabled: false })
    if (path === '/api/admin/security/alerts') return json(ALERTS)
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
    if (path === '/api/admin/elevate' && init?.method === 'POST') {
      isElevated = true
      return json({ elevated_until: '2026-10-07T02:10:00Z' })
    }
    return json({ detail: 'not mocked' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/security']}><AdminSecurityPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const called = (fetchMock: ReturnType<typeof mount>, prefix: string) =>
  fetchMock.mock.calls.some(([p]) => String(p).startsWith(prefix))

test('安全頁：告警、稽核鏈與錨定、TOTP 採用率（未開的管理員標紅）、可疑 IP、事件、高風險時間線', async () => {
  mount()
  expect(await screen.findByRole('heading', { name: '安全' })).toBeInTheDocument()
  const alerts = await screen.findByRole('region', { name: '告警狀態' })
  expect(await within(alerts).findAllByText('全站登入失敗過多')).not.toHaveLength(0)
  expect(within(alerts).getByText('15 分鐘內登入失敗')).toBeInTheDocument()

  const chain = screen.getByRole('region', { name: '稽核鏈' })
  expect(await within(chain).findByText('完整')).toBeInTheDocument()
  expect(within(chain).getByText('超過 48 小時沒有新的錨定')).toBeInTheDocument()

  const totp = screen.getByRole('region', { name: '兩步驟驗證採用率' })
  expect(await within(totp).findByText('nototp-admin')).toHaveClass(/redName/)
  expect(within(totp).getByText('5／12（42%）')).toBeInTheDocument()
  expect(within(totp).getByText(/不強制/)).toBeInTheDocument()

  const events = screen.getByRole('region', { name: '登入事件' })
  expect(await within(events).findByText(eventLabel('login.failure', 'unknown_user'))).toBeInTheDocument()
  expect(within(events).getByRole('cell', { name: '37' })).toBeInTheDocument()

  const risk = screen.getByRole('region', { name: '高風險操作' })
  expect(await within(risk).findByRole('cell', { name: '權限與角色' })).toBeInTheDocument()
  expect(within(risk).getByText('alice・設為 super admin')).toBeInTheDocument()
})

test('登入事件：較舊／較新以 before_id 翻頁；可疑 IP 的「看事件」帶入 IP 篩選', async () => {
  const fetchMock = mount()
  const events = await screen.findByRole('region', { name: '登入事件' })
  await within(events).findByText(eventLabel('login.failure', 'unknown_user'))
  fireEvent.click(within(events).getByRole('button', { name: '較舊' }))
  await within(events).findByText('沒有符合條件的事件')
  expect(fetchMock.mock.calls.some(([p]) => String(p).includes('before_id=8'))).toBe(true)

  const ips = screen.getByRole('region', { name: '可疑 IP' })
  fireEvent.click(await within(ips).findByRole('button', { name: '看事件' }))
  expect(within(events).getByLabelText('IP')).toHaveValue('203.0.113.9')
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) =>
    String(p).startsWith('/api/admin/security/events') && String(p).includes('ip=203.0.113.9')
    && !String(p).includes('before_id'))).toBe(true))
})

test('撤銷單一 session：先確認，後端要求重新驗證時彈出驗證框，驗證後自動重試', async () => {
  const fetchMock = mount()
  const sessions = await screen.findByRole('region', { name: 'Session' })
  await within(sessions).findByText('alice')
  expect(within(sessions).getByText('（目前這個）')).toBeInTheDocument()
  fireEvent.click(within(sessions).getAllByRole('button', { name: '撤銷' })[0])
  fireEvent.click(within(sessions).getByRole('button', { name: '確定撤銷' }))
  await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(screen.getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate1' } })
  fireEvent.click(screen.getByRole('button', { name: '驗證' }))
  expect(await within(sessions).findByText('已撤銷 alice 的這個 session。')).toBeInTheDocument()
  const revokes = fetchMock.mock.calls.filter(([p]) => String(p) === '/api/admin/security/sessions/s-alice/revoke')
  expect(revokes).toHaveLength(2)
  expect(await within(sessions).findByText('已撤銷')).toBeInTheDocument()
})

test('沒有 accounts.manage：不顯示 session 卡、不打 session API', async () => {
  const fetchMock = mount({ scopes: ['admin', 'audit.read'] })
  await screen.findByRole('region', { name: '告警狀態' })
  expect(screen.queryByRole('region', { name: 'Session' })).not.toBeInTheDocument()
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
