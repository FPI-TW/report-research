import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminQuotaPage from './AdminQuotaPage'

afterEach(() => vi.unstubAllGlobals())

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

function mount(opts: { scopes?: string[]; isSuper?: boolean; override?: Override } = {}) {
  const scopes = opts.scopes ?? ['admin', 'accounts.manage']
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', scopes, is_super: opts.isSuper ?? false } }
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
    </QueryClientProvider>,
  )
  return fetchMock
}

async function userRow(name: string) {
  const table = within(await screen.findByRole('table', { name: '每人用量' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr') as HTMLElement)
}

const puts = (fetchMock: ReturnType<typeof mount>) => fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')

test('影子模式狀態、今天超出上限次數、每人用量與覆寫標記', async () => {
  mount()
  expect(await screen.findByText('影子模式')).toBeInTheDocument()
  expect(within(screen.getByLabelText('今天超出上限')).getByText('13')).toBeInTheDocument()
  expect(screen.getByText(/今天超出上限（本來會擋）/)).toBeInTheDocument()
  const alice = await userRow('alice')
  expect(alice.getByText('100 / 100')).toBeInTheDocument()
  expect(alice.getByText('超出 12')).toBeInTheDocument()
  expect(alice.getByText('4 次')).toBeInTheDocument()
  const bob = await userRow('bob')
  expect(bob.getByText('3 / 不限')).toBeInTheDocument()
  expect(bob.getAllByText('不限').length).toBeGreaterThanOrEqual(1)
})

test('非 super admin 不能調整 super admin 的配額，也看不到「不限」選項', async () => {
  mount()
  expect((await userRow('boss')).getByRole('button', { name: '調整' })).toBeDisabled()
  fireEvent.click((await userRow('alice')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的配額' })
  const mode = within(dialog).getByLabelText('設定') as HTMLSelectElement
  expect([...mode.options].map(o => o.value)).toEqual(['limit', 'default'])
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
      if (p === '/api/admin/elevate') {
        elevated = true
        return { body: { elevated_until: '2026-10-07T00:10:00Z' } }
      }
      return undefined
    },
  })
  fireEvent.click((await userRow('alice')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的配額' })
  fireEvent.change(within(dialog).getByLabelText('類別'), { target: { value: 'export' } })
  fireEvent.change(within(dialog).getByLabelText(/每日上限/), { target: { value: '5' } })
  fireEvent.change(within(dialog).getByLabelText(/理由/), { target: { value: '  專案需要  ' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'root-password-1' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「alice」的匯出配額'))
  const sent = puts(fetchMock)
  expect(sent).toHaveLength(2)
  expect(JSON.parse(sent[1][1]!.body as string)).toEqual({ mode: 'limit', daily_limit: 5, reason: '專案需要' })
})

test('上限不是整數時不送出；後端錯誤原樣顯示', async () => {
  const fetchMock = mount({
    override: (p, init) => (p === '/api/admin/quota/users/u1/ask' && init?.method === 'PUT'
      ? { status: 403, body: { detail: '只有 super admin 能調整 super admin 的配額', code: 'super_required' } }
      : undefined),
  })
  fireEvent.click((await userRow('alice')).getByRole('button', { name: '調整' }))
  const dialog = await screen.findByRole('dialog', { name: '調整「alice」的配額' })
  fireEvent.change(within(dialog).getByLabelText(/每日上限/), { target: { value: '1.5' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('0–100000 的整數')
  expect(puts(fetchMock)).toHaveLength(0)
  fireEvent.change(within(dialog).getByLabelText('設定'), { target: { value: 'default' } })
  fireEvent.click(within(dialog).getByRole('button', { name: '儲存' }))
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('只有 super admin 能調整')
  expect(JSON.parse(puts(fetchMock)[0][1]!.body as string)).toEqual({ mode: 'default', daily_limit: null, reason: null })
})

test('用量分布：P50／P95 表，換範圍帶新的 days 重抓；上傳沒有超額欄', async () => {
  const fetchMock = mount()
  const table = within(await screen.findByRole('table', { name: '用量分布' }))
  const ask = within(table.getByRole('cell', { name: '問答' }).closest('tr') as HTMLElement)
  expect(ask.getByText('12')).toBeInTheDocument()
  expect(ask.getByText('96')).toBeInTheDocument()
  const upload = within(table.getByRole('cell', { name: '上傳' }).closest('tr') as HTMLElement)
  expect(upload.getAllByText('—').length).toBeGreaterThanOrEqual(3)
  fireEvent.change(screen.getByLabelText('範圍'), { target: { value: '30' } })
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p) === '/api/admin/quota/stats?days=30')).toBe(true))
})

test('正式阻擋時標示，載入失敗顯示後端 detail', async () => {
  mount({
    override: p => (p === '/api/admin/quota'
      ? { body: { ...OVERVIEW, enforcement: { ...OVERVIEW.enforcement, env_ceiling: true, flag_enabled: true, flag_source: 'db', effective: true, mode: 'enforce' } } }
      : undefined),
  })
  expect(await screen.findByText('正式阻擋')).toBeInTheDocument()
  expect(screen.getByText(/今天超出上限（已擋下）/)).toBeInTheDocument()
})

test('API 失敗時顯示後端 detail，不白屏', async () => {
  mount({ override: p => (p === '/api/admin/quota' ? { status: 503, body: { detail: '服務暫時無法使用', code: 'x' } } : undefined) })
  expect(await screen.findByText('配額載入失敗：服務暫時無法使用')).toBeInTheDocument()
})

test('沒有 accounts.manage 時不打 API', async () => {
  const fetchMock = mount({ scopes: ['admin'] })
  expect(await screen.findByText(/accounts\.manage/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin/quota'))).toBe(false)
})
