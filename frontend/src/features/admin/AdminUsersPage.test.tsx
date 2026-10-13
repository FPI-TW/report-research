import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import AdminUsersPage from './AdminUsersPage'
import c from '../../components/console/Console.module.css'
import styles from './users/Users.module.css'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

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

/** `/api/me` 的 `elevated_until` 還沒到期：重新驗證的閘門直接放行。 */
const STILL_ELEVATED = () => new Date(Date.now() + 5 * 60_000).toISOString()
const ELEVATION_REQUIRED: Reply = { status: 403, body: { detail: '這項操作需要重新驗證密碼', code: 'elevation_required' } }

/** 小型假後端：帳號清單隨寫入變動，回傳的是 fetch mock（斷言送出了什麼）。 */
function mount(opts: {
  role?: 'admin' | 'user'; users?: User[]; override?: Override; me?: Record<string, unknown>; url?: string
} = {}) {
  const role = opts.role ?? 'admin'
  let users = opts.users ?? [user('me', 'root', { role: 'admin' }), user('u2', 'alice')]
  const audit: unknown[] = []
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(init.body as string) : undefined
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role, ...opts.me } }
      if (path === '/api/me/elevate') return { body: { elevated_until: STILL_ELEVATED() } }
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
      <MemoryRouter initialEntries={[opts.url ?? '/admin/users']}><AdminUsersPage /><Toaster /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const writes = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')
const callsTo = (fetchMock: ReturnType<typeof mount>, path: string) => fetchMock.mock.calls.filter(([p]) => p === path)

/** 帳號清單裡某個帳號的那一列。 */
async function row(name: string) {
  const table = within(await screen.findByRole('region', { name: '帳號清單' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr')!)
}

/** 打開某一列的「更多」選單（portal 到 body）。 */
async function menu(name: string) {
  fireEvent.click((await row(name)).getByRole('button', { name: `${name} 的更多操作` }))
  return within(await screen.findByRole('menu', { name: `${name} 的更多操作` }))
}

/** 操作結果的 toast（成功是 ok，失敗是 bad）。 */
async function expectToast(text: string | RegExp, tone: 'ok' | 'bad' = 'ok') {
  const el = (await screen.findByText(text)).closest('[role="status"]')
  expect(el).toHaveAttribute('data-tone', tone)
}

/** 在「重新驗證身分」輸入密碼送出。 */
async function reverify(password = 'root-password-1') {
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: password } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  return within(elevation)
}

test('一般使用者：顯示無權限頁，完全不打 /api/admin/*', async () => {
  const fetchMock = mount({ role: 'user' })
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin'))).toBe(false)
})

test('頁首：摘要一句、長說明收在「說明」後面；表格底下保留生效時機的說明與統計', async () => {
  mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { active_sessions: 2 }),
    user('u3', 'bob', { enabled: false, active_sessions: 0 })] })
  await row('alice')
  expect(screen.getByRole('heading', { level: 1, name: '帳號管理' })).toBeInTheDocument()
  expect(screen.getByText(/建立帳號、調整角色與權限、停用或刪除。/)).toBeInTheDocument()
  expect(screen.queryByText(/每一筆操作都會留在「操作紀錄」/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/每一筆操作都會留在「操作紀錄」/)).toHaveTextContent('刪除、調整權限、重設兩步驟驗證需要重新驗證身分')
  const list = within(screen.getByRole('region', { name: '帳號清單' }))
  expect(list.getByText('停用、降級、重設密碼在對方下一個動作生效')).toBeInTheDocument()
  expect(list.getByText('3 個帳號・2 個啟用・3 個有效 session')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /匯出 CSV/ })).toBeInTheDocument()
})

