import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminUsersPage from './AdminUsersPage'

afterEach(() => vi.unstubAllGlobals())

type User = {
  id: string; username: string; role: 'admin' | 'user'; enabled: boolean; active_sessions: number
  last_login_at: string | null; last_seen_at: string | null
  is_super?: boolean; scopes?: string[]; totp_enabled?: boolean; deletion_execute_after?: string | null
}
type Reply = { status?: number; body: unknown }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const user = (id: string, username: string, over: Partial<User> = {}): User => ({
  id, username, role: 'user', enabled: true, active_sessions: 1,
  last_login_at: '2026-10-04T08:00:00Z', last_seen_at: null, ...over,
})

/** 小型假後端：帳號清單隨寫入變動，回傳的是 fetch mock（斷言送出了什麼）。 */
function mount(opts: { role?: 'admin' | 'user'; users?: User[]; override?: Override } = {}) {
  const role = opts.role ?? 'admin'
  let users = opts.users ?? [user('me', 'root', { role: 'admin' }), user('u2', 'alice')]
  const audit: unknown[] = []
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(init.body as string) : undefined
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role } }
      if (path === '/api/admin/users' && method === 'GET') return { body: { items: users } }
      if (path.startsWith('/api/admin/audit')) {
        return { body: { total: audit.length, limit: 20, offset: 0, has_more: false, next_offset: null, items: audit } }
      }
      if (path === '/api/admin/users' && method === 'POST') {
        const created = user('u3', body.username, { role: body.role, active_sessions: 0, last_login_at: null })
        users = [...users, created]
        audit.unshift({ id: audit.length + 1, actor_user_id: 'me', actor_username: 'root', action: 'user.create',
          target_type: 'user', target_id: 'u3', detail: { username: body.username, role: body.role }, created_at: null })
        return { status: 201, body: created }
      }
      const m = path.match(/^\/api\/admin\/users\/([^/]+)(\/(password|logout))?$/)
      if (m) {
        const target = users.find(u => u.id === m[1])!
        if (m[3] === 'logout') {
          const n = target.active_sessions
          users = users.map(u => u.id === target.id ? { ...u, active_sessions: 0 } : u)
          return { body: { revoked: n } }
        }
        if (m[3] === 'password') {
          users = users.map(u => u.id === target.id ? { ...u, active_sessions: 0 } : u)
          return { body: users.find(u => u.id === target.id) }
        }
        users = users.map(u => u.id === target.id
          ? { ...u, ...body, active_sessions: body.enabled === false ? 0 : u.active_sessions } : u)
        return { body: users.find(u => u.id === target.id) }
      }
      return { status: 404, body: { detail: 'not found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/users']}><AdminUsersPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const writes = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')

