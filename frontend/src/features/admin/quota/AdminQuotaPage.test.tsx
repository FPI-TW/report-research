import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import AdminQuotaPage from './AdminQuotaPage'

afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

type Reply = { status?: number; body: unknown }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const item = (kind: string, over: Record<string, unknown> = {}) => ({
  kind, used: 3, over: 0, limit: kind === 'ask' ? 100 : kind === 'export' ? 20 : 30,
  default_limit: kind === 'ask' ? 100 : kind === 'export' ? 20 : 30, mode: 'default', remaining: 10, reason: null,
  updated_at: null, ...over,
})

const row = (id: string, username: string, over: Record<string, unknown> = {}, items = ['ask', 'export', 'upload'].map(k => item(k))) => ({
  user_id: id, username, role: 'user', enabled: true, is_super: false, items,
  llm: { calls: 4, failures: 0, prompt_tokens: 1000, completion_tokens: 200 }, ...over,
})

const OVERVIEW = {
  day: '2026-10-07', timezone: 'Asia/Taipei', resets_in_seconds: 3600,
  defaults: { ask: 100, export: 20, upload: 30 },
  enforcement: { env_ceiling: false, flag_enabled: false, flag_scoped: false, flag_source: 'default', effective: false, mode: 'shadow' },
  over_today: { ask: 12, export: 1 },
  users: [
    row('u1', 'alice', {}, [item('ask', { used: 100, over: 12, remaining: 0 }), item('export'), item('upload')]),
    row('u2', 'bob', {}, [item('ask', { mode: 'unlimited', limit: null, remaining: null }), item('export'), item('upload')]),
    row('u3', 'boss', { role: 'admin', is_super: true }),
  ],
}

const STATS = {
  since_day: '2026-09-24', until_day: '2026-10-07', days: 14, timezone: 'Asia/Taipei',
  kinds: [
    { kind: 'ask', default_limit: 100, user_days: 20, users: 5, p50: 12, p95: 96, max: 140, over_user_days: 2, over_events: 52 },
    { kind: 'export', default_limit: 20, user_days: 3, users: 2, p50: 1, p95: 4, max: 4, over_user_days: 0, over_events: 0 },
    { kind: 'upload', default_limit: 30, user_days: 0, users: 0, p50: null, p95: null, max: null, over_user_days: 0, over_events: 0 },
  ],
}

/** `elevated`＝/api/me 回報還在重新驗證的 10 分鐘視窗內（預設是），敏感操作不再詢問。 */
function mount(opts: { scopes?: string[]; isSuper?: boolean; elevated?: boolean; override?: Override } = {}) {
  const scopes = opts.scopes ?? ['admin', 'accounts.manage']
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      if (path === '/api/me') {
        return { body: {
          id: 'me', username: 'root', role: 'admin', scopes, is_super: opts.isSuper ?? false,
          elevated_until: opts.elevated === false ? null : new Date(Date.now() + 600_000).toISOString(),
        } }
      }
      if (path === '/api/admin/quota') return { body: OVERVIEW }
      if (path.startsWith('/api/admin/quota/stats')) {
        const days = Number(new URL(path, 'http://x').searchParams.get('days') ?? 14)
        return { body: { ...STATS, days } }
      }
      return { status: 404, body: { detail: 'not found', code: 'not_found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/quota']}><AdminQuotaPage /></MemoryRouter>
      <Toaster />
    </QueryClientProvider>,
  )
  return fetchMock
}