test('帳號表格列出角色、狀態與有效 session；自己那列的停用、降級、刪除停用並說明原因', async () => {
  mount({ users: [user('me', 'root', { role: 'admin', is_super: true, totp_enabled: true }), user('u2', 'alice')] })
  const me = await row('root')
  expect(me.getByText('你')).toBeInTheDocument()
  expect(me.getByText('super')).toBeInTheDocument()
  expect(me.getByText('2FA')).toHaveAttribute('title', '已開啟兩步驟驗證')
  expect(me.getByRole('button', { name: '重設密碼' })).toBeEnabled()
  const mine = await menu('root')
  expect(mine.getByRole('menuitem', { name: '停用' })).toBeDisabled()
  expect(mine.getByRole('menuitem', { name: '停用' })).toHaveAttribute('title', '不能停用自己')
  expect(mine.getByRole('menuitem', { name: '改為一般使用者' })).toBeDisabled()
  expect(mine.getByRole('menuitem', { name: '改為一般使用者' })).toHaveAttribute('title', '不能拿掉自己的管理員權限')
  expect(mine.getByRole('menuitem', { name: '刪除帳號' })).toBeDisabled()
  expect(mine.getByRole('menuitem', { name: '刪除帳號' })).toHaveAttribute('title', '不能刪除自己的帳號')
  fireEvent.keyDown(document, { key: 'Escape' })
  const alice = await row('alice')
  expect(alice.getByText('一般使用者')).toBeInTheDocument()
  expect(alice.getByText('啟用')).toBeInTheDocument()
  expect(alice.getByTitle('有效 session 數')).toHaveTextContent('1')
  const hers = await menu('alice')
  expect(hers.getByRole('menuitem', { name: '停用' })).toBeEnabled()
  // 危險項排最後：停用、刪除帳號。
  expect(hers.getAllByRole('menuitem').map(el => el.textContent)).toEqual([
    '設為管理員', '強制登出（1 個 session）', '停用', '刪除帳號',
  ])
})

test('長帳號單行截斷、完整名稱放 title；操作欄釘在右邊，表格橫向捲動時還看得到', async () => {
  const long = 'institutional-sales.asia-pacific.derivative-desk@tingfong.com.tw'
  mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', long)] })
  const name = await screen.findByText(long)
  expect(name).toHaveAttribute('title', long)
  expect(name).toHaveClass(c.truncate)
  expect(screen.getByRole('columnheader', { name: '操作' })).toHaveClass(c.stickyRight)
  for (const tr of screen.getAllByRole('row').slice(1)) {
    expect(within(tr).getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)
  }
})

test('清單載入失敗：顯示後端的 detail', async () => {
  mount({ override: (p, init) => (p === '/api/admin/users' && !init?.method
    ? { status: 503, body: { detail: '資料庫暫時無法連線' } } : undefined) })
  expect(await screen.findByRole('alert')).toHaveTextContent('帳號清單載入失敗：資料庫暫時無法連線')
})

test('篩選：分段帶數量、輸入框依帳號過濾；沒有符合的顯示空狀態', async () => {
  mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice'), user('u3', 'bob', { enabled: false })] })
  await row('alice')
  const seg = within(screen.getByRole('group', { name: '帳號篩選' }))
  expect(seg.getByRole('button', { name: '全部3' })).toHaveAttribute('aria-pressed', 'true')
  expect(seg.getByRole('button', { name: '管理員1' })).toBeInTheDocument()
  expect(seg.getByRole('button', { name: '一般使用者2' })).toBeInTheDocument()
  fireEvent.click(seg.getByRole('button', { name: '停用1' }))
  const list = within(screen.getByRole('region', { name: '帳號清單' }))
  await waitFor(() => expect(list.queryByText('alice')).not.toBeInTheDocument())
  expect(list.getByText('bob')).toBeInTheDocument()
  fireEvent.click(seg.getByRole('button', { name: '全部3' }))
  fireEvent.change(screen.getByRole('textbox', { name: '篩選帳號' }), { target: { value: 'ALI' } })
  expect(list.getByText('alice')).toBeInTheDocument()
  expect(list.queryByText('bob')).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: '篩選帳號' }), { target: { value: 'zzz' } })
  expect(list.getByText('沒有符合的帳號')).toBeInTheDocument()
})