/** 帳號清單裡某個帳號的那一列。 */
async function row(name: string) {
  const table = within(await screen.findByRole('region', { name: '帳號清單' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr')!)
}

test('一般使用者：顯示無權限頁，完全不打 /api/admin/*', async () => {
  const fetchMock = mount({ role: 'user' })
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin'))).toBe(false)
})

test('帳號表格列出角色、狀態與有效 session；自己那列的停用與降級按鈕停用', async () => {
  mount()
  const me = await row('root')
  expect(me.getByText('（你）')).toBeInTheDocument()
  expect(me.getByRole('button', { name: '停用' })).toBeDisabled()
  expect(me.getByRole('button', { name: '改為一般使用者' })).toBeDisabled()
  expect(me.getByRole('button', { name: '停用' })).toHaveAttribute('title', '不能停用自己，也不能拿掉自己的管理員權限')
  const alice = await row('alice')
  expect(alice.getByText('一般使用者')).toBeInTheDocument()
  expect(alice.getByText('啟用')).toBeInTheDocument()
  expect(alice.getByRole('button', { name: '停用' })).toBeEnabled()
})

test('建立帳號：送出 username／password／role，成功後清單更新', async () => {
  const fetchMock = mount()
  await row('alice')
  fireEvent.change(screen.getByLabelText('帳號'), { target: { value: '  bob  ' } })
  fireEvent.change(screen.getByLabelText('初始密碼'), { target: { value: 'bob-password-1' } })
  fireEvent.change(screen.getByLabelText('角色'), { target: { value: 'admin' } })
  fireEvent.click(screen.getByRole('button', { name: '建立' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已建立帳號「bob」（管理員）')
  await row('bob')
  const post = writes(fetchMock)[0]
  expect(post[0]).toBe('/api/admin/users')
  expect(JSON.parse(post[1]!.body as string)).toEqual({ username: 'bob', password: 'bob-password-1', role: 'admin' })
  expect(screen.getByLabelText('帳號')).toHaveValue('')
})

test('建立帳號：後端 409 的 detail 原樣顯示', async () => {
  mount({ override: (p, init) => (p === '/api/admin/users' && init?.method === 'POST'
    ? { status: 409, body: { detail: '帳號「alice」已存在' } } : undefined) })
  await row('alice')
  fireEvent.change(screen.getByLabelText('帳號'), { target: { value: 'ALICE' } })
  fireEvent.change(screen.getByLabelText('初始密碼'), { target: { value: 'alice-password' } })
  fireEvent.click(screen.getByRole('button', { name: '建立' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('帳號「alice」已存在')
})

test('停用要先確認；確認後 PATCH enabled=false，清單反映停用與 session 歸零', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('alice')).getByRole('button', { name: '停用' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('停用「alice」？')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: '停用' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已停用「alice」'))
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/users/u2')
  expect(init!.method).toBe('PATCH')
  expect(JSON.parse(init!.body as string)).toEqual({ enabled: false })
  const alice = await row('alice')
  await waitFor(() => expect(alice.getByText('停用')).toBeInTheDocument())
  expect(alice.getByRole('button', { name: '啟用' })).toBeInTheDocument()
})

test('取消確認就什麼都不送', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('alice')).getByRole('button', { name: '強制登出' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(writes(fetchMock)).toHaveLength(0)
})

test('降級被後端以 409 拒絕（最後一位管理員）：detail 顯示給人看', async () => {
  mount({
    users: [user('me', 'root', { role: 'admin' }), user('a2', 'carol', { role: 'admin' })],
    override: (p, init) => (p === '/api/admin/users/a2' && init?.method === 'PATCH'
      ? { status: 409, body: { detail: '至少要保留一位啟用中的管理員' } } : undefined),
  })
  fireEvent.click((await row('carol')).getByRole('button', { name: '改為一般使用者' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '改為一般使用者' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('至少要保留一位啟用中的管理員')
})

test('設為管理員不需確認，直接 PATCH role=admin', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('alice')).getByRole('button', { name: '設為管理員' }))
  await waitFor(() => expect(writes(fetchMock)).toHaveLength(1))
  expect(JSON.parse(writes(fetchMock)[0][1]!.body as string)).toEqual({ role: 'admin' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('重設密碼：兩次不一致不送；一致才送，成功說明已登出所有裝置', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('alice')).getByRole('button', { name: '重設密碼' }))
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('新密碼'), { target: { value: 'new-password-1' } })
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'new-password-2' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('兩次輸入的密碼不一致')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'new-password-1' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已重設「alice」的密碼'))
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/users/u2/password')
  expect(JSON.parse(init!.body as string)).toEqual({ password: 'new-password-1' })
})

test('重設密碼：後端 400（政策不符）顯示在對話框裡', async () => {
  mount({ override: p => (p === '/api/admin/users/u2/password'
    ? { status: 400, body: { detail: '密碼至少 10 個字元' } } : undefined) })
  fireEvent.click((await row('alice')).getByRole('button', { name: '重設密碼' }))
  const dialog = await screen.findByRole('dialog')
  // 繞過瀏覽器端的 minLength（jsdom 的 form 驗證不擋 submit 事件），確認後端訊息會被顯示
  fireEvent.change(within(dialog).getByLabelText('新密碼'), { target: { value: 'short' } })
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'short' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('密碼至少 10 個字元')
})

test('強制登出：確認後 POST logout，回報撤銷數；沒有 session 的帳號按鈕停用', async () => {
  const fetchMock = mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { active_sessions: 2 }),
    user('u4', 'dave', { active_sessions: 0 })] })
  expect((await row('dave')).getByRole('button', { name: '強制登出' })).toBeDisabled()
  fireEvent.click((await row('alice')).getByRole('button', { name: '強制登出' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '強制登出' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已強制登出「alice」（2 個 session）'))
  expect(writes(fetchMock)[0][0]).toBe('/api/admin/users/u2/logout')
})

const deletionItem = (id: string, executeAfter: string, status = 'pending') => ({
  id: 1, user_id: id, username: 'alice', requested_by: 'me', requested_by_username: 'root',
  requested_at: '2026-10-06T00:00:00Z', execute_after: executeAfter, cancelled_at: null, executed_at: null, status,
})

test('刪除帳號：確認後收到 elevation_required，輸入密碼重新驗證後自動重試', async () => {
  let elevated = false
  const fetchMock = mount({
    override: (p, init) => {
      if (p === '/api/admin/users/u2/deletion') {
        return elevated
          ? { body: deletionItem('u2', '2026-10-07T00:00:00Z') }
          : { status: 403, body: { detail: '這項操作需要重新驗證密碼', code: 'elevation_required' } }
      }
      if (p === '/api/admin/elevate') {
        const body = JSON.parse(init!.body as string)
        if (body.password !== 'root-password-1') return { status: 403, body: { detail: '密碼不正確', code: 'bad_password' } }
        elevated = true
        return { body: { elevated_until: '2026-10-06T00:10:00Z' } }
      }
      return undefined
    },
  })
  expect((await row('root')).getByRole('button', { name: '刪除帳號' })).toBeDisabled()
  fireEvent.click((await row('alice')).getByRole('button', { name: '刪除帳號' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '排程刪除' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'wrong-password' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  expect(await within(elevation).findByRole('alert')).toHaveTextContent('密碼不正確')
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'root-password-1' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已排程刪除「alice」'))
  const deletions = fetchMock.mock.calls.filter(([p]) => p === '/api/admin/users/u2/deletion')
  expect(deletions).toHaveLength(2)
})

test('排程刪除中的帳號顯示倒數與「取消刪除」，其他操作收起', async () => {
  const soon = new Date(Date.now() + (3 * 60 + 20) * 60_000).toISOString()
  const fetchMock = mount({
    users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { enabled: false, deletion_execute_after: soon })],
    override: p => (p === '/api/admin/users/u2/deletion/cancel' ? { body: deletionItem('u2', soon, 'cancelled') } : undefined),
  })
  const alice = await row('alice')
  expect(alice.getByText('排程刪除')).toBeInTheDocument()
  expect(alice.getByText(/3 小時 2\d 分後刪除/)).toBeInTheDocument()
  expect(alice.queryByRole('button', { name: '停用' })).not.toBeInTheDocument()
  fireEvent.click(alice.getByRole('button', { name: '取消刪除' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已取消刪除「alice」'))
  expect(writes(fetchMock)[0][0]).toBe('/api/admin/users/u2/deletion/cancel')
})

test('super admin 才看得到「調整權限」；送出完整的 scope 清單', async () => {
  const fetchMock = mount({
    users: [user('me', 'root', { role: 'admin', is_super: true }), user('u2', 'alice', { role: 'admin', scopes: [] })],
    override: (p, init) => {
      if (p === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', is_super: true, scopes: [] } }
      if (p === '/api/admin/users/u2/privileges') {
        const body = JSON.parse(init!.body as string)
        return { body: user('u2', 'alice', { role: 'admin', ...body }) }
      }
      return undefined
    },
  })
  fireEvent.click((await row('alice')).getByRole('button', { name: '調整權限' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的權限' })
  fireEvent.click(within(dialog).getByLabelText(/qa_content.read/))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「alice」的權限'))
  const [, init] = writes(fetchMock).find(([p]) => p === '/api/admin/users/u2/privileges')!
  expect(init!.method).toBe('PUT')
  expect(JSON.parse(init!.body as string)).toEqual({ is_super: false, scopes: ['qa_content.read'] })
})

test('非 super 的管理員沒有「調整權限」；開了 2FA 的帳號有「重設兩步驟驗證」', async () => {
  mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { role: 'admin', totp_enabled: true })] })
  const alice = await row('alice')
  expect(alice.queryByRole('button', { name: '調整權限' })).not.toBeInTheDocument()
  expect(alice.getByRole('button', { name: '重設兩步驟驗證' })).toBeEnabled()
})
