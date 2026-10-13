import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import { AdminShell } from '../../components/shell/AdminShell'
import type { AuditEntry } from '../../lib/adminSchemas'
import c from '../../components/console/Console.module.css'
import AdminApiClientsPage from './AdminApiClientsPage'
import { actionLabel, auditSummary } from './auditLabels'

const copyText = vi.hoisted(() => vi.fn(async (_text: string) => {}))
vi.mock('../../lib/clipboard', () => ({ copyText }))

beforeEach(() => {
  copyText.mockReset()
  copyText.mockResolvedValue(undefined)
})
afterEach(() => {
  vi.unstubAllGlobals()
  dismissToast()
})

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

// 用 span：<output> 的隱含 role 是 status，會和 toast 撞在一起。
function LocationProbe() {
  const l = useLocation()
  return <span data-testid="loc">{l.pathname + l.search}</span>
}

/**
 * 小型假後端：用戶端清單隨寫入變動，回傳的是 fetch mock（斷言送出了什麼）。
 * `elevated`＝/api/me 回報還在重新驗證的 10 分鐘視窗內（預設是），建立與輪替不再詢問。
 */
function mount(opts: { scopes?: string[]; clients?: Client[]; elevated?: boolean; at?: string; override?: Override } = {}) {
  const scopes = opts.scopes ?? ['admin', 'api_clients.manage']
  let clients = opts.clients ?? [apiClient(7, '外部系統 A'), apiClient(8, '外部系統 B', { enabled: false, key_prefix: '89abcdef' })]
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(init.body as string) : undefined
      if (path === '/api/me') {
        return { body: {
          id: 'me', username: 'root', role: 'admin', scopes,
          elevated_until: opts.elevated === false ? null : new Date(Date.now() + 600_000).toISOString(),
        } }
      }
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
      <MemoryRouter initialEntries={[opts.at ?? '/admin/api-clients']}>
        <AdminApiClientsPage />
        <LocationProbe />
      </MemoryRouter>
      <Toaster />
    </QueryClientProvider>,
  )
  return fetchMock
}

const writes = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')
const sent = (fetchMock: ReturnType<typeof mount>) =>
  writes(fetchMock).map(([p, i]) => [p, i!.method, i!.body ? JSON.parse(i!.body as string) : null])

/** 清單裡某個用戶端的那一列。 */
async function row(name: string) {
  const table = within(await screen.findByRole('region', { name: 'API 用戶端清單' }))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr')!)
}

/** 開某一列的「更多」選單並點其中一項。 */
async function pickMore(name: string, item: string) {
  fireEvent.click((await row(name)).getByRole('button', { name: `${name} 的更多操作` }))
  fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: item }))
}

async function moreItems(name: string) {
  fireEvent.click((await row(name)).getByRole('button', { name: `${name} 的更多操作` }))
  const items = within(screen.getByRole('menu')).getAllByRole('menuitem').map(b => b.textContent)
  fireEvent.keyDown(document, { key: 'Escape' })
  return items
}

async function openCreate() {
  fireEvent.click(screen.getByRole('button', { name: '建立用戶端' }))
  return within(await screen.findByRole('dialog', { name: '建立 API 用戶端' }))
}

test('頁首：kicker、標題、一句摘要；長說明收在「說明」後面', async () => {
  mount()
  expect(await screen.findByRole('heading', { level: 1, name: 'API 用戶端' })).toBeInTheDocument()
  expect(screen.getByText('存取')).toBeInTheDocument()
  expect(screen.getByText(/對外 API（\/external\/v1）的金鑰、限流、每日額度與授權範圍/)).toBeInTheDocument()
  expect(screen.queryByText(/系統只保存雜湊值/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/系統只保存雜湊值/)).toBeInTheDocument()
  expect(screen.getByText(/每一筆操作都會留在「操作紀錄」/)).toBeInTheDocument()
})

