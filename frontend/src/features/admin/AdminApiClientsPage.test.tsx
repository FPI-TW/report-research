import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { AdminShell } from '../../components/shell/AdminShell'
import type { AuditEntry } from '../../lib/adminSchemas'
import AdminApiClientsPage from './AdminApiClientsPage'
import { actionLabel, auditSummary } from './auditLabels'

const copyText = vi.hoisted(() => vi.fn(async (_text: string) => {}))
vi.mock('../../lib/clipboard', () => ({ copyText }))

beforeEach(() => copyText.mockClear())
afterEach(() => vi.unstubAllGlobals())

// 假金鑰在執行期組出：寫成 `KEY = 'rmk_…'` 的字面值會被 gitleaks 的 generic-api-key 判成金鑰。
const fakeKey = (prefix: string, fill: string) => ['rmk', prefix, fill.repeat(43)].join('_')
const RAW_KEY = fakeKey('0123abcd', 'x')
const ROTATED_KEY = fakeKey('fedcba98', 'y')

type Client = {
  id: number; name: string; key_prefix: string; enabled: boolean; scopes: ('search' | 'report.file')[]
  rate_limit_per_min: number; daily_quota: number
  entitlements: { market: string[]; source?: string[] | null; report_type?: string[] | null; instrument_type?: string[] | null }
  note: string | null; created_at: string | null; updated_at: string | null; key_rotated_at: string | null
  last_used_at: string | null
}
type Reply = { status?: number; body: unknown }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const apiClient = (id: number, name: string, over: Partial<Client> = {}): Client => ({
  id, name, key_prefix: '0123abcd', enabled: true, scopes: ['search'], rate_limit_per_min: 60, daily_quota: 1000,
  entitlements: { market: ['TW', 'US'], source: ['元大'], report_type: null, instrument_type: null },
  note: null, created_at: '2026-10-07T01:00:00Z', updated_at: '2026-10-07T01:00:00Z', key_rotated_at: null,
  last_used_at: null, ...over,
})

