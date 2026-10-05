import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminUsersPage from './AdminUsersPage'

afterEach(() => vi.unstubAllGlobals())

type User = {
  id: string; username: string; role: 'admin' | 'user'; enabled: boolean; active_sessions: number
  last_login_at: string | null; last_seen_at: string | null
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

/** 帳號清單裡某個帳號的那一列（只在帳號表裡找：稽核表的內容欄也會出現帳號名）。 */
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

test('建立帳號：送出 username／password／role，成功後清單與稽核都更新', async () => {
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
  // 稽核表重抓到新那一筆，action 轉成中文
  expect(await screen.findByRole('cell', { name: '建立帳號' })).toBeInTheDocument()
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

test('稽核表：CLI 操作顯示為指令列、未知 action 原樣顯示、可翻頁', async () => {
  const items = [
    { id: 2, actor_user_id: null, actor_username: null, action: 'user.create', target_type: 'user', target_id: 'u1',
      detail: { username: 'root', role: 'admin', via: 'cli' }, created_at: '2026-10-05T01:00:00Z' },
    { id: 1, actor_user_id: 'me', actor_username: 'root', action: 'user.something_new', target_type: 'user',
      target_id: 'u2', detail: { username: 'alice' }, created_at: '2026-10-05T00:00:00Z' },
  ]
  const fetchMock = mount({ override: p => {
    if (!p.startsWith('/api/admin/audit')) return undefined
    const offset = Number(new URL(p, 'http://x').searchParams.get('offset'))
    return { body: offset === 0
      ? { total: 21, limit: 20, offset: 0, has_more: true, next_offset: 20, items }
      : { total: 21, limit: 20, offset: 20, has_more: false, next_offset: null, items: [items[1]] } }
  } })
  expect(await screen.findByRole('cell', { name: '指令列（CLI）' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'root・管理員・經指令列' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'user.something_new' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '下一頁' }))
  await screen.findByText('第 21–21 筆，共 21 筆')
  expect(fetchMock.mock.calls.some(([p]) => String(p).includes('offset=20'))).toBe(true)
})