test('篩選記在 URL：?filter=off 直接只列停用的帳號；不認得的值當成全部', async () => {
  mount({ url: '/admin/users?filter=off',
    users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice'), user('u3', 'bob', { enabled: false })] })
  await row('bob')
  const list = within(screen.getByRole('region', { name: '帳號清單' }))
  expect(list.queryByText('alice')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停用1' })).toHaveAttribute('aria-pressed', 'true')
})

test('建立帳號：頁首按鈕開表單，送出 username／password／role，成功後清單更新、表單關閉', async () => {
  const fetchMock = mount()
  await row('alice')
  fireEvent.click(screen.getByRole('button', { name: '建立帳號' }))
  const dialog = within(await screen.findByRole('dialog', { name: '建立帳號' }))
  fireEvent.change(dialog.getByLabelText('帳號'), { target: { value: '  bob  ' } })
  fireEvent.change(dialog.getByLabelText('初始密碼'), { target: { value: 'bob-password-1' } })
  fireEvent.click(within(dialog.getByRole('group', { name: '角色' })).getByRole('button', { name: '管理員' }))
  fireEvent.click(dialog.getByRole('button', { name: '建立' }))
  await expectToast('已建立帳號「bob」（管理員）')
  await row('bob')
  const post = writes(fetchMock)[0]
  expect(post[0]).toBe('/api/admin/users')
  expect(JSON.parse(post[1]!.body as string)).toEqual({ username: 'bob', password: 'bob-password-1', role: 'admin' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  // 再開一次是空白表單。
  fireEvent.click(screen.getByRole('button', { name: '建立帳號' }))
  expect(within(await screen.findByRole('dialog', { name: '建立帳號' })).getByLabelText('帳號')).toHaveValue('')
})

test('建立帳號：格式不符（帳號字元、密碼長度）先提示、不送出', async () => {
  const fetchMock = mount()
  await row('alice')
  fireEvent.click(screen.getByRole('button', { name: '建立帳號' }))
  const dialog = within(await screen.findByRole('dialog', { name: '建立帳號' }))
  fireEvent.change(dialog.getByLabelText('帳號'), { target: { value: 'bad name' } })
  fireEvent.change(dialog.getByLabelText('初始密碼'), { target: { value: 'bob-password-1' } })
  fireEvent.click(dialog.getByRole('button', { name: '建立' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('帳號需為 2–64 個字元，只能包含文字、數字與 . _ @ -')
  fireEvent.change(dialog.getByLabelText('帳號'), { target: { value: '王小明.ops@tf-1' } })
  fireEvent.change(dialog.getByLabelText('初始密碼'), { target: { value: 'short' } })
  fireEvent.click(dialog.getByRole('button', { name: '建立' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('密碼至少 10 個字元')
  expect(writes(fetchMock)).toHaveLength(0)
})

test('建立帳號：後端 409 的 detail 原樣顯示在表單裡', async () => {
  mount({ override: (p, init) => (p === '/api/admin/users' && init?.method === 'POST'
    ? { status: 409, body: { detail: '帳號「alice」已存在' } } : undefined) })
  await row('alice')
  fireEvent.click(screen.getByRole('button', { name: '建立帳號' }))
  const dialog = within(await screen.findByRole('dialog', { name: '建立帳號' }))
  fireEvent.change(dialog.getByLabelText('帳號'), { target: { value: 'ALICE' } })
  fireEvent.change(dialog.getByLabelText('初始密碼'), { target: { value: 'alice-password' } })
  fireEvent.click(dialog.getByRole('button', { name: '建立' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('帳號「alice」已存在')
})

test('停用要先確認；確認後 PATCH enabled=false，清單反映停用，主要按鈕變成「啟用」', async () => {
  const fetchMock = mount()
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '停用' }))
  const dialog = await screen.findByRole('alertdialog')
  expect(dialog).toHaveTextContent('停用「alice」？')
  expect(writes(fetchMock)).toHaveLength(0)
  expect(within(dialog).getByRole('button', { name: '停用' })).toHaveClass(c.btnDanger)
  fireEvent.click(within(dialog).getByRole('button', { name: '停用' }))
  await expectToast('已停用「alice」')
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/users/u2')
  expect(init!.method).toBe('PATCH')
  expect(JSON.parse(init!.body as string)).toEqual({ enabled: false })
  const alice = await row('alice')
  await waitFor(() => expect(alice.getByText('停用')).toBeInTheDocument())
  expect(alice.getByTitle('有效 session 數')).toHaveTextContent('0')
  expect(alice.queryByRole('button', { name: '重設密碼' })).not.toBeInTheDocument()
  // 停用的帳號：主要按鈕是「啟用」，不另外確認；「重設密碼」收進更多。
  expect((await menu('alice')).getByRole('menuitem', { name: '重設密碼' })).toBeEnabled()
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click(alice.getByRole('button', { name: '啟用' }))
  await expectToast('已啟用「alice」')
  expect(JSON.parse(writes(fetchMock)[1][1]!.body as string)).toEqual({ enabled: true })
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})

test('取消確認就什麼都不送', async () => {
  const fetchMock = mount()
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: /強制登出/ }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  expect(writes(fetchMock)).toHaveLength(0)
})

test('降級被後端以 409 拒絕（最後一位管理員）：detail 顯示給人看', async () => {
  mount({
    users: [user('me', 'root', { role: 'admin' }), user('a2', 'carol', { role: 'admin' })],
    override: (p, init) => (p === '/api/admin/users/a2' && init?.method === 'PATCH'
      ? { status: 409, body: { detail: '至少要保留一位啟用中的管理員' } } : undefined),
  })
  fireEvent.click((await menu('carol')).getByRole('menuitem', { name: '改為一般使用者' }))
  const dialog = await screen.findByRole('alertdialog')
  expect(dialog).toHaveTextContent('拿掉「carol」的管理員權限？')
  fireEvent.click(within(dialog).getByRole('button', { name: '改為一般使用者' }))
  await expectToast('至少要保留一位啟用中的管理員', 'bad')
})

test('設為管理員不需確認，直接 PATCH role=admin', async () => {
  const fetchMock = mount()
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '設為管理員' }))
  await waitFor(() => expect(writes(fetchMock)).toHaveLength(1))
  expect(JSON.parse(writes(fetchMock)[0][1]!.body as string)).toEqual({ role: 'admin' })
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  await expectToast('「alice」已設為管理員')
})

test('重設密碼：太短或兩次不一致不送；一致才送，成功說明已登出所有裝置', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('alice')).getByRole('button', { name: '重設密碼' }))
  const dialog = await screen.findByRole('dialog', { name: '重設「alice」的密碼' })
  fireEvent.change(within(dialog).getByLabelText('新密碼'), { target: { value: 'short' } })
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'short' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('密碼至少 10 個字元')
  fireEvent.change(within(dialog).getByLabelText('新密碼'), { target: { value: 'new-password-1' } })
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'new-password-2' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('兩次輸入的密碼不一致')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'new-password-1' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  await expectToast('已重設「alice」的密碼，該帳號所有裝置都已登出')
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/users/u2/password')
  expect(JSON.parse(init!.body as string)).toEqual({ password: 'new-password-1' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('重設密碼：後端 400（政策不符）顯示在對話框裡', async () => {
  mount({ override: p => (p === '/api/admin/users/u2/password'
    ? { status: 400, body: { detail: '密碼不可與帳號相同' } } : undefined) })
  fireEvent.click((await row('alice')).getByRole('button', { name: '重設密碼' }))
  const dialog = await screen.findByRole('dialog', { name: '重設「alice」的密碼' })
  fireEvent.change(within(dialog).getByLabelText('新密碼'), { target: { value: 'alice-alice-alice' } })
  fireEvent.change(within(dialog).getByLabelText('再輸入一次'), { target: { value: 'alice-alice-alice' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '重設密碼' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('密碼不可與帳號相同')
})

test('強制登出：選單項帶 session 數；確認後 POST logout，回報撤銷數；沒有 session 的帳號該項停用', async () => {
  const fetchMock = mount({ users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { active_sessions: 2 }),
    user('u4', 'dave', { active_sessions: 0 })] })
  const idle = (await menu('dave')).getByRole('menuitem', { name: '強制登出（0 個 session）' })
  expect(idle).toBeDisabled()
  expect(idle).toHaveAttribute('title', '目前沒有已登入的裝置')
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '強制登出（2 個 session）' }))
  const dialog = await screen.findByRole('alertdialog')
  expect(dialog).toHaveTextContent('強制登出「alice」？')
  fireEvent.click(within(dialog).getByRole('button', { name: '強制登出' }))
  await expectToast('已強制登出「alice」（2 個 session）')
  expect(writes(fetchMock)[0][0]).toBe('/api/admin/users/u2/logout')
})

const deletionItem = (id: string, executeAfter: string, status = 'pending') => ({
  id: 1, user_id: id, username: 'alice', requested_by: 'me', requested_by_username: 'root',
  requested_at: '2026-10-06T00:00:00Z', execute_after: executeAfter, cancelled_at: null, executed_at: null, status,
})

test('刪除帳號：確認後先重新驗證（密碼錯會提示），驗證成功才送出', async () => {
  const fetchMock = mount({
    override: (p, init) => {
      if (p === '/api/admin/users/u2/deletion') return { body: deletionItem('u2', '2026-10-07T00:00:00Z') }
      if (p === '/api/me/elevate') {
        const body = JSON.parse(init!.body as string)
        if (body.password !== 'root-password-1') return { status: 403, body: { detail: '密碼不正確', code: 'bad_password' } }
        return { body: { elevated_until: STILL_ELEVATED() } }
      }
      return undefined
    },
  })
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '刪除帳號' }))
  const confirm = await screen.findByRole('alertdialog')
  expect(confirm).toHaveTextContent('刪除「alice」？')
  expect(confirm).toHaveTextContent('24 小時內可以取消')
  fireEvent.click(within(confirm).getByRole('button', { name: '排程刪除' }))
  const elevation = await reverify('wrong-password')
  expect(screen.getByRole('dialog', { name: '重新驗證身分' })).toHaveTextContent('「刪除帳號」需要重新驗證')
  expect(await elevation.findByRole('alert')).toHaveTextContent('密碼不正確')
  expect(callsTo(fetchMock, '/api/admin/users/u2/deletion')).toHaveLength(0)
  await reverify()
  await expectToast('已排程刪除「alice」：帳號已停用，24 小時後執行')
  expect(callsTo(fetchMock, '/api/admin/users/u2/deletion')).toHaveLength(1)
})

test('刪除帳號：10 分鐘內驗證過就不再詢問；後端仍回 elevation_required 時補驗證後自動重試', async () => {
  let elevated = false
  const fetchMock = mount({
    me: { elevated_until: STILL_ELEVATED() },
    users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice'), user('u3', 'bob')],
    override: p => {
      if (p === '/api/admin/users/u2/deletion') return { body: deletionItem('u2', '2026-10-07T00:00:00Z') }
      if (p === '/api/admin/users/u3/deletion') return elevated ? { body: deletionItem('u3', '2026-10-07T00:00:00Z') } : ELEVATION_REQUIRED
      if (p === '/api/me/elevate') { elevated = true; return { body: { elevated_until: STILL_ELEVATED() } } }
      return undefined
    },
  })
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '刪除帳號' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '排程刪除' }))
  await expectToast(/已排程刪除「alice」/)
  expect(callsTo(fetchMock, '/api/me/elevate')).toHaveLength(0)
  fireEvent.click((await menu('bob')).getByRole('menuitem', { name: '刪除帳號' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '排程刪除' }))
  await reverify()
  await expectToast(/已排程刪除「bob」/)
  expect(callsTo(fetchMock, '/api/admin/users/u3/deletion')).toHaveLength(2)
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
  expect(alice.queryByRole('button', { name: 'alice 的更多操作' })).not.toBeInTheDocument()
  expect(alice.getAllByRole('button')).toHaveLength(1)
  fireEvent.click(alice.getByRole('button', { name: '取消刪除' }))
  await expectToast('已取消刪除「alice」')
  expect(writes(fetchMock)[0][0]).toBe('/api/admin/users/u2/deletion/cancel')
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
})