/** 小型假後端：用戶端清單隨寫入變動，回傳的是 fetch mock（斷言送出了什麼）。 */
function mount(opts: { scopes?: string[]; clients?: Client[]; override?: Override } = {}) {
  const scopes = opts.scopes ?? ['admin', 'api_clients.manage']
  let clients = opts.clients ?? [apiClient(7, '外部系統 A'), apiClient(8, '外部系統 B', { enabled: false, key_prefix: '89abcdef' })]
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(init.body as string) : undefined
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', scopes } }
      if (path === '/api/admin/api-clients' && method === 'GET') return { body: { items: clients } }
      if (path.startsWith('/api/admin/audit')) {
        return { body: { total: 0, limit: 20, offset: 0, has_more: false, next_offset: null, items: [] } }
      }
      if (path === '/api/admin/api-clients' && method === 'POST') {
        const created = apiClient(9, body.name, {
          scopes: body.scopes, rate_limit_per_min: body.rate_limit_per_min, daily_quota: body.daily_quota,
          entitlements: body.entitlements, note: body.note ?? null, key_prefix: '0123abcd',
        })
        clients = [...clients, created]
        return { status: 201, body: { ...created, api_key: RAW_KEY } }
      }
      const m = path.match(/^\/api\/admin\/api-clients\/(\d+)(\/(entitlements|rotate))?$/)
      if (m) {
        const id = Number(m[1])
        const target = clients.find(c => c.id === id)
        if (!target) return { status: 404, body: { detail: 'API 用戶端不存在', code: 'not_found' } }
        let next: Client = target
        if (m[3] === 'rotate') next = { ...target, key_prefix: 'fedcba98' }
        else if (m[3] === 'entitlements') next = { ...target, entitlements: body }
        else next = { ...target, ...body }
        clients = clients.map(c => (c.id === id ? next : c))
        return m[3] === 'rotate' ? { body: { ...next, api_key: ROTATED_KEY } } : { body: next }
      }
      return { status: 404, body: { detail: 'not found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/api-clients']}><AdminApiClientsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const writes = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')

/** 清單裡某個用戶端的那一列。 */
async function row(name: string) {
  const table = within(await screen.findByRole('region', { name: 'API 用戶端清單' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr')!)
}

test('列表：名稱、金鑰前綴、狀態、端點、限流／額度、授權範圍與最後使用；不出現任何完整金鑰', async () => {
  mount({ clients: [
    apiClient(7, '外部系統 A', { last_used_at: '2026-10-07T02:30:00Z', scopes: ['search', 'report.file'] }),
    apiClient(8, '外部系統 B', { enabled: false, key_prefix: '89abcdef', note: '暫停使用' }),
  ] })
  const a = await row('外部系統 A')
  expect(a.getByText('rmk_0123abcd_…')).toBeInTheDocument()
  expect(a.getByText('啟用')).toBeInTheDocument()
  expect(a.getByText('search、report.file')).toBeInTheDocument()
  expect(a.getByText('60／分')).toBeInTheDocument()
  expect(a.getByText('1,000／日')).toBeInTheDocument()
  expect(a.getByText('台股、美股；券商：元大')).toBeInTheDocument()
  expect(a.queryByText('—')).not.toBeInTheDocument()
  const b = await row('外部系統 B')
  expect(b.getByText('停用')).toBeInTheDocument()
  expect(b.getByText('暫停使用')).toBeInTheDocument()
  expect(b.getByRole('button', { name: '啟用' })).toBeInTheDocument()
  expect(b.getByText('—')).toBeInTheDocument()
  expect(document.body.textContent).not.toContain('x'.repeat(43))
})

test('建立：送出設定後顯示一次性金鑰，可複製；關閉後金鑰從畫面消失、無法再取得', async () => {
  const fetchMock = mount()
  await row('外部系統 A')
  fireEvent.click(screen.getByRole('button', { name: '建立 API 用戶端' }))
  const form = await screen.findByRole('dialog', { name: '建立 API 用戶端' })
  fireEvent.change(within(form).getByLabelText('名稱'), { target: { value: '  外部系統 C  ' } })
  fireEvent.click(within(form).getByLabelText(/研報原檔連結/))
  fireEvent.change(within(form).getByLabelText('每日額度'), { target: { value: '500' } })
  fireEvent.click(within(form).getByLabelText('台股（TW）'))
  fireEvent.click(within(form).getByLabelText('港股（HK）'))
  fireEvent.change(within(form).getByLabelText(/^券商/), { target: { value: '元大、凱基, 元大' } })
  fireEvent.click(within(form).getByRole('button', { name: '建立' }))

  const keyDialog = await screen.findByRole('dialog', { name: '「外部系統 C」的 API 金鑰' })
  expect(keyDialog).toHaveTextContent('請立即複製並妥善保存，關閉後將無法再次查看。')
  expect(within(keyDialog).getByLabelText('API 金鑰')).toHaveTextContent(RAW_KEY)
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/api-clients')
  expect(JSON.parse(init!.body as string)).toEqual({
    name: '外部系統 C', scopes: ['search', 'report.file'], rate_limit_per_min: 60, daily_quota: 500,
    entitlements: { market: ['TW', 'HK'], source: ['元大', '凱基'] }, note: null,
  })

  // 複製鈕是金鑰欄位右側的 icon：成功後同一顆按鈕的 icon 換成勾勾，不另顯示文字訊息。
  const keyField = within(keyDialog).getByLabelText('API 金鑰').parentElement!
  const copyBtn = within(keyField).getByRole('button', { name: '複製金鑰' })
  expect(copyBtn.querySelector('.lucide-copy')).not.toBeNull()
  fireEvent.click(copyBtn)
  await waitFor(() => expect(copyBtn.querySelector('.lucide-check')).not.toBeNull())
  expect(copyText).toHaveBeenCalledWith(RAW_KEY)
  expect(within(keyDialog).getAllByRole('button', { name: '複製金鑰' })).toHaveLength(1)
  expect(keyDialog).not.toHaveTextContent('已複製到剪貼簿')

  fireEvent.click(within(keyDialog).getByRole('button', { name: '我已妥善保存，關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain(RAW_KEY)
  expect(await row('外部系統 C')).toBeTruthy()
})

test('建立：沒選市場不送出；後端 409 的 detail 顯示在表單裡', async () => {
  const fetchMock = mount({ override: (p, init) => (p === '/api/admin/api-clients' && init?.method === 'POST'
    ? { status: 409, body: { detail: 'API 用戶端「外部系統 A」已存在', code: 'name_taken' } } : undefined) })
  await row('外部系統 A')
  fireEvent.click(screen.getByRole('button', { name: '建立 API 用戶端' }))
  const form = await screen.findByRole('dialog', { name: '建立 API 用戶端' })
  fireEvent.change(within(form).getByLabelText('名稱'), { target: { value: '外部系統 A' } })
  fireEvent.click(within(form).getByRole('button', { name: '建立' }))
  expect(await within(form).findByRole('alert')).toHaveTextContent('至少要允許一個市場')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(form).getByLabelText('台股（TW）'))
  fireEvent.click(within(form).getByRole('button', { name: '建立' }))
  await waitFor(() => expect(within(form).getByRole('alert')).toHaveTextContent('API 用戶端「外部系統 A」已存在'))
})

test('停用要先確認；確認後 PATCH enabled=false，清單反映停用', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '停用' }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog).toHaveTextContent('停用「外部系統 A」？')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: '停用' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已停用「外部系統 A」'))
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/api-clients/7')
  expect(init!.method).toBe('PATCH')
  expect(JSON.parse(init!.body as string)).toEqual({ enabled: false })
  const a = await row('外部系統 A')
  await waitFor(() => expect(a.getByRole('button', { name: '啟用' })).toBeInTheDocument())
})

test('編輯：只送有變的設定；授權範圍有變才 PUT 完整清單', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '編輯' }))
  const form = await screen.findByRole('dialog', { name: '編輯「外部系統 A」' })
  expect(within(form).getByLabelText(/^名稱/)).toBeDisabled()
  expect(within(form).getByLabelText('台股（TW）')).toBeChecked()
  fireEvent.change(within(form).getByLabelText('每分鐘限流'), { target: { value: '120' } })
  fireEvent.click(within(form).getByLabelText('美股（US）'))
  fireEvent.change(within(form).getByLabelText(/^券商/), { target: { value: '' } })
  fireEvent.click(within(form).getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「外部系統 A」'))
  const sent = writes(fetchMock).map(([p, i]) => [p, i!.method, JSON.parse(i!.body as string)])
  expect(sent).toEqual([
    ['/api/admin/api-clients/7', 'PATCH', { rate_limit_per_min: 120 }],
    ['/api/admin/api-clients/7/entitlements', 'PUT', { market: ['TW'] }],
  ])
})