test('列表：名稱、金鑰前綴、狀態、端點、限流／額度、授權範圍與最後使用；不出現任何完整金鑰', async () => {
  mount({ clients: [
    apiClient(7, '外部系統 A', { last_used_at: '2026-10-07T02:30:00Z', scopes: ['search', 'report.file'] }),
    apiClient(8, '外部系統 B', {
      enabled: false, key_prefix: '89abcdef', note: '暫停使用', scopes: [], entitlements: { market: ['TW'] },
    }),
  ] })
  const a = await row('外部系統 A')
  expect(a.getByText('rmk_0123abcd_…')).toBeInTheDocument()
  expect(a.getByText('啟用')).toHaveAttribute('data-tone', 'ok')
  expect(a.getByText('search、report.file')).toBeInTheDocument()
  expect(a.getByText('60／分')).toBeInTheDocument()
  expect(a.getByText('1,000／日')).toBeInTheDocument()
  expect(a.getByText('台股、美股')).toBeInTheDocument()
  expect(a.getByText('券商：元大')).toBeInTheDocument()
  expect(a.queryByText('尚未使用')).not.toBeInTheDocument()
  const b = await row('外部系統 B')
  // 停用是管理員設定的狀態、不是異常：中性的灰燈，不亮紅
  expect(b.getByText('停用')).toHaveAttribute('data-tone', 'idle')
  // 備註在清單裡最多兩行，完整內容放 title（抽屜裡也有）
  expect(b.getByText('暫停使用')).toHaveAttribute('title', '暫停使用')
  expect(b.getByText('暫停使用')).toHaveClass(c.clamp2)
  expect(b.getByText('（無）')).toBeInTheDocument()
  expect(b.getByText('券商、報告類型、商品類型不限')).toBeInTheDocument()
  expect(b.getByRole('button', { name: '啟用' })).toBeInTheDocument()
  expect(b.getByText('尚未使用')).toBeInTheDocument()
  expect(document.body.textContent).not.toContain('x'.repeat(43))
  // 操作欄釘在右邊：表格橫向捲動時還看得到
  expect(screen.getByRole('columnheader', { name: '操作' })).toHaveClass(c.stickyRight)
  expect(a.getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)
  expect(b.getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)
})

test('列動作：啟用中主要是「輪替金鑰」，停用排在「更多」最後；停用中主要是「啟用」', async () => {
  mount()
  const a = await row('外部系統 A')
  expect(a.getAllByRole('button').map(b => b.textContent)).toEqual(['外部系統 A', '輪替金鑰', '更多'])
  expect(await moreItems('外部系統 A')).toEqual(['調整限流與額度', '變更授權範圍', '編輯備註', '停用'])
  const b = await row('外部系統 B')
  expect(b.getAllByRole('button').map(x => x.textContent)).toEqual(['外部系統 B', '啟用', '更多'])
  expect(await moreItems('外部系統 B')).toEqual(['輪替金鑰', '調整限流與額度', '變更授權範圍', '編輯備註'])
})

test('清單載入失敗顯示後端 detail；沒有任何用戶端時顯示空狀態', async () => {
  mount({ override: (p, init) => (p === '/api/admin/api-clients' && !init?.method
    ? { status: 503, body: { detail: '服務暫時無法使用', code: 'x' } } : undefined) })
  expect(await screen.findByRole('alert')).toHaveTextContent('API 用戶端清單載入失敗：服務暫時無法使用')
})

test('沒有任何用戶端時顯示空狀態', async () => {
  mount({ clients: [] })
  expect(await screen.findByText('還沒有任何 API 用戶端')).toBeInTheDocument()
})