test('super admin 才看得到「調整權限」；先重新驗證（取消就留在表單、不算錯誤），送出完整的 scope 清單', async () => {
  const fetchMock = mount({
    me: { is_super: true, scopes: [] },
    users: [user('me', 'root', { role: 'admin', is_super: true }), user('u2', 'alice', { role: 'admin', scopes: ['ops.operate'] }),
      user('u3', 'bob')],
    override: (p, init) => {
      if (p === '/api/admin/users/u2/privileges') {
        const body = JSON.parse(init!.body as string)
        return { body: user('u2', 'alice', { role: 'admin', ...body }) }
      }
      return undefined
    },
  })
  // 一般使用者那列沒有這一項。
  expect((await menu('bob')).queryByRole('menuitem', { name: '調整權限' })).not.toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '調整權限' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的權限' })
  expect(within(dialog).getByLabelText(/ops.operate/)).toBeChecked()
  // 勾 super admin：其他權限全部勾選並停用；取消勾選後回到原本的選擇。
  fireEvent.click(within(dialog).getByLabelText(/^super admin/))
  for (const scope of [/qa_content.read/, /ops.operate/, /api_clients.manage/]) {
    expect(within(dialog).getByLabelText(scope)).toBeChecked()
    expect(within(dialog).getByLabelText(scope)).toBeDisabled()
  }
  fireEvent.click(within(dialog).getByLabelText(/^super admin/))
  expect(within(dialog).getByLabelText(/qa_content.read/)).not.toBeChecked()
  fireEvent.click(within(dialog).getByLabelText(/qa_content.read/))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(elevation).toHaveTextContent('「調整權限」需要重新驗證')
  fireEvent.click(within(elevation).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '重新驗證身分' })).not.toBeInTheDocument())
  expect(screen.getByRole('dialog', { name: '調整「alice」的權限' })).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(callsTo(fetchMock, '/api/admin/users/u2/privileges')).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  await reverify()
  await expectToast('已更新「alice」的權限')
  const [, init] = callsTo(fetchMock, '/api/admin/users/u2/privileges')[0]
  expect(init!.method).toBe('PUT')
  expect(JSON.parse(init!.body as string)).toEqual({ is_super: false, scopes: ['ops.operate', 'qa_content.read'] })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('調整權限：後端拒絕時 detail 顯示在表單裡', async () => {
  mount({
    me: { is_super: true, elevated_until: STILL_ELEVATED() },
    users: [user('me', 'root', { role: 'admin', is_super: true }), user('u2', 'alice', { role: 'admin', is_super: true })],
    override: p => (p === '/api/admin/users/u2/privileges'
      ? { status: 409, body: { detail: '至少要保留一位啟用中的 super admin' } } : undefined),
  })
  fireEvent.click((await menu('alice')).getByRole('menuitem', { name: '調整權限' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的權限' })
  fireEvent.click(within(dialog).getByLabelText(/^super admin/))
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('至少要保留一位啟用中的 super admin')
})

test('非 super 的管理員沒有「調整權限」；開了 2FA 的帳號可以「重設兩步驟驗證」（確認＋重新驗證）', async () => {
  const fetchMock = mount({
    users: [user('me', 'root', { role: 'admin' }), user('u2', 'alice', { role: 'admin', totp_enabled: true }), user('u3', 'bob')],
    override: p => (p === '/api/admin/users/u2/totp/reset'
      ? { body: user('u2', 'alice', { role: 'admin', totp_enabled: false }) } : undefined),
  })
  expect((await menu('bob')).queryByRole('menuitem', { name: '重設兩步驟驗證' })).not.toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  const hers = await menu('alice')
  expect(hers.queryByRole('menuitem', { name: '調整權限' })).not.toBeInTheDocument()
  fireEvent.click(hers.getByRole('menuitem', { name: '重設兩步驟驗證' }))
  const confirm = await screen.findByRole('alertdialog')
  expect(confirm).toHaveTextContent('重設「alice」的兩步驟驗證？')
  fireEvent.click(within(confirm).getByRole('button', { name: '重設兩步驟驗證' }))
  expect(await screen.findByRole('dialog', { name: '重新驗證身分' })).toHaveTextContent('「重設兩步驟驗證」需要重新驗證')
  await reverify()
  await expectToast('已重設「alice」的兩步驟驗證')
  expect(callsTo(fetchMock, '/api/admin/users/u2/totp/reset')).toHaveLength(1)
})

const bulkPosts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([p, init]) => p === '/api/admin/users/bulk' && init?.method === 'POST')

const team = () => [
  user('me', 'root', { role: 'admin' }), user('u2', 'alice'), user('u3', 'bob', { active_sessions: 0 }),
  user('u4', 'carol', { enabled: false, active_sessions: 0 }),
]

test('批次勾選：自己那列不可勾；全選只選別人，批次列依狀態分別計數，數量為 0 就停用', async () => {
  mount({ users: team() })
  expect((await row('root')).getByRole('checkbox')).toBeDisabled()
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: '全選' }))
  const bar = within(await screen.findByRole('toolbar', { name: '批次操作' }))
  expect(bar.getByText('已選 3 個')).toBeInTheDocument()
  expect(bar.getByRole('button', { name: '停用（2）' })).toBeEnabled()
  expect(bar.getByRole('button', { name: '啟用（1）' })).toBeEnabled()
  expect(bar.getByRole('button', { name: '強制登出（1）' })).toBeEnabled()
  expect((await row('root')).getByRole('checkbox')).not.toBeChecked()
  // 有勾選時整條工具列釘在頁面頂端：捲到下面的列也按得到批次按鈕
  expect(screen.getByRole('toolbar', { name: '批次操作' }).parentElement).toHaveClass(styles.toolbarSticky)
  fireEvent.click((await row('carol')).getByRole('checkbox'))
  expect(bar.getByRole('button', { name: '啟用（0）' })).toBeDisabled()
  expect(bar.getByText('已選 2 個')).toBeInTheDocument()
  fireEvent.click(bar.getByRole('button', { name: '清除選取' }))
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
  expect(screen.getByPlaceholderText('篩選帳號').closest(`.${c.toolbar}`)).not.toHaveClass(styles.toolbarSticky)
})