async function userRow(name: string) {
  const table = within(await screen.findByRole('table', { name: '每人用量' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr') as HTMLElement)
}

/** 開某個帳號的「調整上限」表單。 */
async function openLimits(name: string) {
  fireEvent.click((await userRow(name)).getByRole('button', { name: '調整上限' }))
  return within(await screen.findByRole('dialog', { name: `調整「${name}」的每日上限` }))
}

const tile = (label: RegExp) => within(screen.getByText(label).parentElement!)
const puts = (fetchMock: ReturnType<typeof mount>) => fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
const putBodies = (fetchMock: ReturnType<typeof mount>) =>
  puts(fetchMock).map(([p, init]) => [String(p).split('/').pop(), JSON.parse(init!.body as string)])

test('頁首：kicker、標題、一句摘要；長說明收在「說明」後面', async () => {
  mount()
  expect(await screen.findByRole('heading', { level: 1, name: '配額' })).toBeInTheDocument()
  expect(screen.getByText('存取')).toBeInTheDocument()
  expect(await screen.findByText(/目前是影子模式，只計數、記錄本來會擋的請求/)).toBeInTheDocument()
  expect(screen.queryByText(/兩道都開才會正式阻擋/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/兩道都開才會正式阻擋/)).toBeInTheDocument()
  expect(screen.getByText(/上傳一直是正式阻擋/)).toBeInTheDocument()
})

test('影子模式狀態、今天超出上限次數、每人用量與覆寫標記', async () => {
  mount()
  expect(await screen.findByText('影子模式')).toBeInTheDocument()
  expect(tile(/^今天超出上限（本來會擋）$/).getByText('13')).toBeInTheDocument()
  expect(tile(/^今天超出上限（本來會擋）$/).getByText('問答 12・匯出 1')).toBeInTheDocument()
  expect(tile(/^環境變數 QUOTA_ENFORCE$/).getByText('關')).toBeInTheDocument()
  expect(tile(/^旗標 quota\.enforce$/).getByText('程式預設')).toBeInTheDocument()
  expect(tile(/^預設每日上限$/).getByText('匯出 20・上傳 30')).toBeInTheDocument()
  const alice = await userRow('alice')
  expect(alice.getByText('100／100')).toHaveAttribute('data-tone', 'bad')
  expect(alice.getByText('超出 12（影子模式照常放行）')).toBeInTheDocument()
  expect(alice.getByText('4 次')).toBeInTheDocument()
  expect(alice.getByText('1,200 token')).toBeInTheDocument()
  expect(alice.getByText('一般使用者')).toBeInTheDocument()
  const bob = await userRow('bob')
  expect(bob.getByText('3／不限')).toBeInTheDocument()
  expect(bob.getByText('不限')).toBeInTheDocument()
  const boss = await userRow('boss')
  expect(boss.getByText('super')).toBeInTheDocument()
  expect(boss.getByText('管理員')).toBeInTheDocument()
  expect(screen.getByText('2026-10-07（台北時間）')).toBeInTheDocument()
})

test('旗標讀取失敗、只對部分對象生效、已停用的帳號與用滿上限都標得出來', async () => {
  mount({
    override: p => (p === '/api/admin/quota' ? { body: {
      ...OVERVIEW,
      enforcement: { ...OVERVIEW.enforcement, flag_source: 'fallback', flag_scoped: true },
      users: [row('u4', 'carol', { enabled: false }, [item('ask', { used: 100, remaining: 0 }), item('export', { mode: 'limit', limit: 5 }), item('upload')])],
    } } : undefined),
  })
  expect(await screen.findByText('旗標讀取失敗，暫用預設（關）・只對部分角色或使用者生效')).toBeInTheDocument()
  const carol = await userRow('carol')
  expect(carol.getByText('已停用')).toBeInTheDocument()
  expect(carol.getByText('100／100')).toHaveAttribute('data-tone', 'warn')
  expect(carol.getByText('3／5')).toBeInTheDocument()
  expect(carol.getByText('覆寫')).toBeInTheDocument()
  expect(carol.queryByText(/^超出/)).not.toBeInTheDocument()
})

test('沒有任何帳號時顯示空狀態', async () => {
  mount({ override: p => (p === '/api/admin/quota' ? { body: { ...OVERVIEW, users: [] } } : undefined) })
  expect(await screen.findByText('還沒有任何帳號')).toBeInTheDocument()
})

test('非 super admin 不能調整 super admin 的配額，也看不到「不限」選項', async () => {
  mount()
  const locked = (await userRow('boss')).getByRole('button', { name: '調整上限' })
  expect(locked).toBeDisabled()
  expect(locked).toHaveAttribute('title', '只有 super admin 能調整 super admin 的配額')
  const dialog = await openLimits('alice')
  for (const kind of ['問答', '匯出', '上傳']) {
    const mode = dialog.getByLabelText(`${kind}設定`) as HTMLSelectElement
    expect([...mode.options].map(o => o.value)).toEqual(['limit', 'default'])
  }
  expect(dialog.getByText(/程式預設 100・今天已用 100/)).toBeInTheDocument()
})

test('super admin 可以選「不限」', async () => {
  const fetchMock = mount({
    isSuper: true,
    override: (_p, init) => (init?.method === 'PUT' ? { body: { changed: true, item: item('ask', { mode: 'unlimited', limit: null }) } } : undefined),
  })
  const dialog = await openLimits('alice')
  const mode = dialog.getByLabelText('問答設定') as HTMLSelectElement
  expect([...mode.options].map(o => o.value)).toEqual(['limit', 'default', 'unlimited'])
  fireEvent.change(mode, { target: { value: 'unlimited' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已調整「alice」的上限'))
  expect(putBodies(fetchMock)).toEqual([['ask', { mode: 'unlimited', daily_limit: null, reason: null }]])
})

test('一般管理員開已經是「不限」的帳號：設定顯示現況，可以改回程式預設', async () => {
  const fetchMock = mount({
    override: (_p, init) => (init?.method === 'PUT' ? { body: { changed: true, item: item('ask') } } : undefined),
  })
  const dialog = await openLimits('bob')
  const mode = dialog.getByLabelText('問答設定') as HTMLSelectElement
  expect(mode.value).toBe('unlimited')
  expect(dialog.getByRole('button', { name: '儲存' })).toBeDisabled()
  fireEvent.change(mode, { target: { value: 'default' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(puts(fetchMock)).toHaveLength(1))
  expect(putBodies(fetchMock)).toEqual([['ask', { mode: 'default', daily_limit: null, reason: null }]])
})

test('設定上限：收到 elevation_required 時重新驗證後自動重試，送出的 body 正確', async () => {
  let elevated = false
  const fetchMock = mount({
    isSuper: true,
    override: (p, init) => {
      if (p === '/api/admin/quota/users/u1/export' && init?.method === 'PUT') {
        return elevated
          ? { body: { changed: true, item: item('export', { mode: 'limit', limit: 5 }) } }
          : { status: 403, body: { detail: '這項操作需要重新驗證密碼', code: 'elevation_required' } }
      }
      if (p === '/api/me/elevate') {
        expect(JSON.parse(init!.body as string).password).toBe('fixed-test-secret-pw')
        elevated = true
        return { body: { elevated_until: '2026-10-07T00:10:00Z' } }
      }
      return undefined
    },
  })
  const dialog = await openLimits('alice')
  fireEvent.change(dialog.getByLabelText('匯出每日上限'), { target: { value: '5' } })
  expect(dialog.getByLabelText('匯出設定')).toHaveValue('limit')
  fireEvent.change(dialog.getByLabelText(/備註/), { target: { value: '  專案需要  ' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(elevation).toHaveTextContent('「調整配額」需要重新驗證')
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'fixed-test-secret-pw' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已調整「alice」的上限'))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  // 只送有變的類別：問答、上傳沒動，不送。
  expect(putBodies(fetchMock)).toEqual([
    ['export', { mode: 'limit', daily_limit: 5, reason: '專案需要' }],
    ['export', { mode: 'limit', daily_limit: 5, reason: '專案需要' }],
  ])
})

test('還沒重新驗證：先問身分才送；取消就什麼都不送，表單留著、不算錯誤', async () => {
  const fetchMock = mount({ elevated: false })
  const dialog = await openLimits('alice')
  fireEvent.change(dialog.getByLabelText('問答每日上限'), { target: { value: '200' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(puts(fetchMock)).toHaveLength(0)
  fireEvent.click(within(elevation).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '重新驗證身分' })).not.toBeInTheDocument())
  expect(screen.getByRole('dialog', { name: '調整「alice」的每日上限' })).toBeInTheDocument()
  expect(dialog.queryByRole('alert')).not.toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
  expect(fetchMock.mock.calls.some(([p]) => p === '/api/me/elevate')).toBe(false)
})

test('一次調整多個類別：每個類別一筆 PUT，備註寫在這次送出的類別上', async () => {
  const fetchMock = mount({
    override: (_p, init) => (init?.method === 'PUT' ? { body: { changed: true, item: item('ask') } } : undefined),
  })
  const dialog = await openLimits('alice')
  fireEvent.change(dialog.getByLabelText('問答每日上限'), { target: { value: '200' } })
  fireEvent.change(dialog.getByLabelText('上傳每日上限'), { target: { value: '0' } })
  fireEvent.change(dialog.getByLabelText(/備註/), { target: { value: '研究週需求' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已調整「alice」的上限'))
  expect(putBodies(fetchMock)).toEqual([
    ['ask', { mode: 'limit', daily_limit: 200, reason: '研究週需求' }],
    ['upload', { mode: 'limit', daily_limit: 0, reason: '研究週需求' }],
  ])
})

test('沒有改動時不能儲存；只改備註會更新現有覆寫的備註；後端回報沒有變動時照實說', async () => {
  const fetchMock = mount({
    override: (p, init) => {
      if (p === '/api/admin/quota') {
        return { body: { ...OVERVIEW, users: [
          row('u5', 'dave', {}, [item('ask', { mode: 'limit', limit: 250, reason: '舊備註' }), item('export'), item('upload')]),
        ] } }
      }
      return init?.method === 'PUT' ? { body: { changed: false, item: item('ask') } } : undefined
    },
  })
  const dialog = await openLimits('dave')
  expect(dialog.getByLabelText('問答每日上限')).toHaveValue(250)
  expect(dialog.getByText(/覆寫 250・今天已用 3・備註：舊備註/)).toBeInTheDocument()
  expect(dialog.getByRole('button', { name: '儲存' })).toBeDisabled()
  fireEvent.change(dialog.getByLabelText(/備註/), { target: { value: '新備註' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('「dave」的上限沒有變動'))
  expect(putBodies(fetchMock)).toEqual([['ask', { mode: 'limit', daily_limit: 250, reason: '新備註' }]])
})

test('上限不是整數時不送出；後端錯誤原樣顯示', async () => {
  const fetchMock = mount({
    override: (p, init) => (p === '/api/admin/quota/users/u2/ask' && init?.method === 'PUT'
      ? { status: 403, body: { detail: '只有 super admin 能調整 super admin 的配額', code: 'super_required' } }
      : undefined),
  })
  const dialog = await openLimits('bob')
  fireEvent.change(dialog.getByLabelText('問答每日上限'), { target: { value: '1.5' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('問答的每日上限必須是 0–100000 的整數')
  expect(puts(fetchMock)).toHaveLength(0)
  fireEvent.change(dialog.getByLabelText('問答每日上限'), { target: { value: '100001' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('0–100000 的整數')
  expect(puts(fetchMock)).toHaveLength(0)
  fireEvent.change(dialog.getByLabelText('問答設定'), { target: { value: 'default' } })
  fireEvent.click(dialog.getByRole('button', { name: '儲存' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('只有 super admin 能調整')
  expect(putBodies(fetchMock)).toEqual([['ask', { mode: 'default', daily_limit: null, reason: null }]])
  // 失敗時表單留著，可以取消
  fireEvent.click(dialog.getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('用量分布：P50／P95 表，換範圍帶新的 days 重抓；上傳沒有超額欄；最大值超過預設上限時標出', async () => {
  const fetchMock = mount()
  const table = within(await screen.findByRole('table', { name: '用量分布' }))
  const ask = within(table.getByRole('cell', { name: '問答' }).closest('tr') as HTMLElement)
  expect(ask.getByText('12')).toBeInTheDocument()
  expect(ask.getByText('96')).toBeInTheDocument()
  expect(ask.getByText('140')).toHaveAttribute('data-tone', 'warn')
  const upload = within(table.getByRole('cell', { name: '上傳' }).closest('tr') as HTMLElement)
  expect(upload.getAllByText('—').length).toBeGreaterThanOrEqual(3)
  expect(screen.getByText('2026-09-24 ～ 2026-10-07（台北時間）')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p) === '/api/admin/quota/stats?days=14')).toBe(true)
  fireEvent.click(within(screen.getByRole('group', { name: '範圍' })).getByRole('button', { name: '30 天' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p) === '/api/admin/quota/stats?days=30')).toBe(true))
})

test('用量分布載入失敗只影響那一張卡，每人用量照常顯示', async () => {
  mount({ override: p => (p.startsWith('/api/admin/quota/stats') ? { status: 503, body: { detail: '統計暫時無法使用', code: 'x' } } : undefined) })
  expect(await screen.findByText('用量分布載入失敗：統計暫時無法使用')).toBeInTheDocument()
  expect(await userRow('alice')).toBeTruthy()
})

test('正式阻擋時標示，超出的次數寫「已擋下」', async () => {
  mount({
    override: p => (p === '/api/admin/quota'
      ? { body: { ...OVERVIEW, enforcement: { ...OVERVIEW.enforcement, env_ceiling: true, flag_enabled: true, flag_source: 'db', effective: true, mode: 'enforce' } } }
      : undefined),
  })
  expect(await screen.findByText('正式阻擋')).toBeInTheDocument()
  expect(screen.getByText(/今天超出上限（已擋下）/)).toBeInTheDocument()
  expect(screen.getByText(/目前正式阻擋，超額的請求回 429/)).toBeInTheDocument()
  expect(tile(/^旗標 quota\.enforce$/).getByText('來自 DB 覆寫')).toBeInTheDocument()
  expect((await userRow('alice')).getByText('超出 12（已擋下）')).toBeInTheDocument()
})

test('API 失敗時顯示後端 detail，不白屏', async () => {
  mount({ override: p => (p === '/api/admin/quota' ? { status: 503, body: { detail: '服務暫時無法使用', code: 'x' } } : undefined) })
  expect(await screen.findByText('配額載入失敗：服務暫時無法使用')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '配額' })).toBeInTheDocument()
})

test('「前往功能旗標」只在有 ops.read 時出現', async () => {
  mount({ scopes: ['admin', 'accounts.manage', 'ops.read'] })
  expect(await screen.findByRole('link', { name: '前往功能旗標' })).toHaveAttribute('href', '/admin/flags')
})

test('沒有 ops.read 時不出現「前往功能旗標」', async () => {
  mount()
  await userRow('alice')
  expect(screen.queryByRole('link', { name: '前往功能旗標' })).not.toBeInTheDocument()
})

test('沒有 accounts.manage 時不打 API', async () => {
  const fetchMock = mount({ scopes: ['admin'] })
  expect(await screen.findByText(/accounts\.manage/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin/quota'))).toBe(false)
})