test('建立：送出設定後顯示一次性金鑰，可複製；關閉後金鑰從畫面消失、無法再取得', async () => {
  const fetchMock = mount()
  await row('外部系統 A')
  const form = await openCreate()
  fireEvent.change(form.getByLabelText(/^名稱/), { target: { value: '  外部系統 C  ' } })
  fireEvent.click(form.getByLabelText(/研報原檔連結/))
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '500' } })
  fireEvent.click(form.getByLabelText('台股（TW）'))
  fireEvent.click(form.getByLabelText('港股（HK）'))
  fireEvent.change(form.getByLabelText(/^券商/), { target: { value: '元大、凱基, 元大' } })
  fireEvent.click(form.getByRole('button', { name: '建立' }))

  const keyDialog = await screen.findByRole('dialog', { name: '「外部系統 C」的新金鑰' })
  expect(keyDialog).toHaveTextContent('這把金鑰只會顯示這一次。請現在複製，交給對方後再關閉。')
  expect(within(keyDialog).getByLabelText('API 金鑰')).toHaveTextContent(RAW_KEY)
  expect(screen.queryByRole('dialog', { name: '建立 API 用戶端' })).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('已建立「外部系統 C」')
  const [path, init] = writes(fetchMock)[0]
  expect(path).toBe('/api/admin/api-clients')
  expect(JSON.parse(init!.body as string)).toEqual({
    name: '外部系統 C', scopes: ['search', 'report.file'], rate_limit_per_min: 60, daily_quota: 500,
    entitlements: { market: ['TW', 'HK'], source: ['元大', '凱基'] }, note: null,
  })

  // 複製鈕在金鑰欄位右側，只有一顆；成功後用 toast 說明結果。
  const keyField = within(keyDialog).getByLabelText('API 金鑰').parentElement!
  const copyBtn = within(keyField).getByRole('button', { name: '複製' })
  expect(within(keyDialog).getAllByRole('button').map(b => b.textContent)).toEqual(['複製', '我已複製，關閉'])
  fireEvent.click(copyBtn)
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已複製金鑰'))
  expect(copyText).toHaveBeenCalledWith(RAW_KEY)

  // 只能按關閉鈕離開：Esc 與點遮罩都不關。
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.mouseDown(keyDialog.parentElement!)
  expect(screen.getByRole('dialog', { name: '「外部系統 C」的新金鑰' })).toBeInTheDocument()

  fireEvent.click(within(keyDialog).getByRole('button', { name: '我已複製，關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain(RAW_KEY)
  expect(await row('外部系統 C')).toBeTruthy()
  expect(document.body.textContent).not.toContain('x'.repeat(43))
})

test('複製失敗時照實說，金鑰仍留在畫面上可以手動選取', async () => {
  copyText.mockRejectedValue(new Error('copy failed'))
  mount()
  await row('外部系統 A')
  const form = await openCreate()
  fireEvent.change(form.getByLabelText(/^名稱/), { target: { value: '外部系統 C' } })
  fireEvent.click(form.getByLabelText('台股（TW）'))
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  const keyDialog = await screen.findByRole('dialog', { name: '「外部系統 C」的新金鑰' })
  fireEvent.click(within(keyDialog).getByRole('button', { name: '複製' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('複製失敗，請手動選取金鑰'))
  expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'bad')
  expect(within(keyDialog).getByLabelText('API 金鑰')).toHaveTextContent(RAW_KEY)
})

test('建立：沒填名稱、限流超出範圍、沒選市場都不送出；後端 409 的 detail 顯示在表單裡', async () => {
  const fetchMock = mount({ override: (p, init) => (p === '/api/admin/api-clients' && init?.method === 'POST'
    ? { status: 409, body: { detail: 'API 用戶端「外部系統 A」已存在', code: 'name_taken' } } : undefined) })
  await row('外部系統 A')
  const form = await openCreate()
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  expect(await form.findByRole('alert')).toHaveTextContent('請填寫名稱')
  fireEvent.change(form.getByLabelText(/^名稱/), { target: { value: '外部系統 A' } })
  fireEvent.change(form.getByLabelText(/^每分鐘限流/), { target: { value: '6001' } })
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  expect(form.getByRole('alert')).toHaveTextContent('每分鐘限流需為 1–6000 的整數')
  fireEvent.change(form.getByLabelText(/^每分鐘限流/), { target: { value: '60' } })
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '0' } })
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  expect(form.getByRole('alert')).toHaveTextContent('每日額度需為 1–1,000,000 的整數')
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '1000' } })
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  expect(form.getByRole('alert')).toHaveTextContent('至少要允許一個市場')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(form.getByLabelText('台股（TW）'))
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  await waitFor(() => expect(form.getByRole('alert')).toHaveTextContent('API 用戶端「外部系統 A」已存在'))
  expect(screen.queryByRole('dialog', { name: /新金鑰/ })).not.toBeInTheDocument()
})

test('建立要先重新驗證：還沒驗證時先問身分；取消就不送，表單留著、不算錯誤', async () => {
  const fetchMock = mount({ elevated: false })
  await row('外部系統 A')
  const form = await openCreate()
  fireEvent.change(form.getByLabelText(/^名稱/), { target: { value: '外部系統 C' } })
  fireEvent.click(form.getByLabelText('台股（TW）'))
  fireEvent.click(form.getByRole('button', { name: '建立' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(elevation).toHaveTextContent('「建立 API 用戶端」需要重新驗證')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(elevation).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '重新驗證身分' })).not.toBeInTheDocument())
  expect(screen.getByRole('dialog', { name: '建立 API 用戶端' })).toBeInTheDocument()
  expect(form.queryByRole('alert')).not.toBeInTheDocument()
  expect(writes(fetchMock)).toHaveLength(0)
})