test('批次啟用不算排程刪除中的帳號；選取跨篩選保留', async () => {
  const soon = new Date(Date.now() + 60 * 60_000).toISOString()
  mount({ users: [...team(), user('u5', 'erin', { enabled: false, active_sessions: 0, deletion_execute_after: soon })] })
  fireEvent.click((await row('carol')).getByRole('checkbox'))
  fireEvent.click((await row('erin')).getByRole('checkbox'))
  const bar = within(await screen.findByRole('toolbar', { name: '批次操作' }))
  expect(bar.getByRole('button', { name: '啟用（1）' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: '管理員1' }))
  await waitFor(() => expect(within(screen.getByRole('region', { name: '帳號清單' })).queryByText('carol')).not.toBeInTheDocument())
  expect(bar.getByText('已選 2 個')).toBeInTheDocument()
})

test('批次停用：確認框列出帳號；重新驗證後送出，顯示結果與略過原因，選取清空', async () => {
  const fetchMock = mount({
    users: team(),
    override: (p, init) => (p === '/api/admin/users/bulk' && init?.method === 'POST'
      ? { body: { action: 'disable', requested: 2, ok: 1, unchanged: 0, skipped: 1, results: [
        { user_id: 'u2', status: 'ok' },
        { user_id: 'u3', status: 'skipped', code: 'last_super', detail: '至少要保留一位啟用中的 super admin' },
      ] } } : undefined),
  })
  fireEvent.click((await row('alice')).getByRole('checkbox'))
  fireEvent.click((await row('bob')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '停用（2）' }))
  const confirm = within(await screen.findByRole('alertdialog', { name: '停用 2 個帳號？' }))
  expect(confirm.getByText(/「alice」、「bob」/)).toHaveTextContent('每個帳號各留一筆操作紀錄')
  expect(confirm.getByRole('button', { name: '停用 2 個' })).toHaveClass(c.btnDanger)
  fireEvent.click(confirm.getByRole('button', { name: '停用 2 個' }))
  expect(await screen.findByRole('dialog', { name: '重新驗證身分' })).toHaveTextContent('「批次停用」需要重新驗證')
  expect(bulkPosts(fetchMock)).toHaveLength(0)
  await reverify()
  const result = await screen.findByRole('status', { name: '批次結果' })
  expect(result).toHaveTextContent('批次停用：成功 1 筆、略過 1 筆')
  expect(result).toHaveTextContent('bob：至少要保留一位啟用中的 super admin')
  const sent = bulkPosts(fetchMock)
  expect(sent).toHaveLength(1)
  expect(JSON.parse(sent[0][1]!.body as string)).toEqual({ action: 'disable', user_ids: ['u2', 'u3'] })
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
  fireEvent.click(within(result).getByRole('button', { name: '關閉' }))
  expect(screen.queryByRole('status', { name: '批次結果' })).not.toBeInTheDocument()
})