test('輪替金鑰：確認後收到 elevation_required，重新驗證後自動重試並只顯示一次新金鑰', async () => {
  let elevated = false
  const fetchMock = mount({
    override: (p, init) => {
      if (p === '/api/admin/api-clients/7/rotate' && !elevated) {
        return { status: 403, body: { detail: '這項操作需要重新驗證密碼', code: 'elevation_required' } }
      }
      if (p === '/api/admin/elevate') {
        expect(JSON.parse(init!.body as string).password).toBe('root-password-1')
        elevated = true
        return { body: { elevated_until: '2026-10-07T00:10:00Z' } }
      }
      return undefined
    },
  })
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '輪替金鑰' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '輪替金鑰' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'root-password-1' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  const keyDialog = await screen.findByRole('dialog', { name: '「外部系統 A」的 API 金鑰' })
  expect(within(keyDialog).getByLabelText('API 金鑰')).toHaveTextContent(ROTATED_KEY)
  expect(fetchMock.mock.calls.filter(([p]) => p === '/api/admin/api-clients/7/rotate')).toHaveLength(2)
  fireEvent.click(within(keyDialog).getByRole('button', { name: '我已妥善保存，關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain(ROTATED_KEY)
  expect(screen.getByRole('status')).toHaveTextContent('已輪替「外部系統 A」的金鑰')
})

test('沒有 api_clients.manage：頁面只說明需要權限，不打任何 /api/admin/*', async () => {
  const fetchMock = mount({ scopes: ['admin', 'reports.manage'] })
  expect(await screen.findByRole('heading', { name: '需要「API 用戶端管理」權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin'))).toBe(false)
})

function mountShell(scopes: string[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) =>
    new Response(JSON.stringify(url === '/api/me' ? { id: 'me', username: 'root', role: 'admin', scopes } : {}),
      { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/audit']}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="*" element={<div>其他管理頁</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('管理導覽：有 api_clients.manage 才出現「API 用戶端」入口', async () => {
  mountShell(['admin', 'api_clients.manage'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /API 用戶端/ })).toHaveAttribute('href', '/admin/api-clients')
})

test('管理導覽：沒有 api_clients.manage 時不出現「API 用戶端」入口', async () => {
  mountShell(['admin'])
  const nav = within(await screen.findByRole('navigation', { name: '管理導覽' }))
  expect(nav.getByRole('link', { name: /帳號管理/ })).toBeInTheDocument()
  expect(nav.queryByRole('link', { name: /API 用戶端/ })).not.toBeInTheDocument()
})

const entry = (over: Partial<AuditEntry>): AuditEntry => ({
  id: 1, actor_user_id: 'u1', actor_username: 'root', action: 'api_client.create', target_type: 'api_client',
  target_id: '7', detail: {}, created_at: '2026-10-07T02:00:00Z', ...over,
} as AuditEntry)

test('稽核：API 用戶端四種動作的中文摘要，不顯示金鑰前綴', () => {
  expect(actionLabel('api_client.create')).toBe('建立 API 用戶端')
  expect(actionLabel('api_client.update')).toBe('變更 API 用戶端設定')
  expect(actionLabel('api_client.entitlements')).toBe('變更 API 用戶端授權範圍')
  expect(actionLabel('api_client.rotate')).toBe('輪替 API 用戶端金鑰')
  const create = auditSummary(entry({ detail: {
    name: '外部系統 A', key_prefix: '0123abcd', scopes: ['report.file', 'search'], rate_limit_per_min: 60,
    daily_quota: 1000, entitlements: { market: ['TW', 'US'] },
  } }))
  expect(create).toBe('外部系統 A・端點 report.file、search・市場 TW、US')
  expect(auditSummary(entry({ action: 'api_client.update', detail: {
    name: '外部系統 A', key_prefix: '0123abcd',
    before: { enabled: true, daily_quota: 1000, note: '舊備註' }, after: { enabled: false, daily_quota: 50, note: '新備註' },
  } }))).toBe('外部系統 A・停用・每日額度 1000 → 50・變更備註')
  expect(auditSummary(entry({ action: 'api_client.entitlements', detail: {
    name: '外部系統 A', key_prefix: '0123abcd',
    before: { market: ['TW', 'US'], source: ['元大'] }, after: { market: ['TW'] },
  } }))).toBe('外部系統 A・市場 TW、US → TW・變更券商')
  const rotate = auditSummary(entry({ action: 'api_client.rotate', detail: {
    name: '外部系統 A', old_key_prefix: '0123abcd', key_prefix: 'fedcba98',
  } }))
  expect(rotate).toBe('外部系統 A・已換新金鑰')
  for (const s of [create, rotate]) {
    expect(s).not.toContain('0123abcd')
    expect(s).not.toContain('fedcba98')
  }
})