test('停用要先確認；確認後 PATCH enabled=false，清單反映停用', async () => {
  const fetchMock = mount()
  await pickMore('外部系統 A', '停用')
  const dialog = await screen.findByRole('alertdialog', { name: '停用「外部系統 A」？' })
  expect(dialog).toHaveTextContent('對方會收到 401')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: '停用' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已停用「外部系統 A」'))
  expect(sent(fetchMock)).toEqual([['/api/admin/api-clients/7', 'PATCH', { enabled: false }]])
  const a = await row('外部系統 A')
  await waitFor(() => expect(a.getByRole('button', { name: '啟用' })).toBeInTheDocument())
})

test('停用的確認框按取消就不送', async () => {
  const fetchMock = mount()
  await pickMore('外部系統 A', '停用')
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  expect(writes(fetchMock)).toHaveLength(0)
})

test('啟用不必確認：直接 PATCH enabled=true；後端失敗時顯示 detail', async () => {
  let fail = true
  const fetchMock = mount({ override: (p, init) => {
    if (p !== '/api/admin/api-clients/8' || init?.method !== 'PATCH' || !fail) return undefined
    fail = false
    return { status: 409, body: { detail: '這個 API 用戶端缺少授權市場', code: 'x' } }
  } })
  fireEvent.click((await row('外部系統 B')).getByRole('button', { name: '啟用' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('這個 API 用戶端缺少授權市場'))
  expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'bad')
  fireEvent.click((await row('外部系統 B')).getByRole('button', { name: '啟用' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已啟用「外部系統 B」'))
  expect(sent(fetchMock)).toEqual([
    ['/api/admin/api-clients/8', 'PATCH', { enabled: true }],
    ['/api/admin/api-clients/8', 'PATCH', { enabled: true }],
  ])
  await waitFor(async () => expect((await row('外部系統 B')).getByRole('button', { name: '輪替金鑰' })).toBeInTheDocument())
})

test('調整限流與額度：沒改不能存、超出範圍不送、只送有變的欄位', async () => {
  const fetchMock = mount()
  await pickMore('外部系統 A', '調整限流與額度')
  const form = within(await screen.findByRole('dialog', { name: '調整「外部系統 A」的限流與額度' }))
  expect(form.getByLabelText(/^每分鐘限流/)).toHaveValue(60)
  expect(form.getByLabelText(/^每日額度/)).toHaveValue(1000)
  expect(form.getByRole('button', { name: '儲存' })).toBeDisabled()
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '1000001' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  expect(form.getByRole('alert')).toHaveTextContent('每日額度需為 1–1,000,000 的整數')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '1000' } })
  fireEvent.change(form.getByLabelText(/^每分鐘限流/), { target: { value: '120' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「外部系統 A」的限流與額度'))
  expect(sent(fetchMock)).toEqual([['/api/admin/api-clients/7', 'PATCH', { rate_limit_per_min: 120 }]])
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect((await row('外部系統 A')).getByText('120／分')).toBeInTheDocument()
})

test('變更授權範圍：端點有變才 PATCH；授權範圍有變才 PUT 完整清單', async () => {
  const fetchMock = mount()
  await pickMore('外部系統 A', '變更授權範圍')
  const form = within(await screen.findByRole('dialog', { name: '變更「外部系統 A」的授權範圍' }))
  expect(form.getByLabelText(/語意檢索/)).toBeChecked()
  expect(form.getByLabelText('台股（TW）')).toBeChecked()
  expect(form.getByLabelText(/^券商/)).toHaveValue('元大')
  expect(form.getByRole('button', { name: '儲存' })).toBeDisabled()
  fireEvent.click(form.getByLabelText('美股（US）'))
  fireEvent.change(form.getByLabelText(/^券商/), { target: { value: '' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「外部系統 A」的授權範圍'))
  expect(sent(fetchMock)).toEqual([['/api/admin/api-clients/7/entitlements', 'PUT', { market: ['TW'] }]])
})

test('變更授權範圍：端點與授權一起改，先 PATCH 端點再 PUT 授權；市場全部取消不送', async () => {
  const fetchMock = mount()
  await pickMore('外部系統 A', '變更授權範圍')
  const form = within(await screen.findByRole('dialog', { name: '變更「外部系統 A」的授權範圍' }))
  fireEvent.click(form.getByLabelText('台股（TW）'))
  fireEvent.click(form.getByLabelText('美股（US）'))
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  expect(form.getByRole('alert')).toHaveTextContent('至少要允許一個市場')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(form.getByLabelText('港股（HK）'))
  fireEvent.click(form.getByLabelText(/研報原檔連結/))
  fireEvent.change(form.getByLabelText(/^報告類型/), { target: { value: '個股報告，產業報告' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「外部系統 A」的授權範圍'))
  expect(sent(fetchMock)).toEqual([
    ['/api/admin/api-clients/7', 'PATCH', { scopes: ['search', 'report.file'] }],
    ['/api/admin/api-clients/7/entitlements', 'PUT', { market: ['HK'], source: ['元大'], report_type: ['個股報告', '產業報告'] }],
  ])
})

test('編輯備註：送去掉頭尾空白的內容；後端錯誤顯示在表單裡', async () => {
  let fail = true
  const fetchMock = mount({ override: (p, init) => {
    if (p !== '/api/admin/api-clients/7' || init?.method !== 'PATCH' || !fail) return undefined
    fail = false
    return { status: 400, body: { detail: '備註最多 1000 個字元', code: 'invalid' } }
  } })
  await pickMore('外部系統 A', '編輯備註')
  const form = within(await screen.findByRole('dialog', { name: '編輯「外部系統 A」的備註' }))
  expect(form.getByRole('button', { name: '儲存' })).toBeDisabled()
  fireEvent.change(form.getByLabelText(/^備註/), { target: { value: '  合約到 2027  ' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  expect(await form.findByRole('alert')).toHaveTextContent('備註最多 1000 個字元')
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已更新「外部系統 A」的備註'))
  expect(sent(fetchMock)[1]).toEqual(['/api/admin/api-clients/7', 'PATCH', { note: '合約到 2027' }])
  expect((await row('外部系統 A')).getByText('合約到 2027')).toBeInTheDocument()
})

test('輪替金鑰：確認後收到 elevation_required，重新驗證後自動重試並只顯示一次新金鑰', async () => {
  let elevated = false
  const fetchMock = mount({
    override: (p, init) => {
      if (p === '/api/admin/api-clients/7/rotate' && !elevated) {
        return { status: 403, body: { detail: '這項操作需要重新驗證密碼', code: 'elevation_required' } }
      }
      if (p === '/api/me/elevate') {
        expect(JSON.parse(init!.body as string).password).toBe('fixed-test-secret-pw')
        elevated = true
        return { body: { elevated_until: '2026-10-07T00:10:00Z' } }
      }
      return undefined
    },
  })
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '輪替金鑰' }))
  const confirm = await screen.findByRole('alertdialog', { name: '輪替「外部系統 A」的金鑰？' })
  expect(confirm).toHaveTextContent('舊金鑰在下一個請求就失效')
  expect(writes(fetchMock)).toHaveLength(0)
  fireEvent.click(within(confirm).getByRole('button', { name: '輪替' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(elevation).toHaveTextContent('「輪替金鑰」需要重新驗證')
  fireEvent.change(within(elevation).getByLabelText('密碼'), { target: { value: 'fixed-test-secret-pw' } })
  fireEvent.click(within(elevation).getByRole('button', { name: '驗證' }))
  const keyDialog = await screen.findByRole('dialog', { name: '「外部系統 A」的新金鑰' })
  expect(within(keyDialog).getByLabelText('API 金鑰')).toHaveTextContent(ROTATED_KEY)
  expect(screen.getByRole('status')).toHaveTextContent('已輪替「外部系統 A」的金鑰')
  expect(fetchMock.mock.calls.filter(([p]) => p === '/api/admin/api-clients/7/rotate')).toHaveLength(2)
  fireEvent.click(within(keyDialog).getByRole('button', { name: '我已複製，關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain(ROTATED_KEY)
  expect((await row('外部系統 A')).getByText('rmk_fedcba98_…')).toBeInTheDocument()
})

test('輪替金鑰：重新驗證按取消就不輪替，也不顯示錯誤', async () => {
  const fetchMock = mount({ elevated: false })
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '輪替金鑰' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '輪替' }))
  const elevation = await screen.findByRole('dialog', { name: '重新驗證身分' })
  fireEvent.click(within(elevation).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(writes(fetchMock)).toHaveLength(0)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

test('輪替失敗時顯示後端 detail，不出現金鑰對話框', async () => {
  mount({ override: p => (p === '/api/admin/api-clients/7/rotate'
    ? { status: 404, body: { detail: 'API 用戶端不存在', code: 'not_found' } } : undefined) })
  fireEvent.click((await row('外部系統 A')).getByRole('button', { name: '輪替金鑰' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '輪替' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('API 用戶端不存在'))
  expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'bad')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('詳情抽屜：點名稱或整列打開並寫進 URL；列動作不會誤開；關閉拿掉參數', async () => {
  mount({ clients: [
    apiClient(7, '外部系統 A', {
      note: '合約到 2027', last_used_at: '2026-10-07T02:30:00Z', key_rotated_at: '2026-10-06T02:30:00Z',
      scopes: ['search', 'report.file'],
    }),
    apiClient(8, '外部系統 B', { enabled: false, key_prefix: '89abcdef' }),
  ] })
  const a = await row('外部系統 A')
  fireEvent.click(a.getByRole('button', { name: '輪替金鑰' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/api-clients')
  expect(screen.getByTestId('loc')).not.toHaveTextContent('client=')

  fireEvent.click(a.getByRole('button', { name: '外部系統 A，查看詳情' }))
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/api-clients?client=7')
  const drawer = within(await screen.findByRole('dialog', { name: '外部系統 A' }))
  expect(drawer.getByText('啟用')).toBeInTheDocument()
  expect(drawer.getAllByText('rmk_0123abcd_…').length).toBeGreaterThanOrEqual(1)
  expect(drawer.getByText('60／分')).toBeInTheDocument()
  expect(drawer.getByText('1,000／日')).toBeInTheDocument()
  expect(drawer.getByText('report.file')).toBeInTheDocument()
  expect(drawer.getByText('台股')).toBeInTheDocument()
  expect(drawer.getByText('元大')).toBeInTheDocument()
  expect(drawer.getAllByText('不限')).toHaveLength(2)
  expect(drawer.getByText('合約到 2027')).toBeInTheDocument()
  expect(document.body.textContent).not.toContain('x'.repeat(43))
  fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByTestId('loc')).not.toHaveTextContent('client=')

  fireEvent.click((await row('外部系統 B')).getByText('rmk_89abcdef_…'))
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/api-clients?client=8')
  const b = within(await screen.findByRole('dialog', { name: '外部系統 B' }))
  expect(b.getByText('停用')).toBeInTheDocument()
  expect(b.getByText('建立後沒有輪替過')).toBeInTheDocument()
  expect(b.getByText('尚未使用')).toBeInTheDocument()
  expect(b.getByText('沒有備註')).toBeInTheDocument()
  expect(b.getByRole('button', { name: '啟用' })).toBeInTheDocument()
})

test('詳情抽屜：直接開帶 ?client= 的連結就是打開的；抽屜裡的動作走同一套流程', async () => {
  const fetchMock = mount({ at: '/admin/api-clients?client=7' })
  const drawer = within(await screen.findByRole('dialog', { name: '外部系統 A' }))
  fireEvent.click(drawer.getByRole('button', { name: '調整限流與額度' }))
  const form = within(await screen.findByRole('dialog', { name: '調整「外部系統 A」的限流與額度' }))
  fireEvent.change(form.getByLabelText(/^每日額度/), { target: { value: '50' } })
  fireEvent.click(form.getByRole('button', { name: '儲存' }))
  await waitFor(() => expect(sent(fetchMock)).toEqual([['/api/admin/api-clients/7', 'PATCH', { daily_quota: 50 }]]))
  await waitFor(() => expect(drawer.getByText('50／日')).toBeInTheDocument())
  // 抽屜的「停用」只是開確認框：紅字不填色；實心紅留給確認框的最後一步
  expect(drawer.getByRole('button', { name: '停用' })).toHaveClass(c.btnDangerText)
  expect(drawer.getByRole('button', { name: '停用' })).not.toHaveClass(c.btnDanger)
  fireEvent.click(drawer.getByRole('button', { name: '停用' }))
  const confirm = await screen.findByRole('alertdialog', { name: '停用「外部系統 A」？' })
  expect(within(confirm).getByRole('button', { name: '停用' })).toHaveClass(c.btnDanger)
  fireEvent.click(within(confirm).getByRole('button', { name: '停用' }))
  await waitFor(() => expect(drawer.getByRole('button', { name: '啟用' })).toBeInTheDocument())
  expect(screen.getByTestId('loc')).toHaveTextContent('/admin/api-clients?client=7')
})

test('?client= 指到不存在的用戶端時不開抽屜，清單照常顯示', async () => {
  mount({ at: '/admin/api-clients?client=999' })
  expect(await row('外部系統 A')).toBeTruthy()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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
      <MemoryRouter initialEntries={['/admin/users']}>
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