test('批次啟用：可逆的動作用金色按鈕；全部成功只跳 toast，不留結果面板', async () => {
  const fetchMock = mount({
    me: { elevated_until: STILL_ELEVATED() },
    users: team(),
    override: (p, init) => (p === '/api/admin/users/bulk' && init?.method === 'POST'
      ? { body: { action: 'enable', requested: 1, ok: 1, unchanged: 0, skipped: 0, results: [{ user_id: 'u4', status: 'ok' }] } }
      : undefined),
  })
  fireEvent.click((await row('carol')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '啟用（1）' }))
  const confirm = within(await screen.findByRole('alertdialog', { name: '啟用 1 個帳號？' }))
  expect(confirm.getByRole('button', { name: '啟用 1 個' })).toHaveClass(c.btnPrimary)
  fireEvent.click(confirm.getByRole('button', { name: '啟用 1 個' }))
  await expectToast('批次啟用：成功 1 筆、略過 0 筆')
  expect(screen.queryByRole('status', { name: '批次結果' })).not.toBeInTheDocument()
  expect(JSON.parse(bulkPosts(fetchMock)[0][1]!.body as string)).toEqual({ action: 'enable', user_ids: ['u4'] })
})

test('批次操作：後端回 elevation_required 時補驗證，取消就不再送、不顯示錯誤，選取保留', async () => {
  const fetchMock = mount({
    me: { elevated_until: STILL_ELEVATED() },
    users: team(),
    override: (p, init) => (p === '/api/admin/users/bulk' && init?.method === 'POST' ? ELEVATION_REQUIRED : undefined),
  })
  fireEvent.click((await row('alice')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '強制登出（1）' }))
  fireEvent.click(within(await screen.findByRole('alertdialog', { name: '強制登出 1 個帳號？' }))
    .getByRole('button', { name: '強制登出 1 個' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.click(within(elevation).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(bulkPosts(fetchMock)).toHaveLength(1)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByRole('toolbar', { name: '批次操作' })).toHaveTextContent('已選 1 個')
})

test('批次操作失敗：錯誤用 toast 顯示', async () => {
  mount({
    me: { elevated_until: STILL_ELEVATED() },
    users: team(),
    override: (p, init) => (p === '/api/admin/users/bulk' && init?.method === 'POST'
      ? { status: 503, body: { detail: '稽核寫入失敗' } } : undefined),
  })
  fireEvent.click((await row('alice')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '停用（1）' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '停用 1 個' }))
  await expectToast('批次停用失敗：稽核寫入失敗', 'bad')
})
