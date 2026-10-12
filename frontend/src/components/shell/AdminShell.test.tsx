import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { requestJSON } from '../../lib/api'
import { AdminShell } from './AdminShell'

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
})

type Body = Record<string, unknown>
type Api = (url: string) => Body | undefined

function mount(
  role: 'admin' | 'user', path = '/admin/reviews', scopes?: string[],
  opts: { api?: Api; me?: Body } = {},
) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      return new Response(JSON.stringify({ id: 'me', username: 'root', role, scopes, ...opts.me }), { status: 200 })
    }
    return new Response(JSON.stringify(opts.api?.(url) ?? {}), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route index element={<div>總覽內容</div>} />
            <Route path="reviews" element={<div>待複核內容</div>} />
            <Route path="*" element={<div>其他管理頁</div>} />
          </Route>
          <Route path="/search" element={<div>研報平台</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const ALL = ['admin', 'accounts.manage', 'audit.read', 'review.manage', 'ops.read', 'reports.manage', 'analytics.read']
const rail = () => within(screen.getByRole('navigation', { name: '管理領域' }))
const panel = () => within(screen.getByRole('navigation', { name: '管理導覽' }))

test('管理員：圖示軌七個領域、次級面板列該領域的頁面，有回到研報平台與登出，沒有研報側欄', async () => {
  mount('admin', '/admin/reviews', ALL)
  expect(await screen.findByText('待複核內容')).toBeInTheDocument()
  expect(rail().getAllByRole('link').map(a => a.textContent)).toEqual(['總覽', '內容', '存取', '維運', '安全', '洞察', '設定'])
  expect(rail().getByRole('link', { name: '內容' })).toHaveAttribute('aria-current', 'true')
  expect(rail().getByRole('link', { name: '維運' })).toHaveAttribute('href', '/admin/operations/services')
  expect(panel().getAllByRole('link').map(a => a.getAttribute('href'))).toEqual(['/admin/reviews', '/admin/reports', '/admin/uploads'])
  expect(panel().getByRole('link', { name: /待複核/ })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: /回到研報平台/ })).toHaveAttribute('href', '/search')
  expect(screen.getByRole('button', { name: '登出' }).closest('form')).toHaveAttribute('action', '/logout')
  // 研報平台的導覽與歷史對話不在這裡
  expect(screen.queryByRole('link', { name: /^檢索$/ })).not.toBeInTheDocument()
  expect(screen.queryByText('歷史對話')).not.toBeInTheDocument()
})

test('一般使用者：連管理導覽都不畫，只看到無權限頁，也不打任何管理 API', async () => {
  const fetchMock = mount('user')
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理領域' })).not.toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理導覽' })).not.toBeInTheDocument()
  expect(screen.queryByText('待複核內容')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.map(c => c[0])).toEqual(['/api/me'])
})

test('維運領域：四個分組十一頁，麵包屑是「領域 / 分組 / 頁面」', async () => {
  mount('admin', '/admin/operations/logs', ['admin', 'ops.read'])
  await screen.findByText('其他管理頁')
  expect(panel().getAllByRole('link').map(a => a.textContent)).toEqual([
    '服務', '依賴圖', '事件', '管線', '資料健康', '資料庫', '排程工作', '日誌', '主機', '診斷', 'LLM 用量',
  ])
  expect(panel().getByRole('link', { name: '日誌' })).toHaveAttribute('aria-current', 'page')
  expect(rail().getByRole('link', { name: '維運' })).toHaveAttribute('aria-current', 'true')
  expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent('維運/執行/日誌')
})

test('上傳詳情頁裡，「上傳研報」維持選取狀態，「研報管理」不會跟著亮', async () => {
  mount('admin', '/admin/uploads/u-1', ['admin', 'reports.manage'])
  await screen.findByText('其他管理頁')
  expect(panel().getByRole('link', { name: /上傳研報/ })).toHaveAttribute('aria-current', 'page')
  expect(panel().getByRole('link', { name: /研報管理/ })).not.toHaveAttribute('aria-current')
})

test('依 scope 顯示：沒有對應 scope 的頁面不列，整個領域都看不到時連領域一起拿掉', async () => {
  mount('admin', '/admin/reviews', ['admin'])
  await screen.findByText('待複核內容')
  expect(rail().getAllByRole('link').map(a => a.textContent)).toEqual(['總覽', '內容', '存取', '安全'])
  expect(panel().getAllByRole('link').map(a => a.textContent)).toEqual(['待複核'])
  expect(rail().getByRole('link', { name: '安全' })).toHaveAttribute('href', '/admin/audit')
})

test('沒有對應 scope 就不送那個來源的請求', async () => {
  const fetchMock = mount('admin', '/admin/reviews', ['admin', 'review.manage'])
  await screen.findByText('待複核內容')
  await waitFor(() => expect(fetchMock.mock.calls.some(c => String(c[0]).startsWith('/api/review/queue'))).toBe(true))
  const urls = fetchMock.mock.calls.map(c => String(c[0]))
  expect(urls.some(u => u.startsWith('/api/admin/') || u === '/api/progress')).toBe(false)
})

const svc = (name: string, tier: string, summary: string) => ({
  name, kind: 'systemd', tier, target: `report-mark-${name}.service`, timer: null, actions: ['status'],
  description: '', summary, error: null, systemd: null, container: null, timer_state: null,
})
const queue = (kind: string, total: number) => ({ kind, total, limit: 1, offset: 0, has_more: false, next_offset: null, items: [] })
const SIGNAL_API: Api = url => {
  if (url === '/api/admin/ops/services') {
    return { environment: 'production', host: 'office-host', checked_at: '2026-10-06T02:00:00Z',
      items: [svc('web', 'critical', 'running'), svc('backup', 'important', 'failed')] }
  }
  if (url.startsWith('/api/admin/incidents')) {
    return { since: 'a', until: 'b', total: 1, limit: 50, offset: 0, has_more: false, next_offset: null, items: [{
      incident_id: 'i1', host: 'office-host', component: 'host', kind: 'service', status: 'firing', severity: 'WARNING',
      reason: 'disk', summary: '磁碟使用率 87%', opened_at: '2026-10-06T01:00:00Z', last_event_at: '2026-10-06T01:30:00Z',
      event_count: 2 }] }
  }
  if (url.startsWith('/api/review/queue')) {
    const kind = new URLSearchParams(url.split('?')[1]).get('kind')!
    return queue(kind, { faithfulness: 8, feedback: 3, extraction: 5 }[kind] ?? 0)
  }
  if (url.startsWith('/api/admin/uploads')) {
    return { total: 3, limit: 1, offset: 0, has_more: true, next_offset: 1, items: [], scanner: { pending: 0, scanning: 0 } }
  }
  return undefined
}

test('燈號：服務失敗亮紅、進行中的事件亮橘並帶筆數；圖示軌取該領域最嚴重的那一顆', async () => {
  mount('admin', '/admin/operations/logs', ALL, { api: SIGNAL_API })
  await screen.findByText('其他管理頁')
  expect(await panel().findByRole('link', { name: /服務.*有異常/ })).toBeInTheDocument()
  expect(await panel().findByRole('link', { name: /事件.*需要注意/ })).toHaveTextContent('1')
  expect(panel().getByRole('link', { name: /主機.*需要注意/ })).toBeInTheDocument()
  expect(panel().getByRole('link', { name: '日誌' })).toBeInTheDocument()
  expect(rail().getByRole('link', { name: '維運（有異常）' })).toBeInTheDocument()
  // 環境卡來自服務清單
  expect(within(screen.getByLabelText('環境')).getByText('office-host')).toBeInTheDocument()
})

test('待處理佇列只給數字、不亮燈：待複核是三個分頁的待處理數相加，上傳研報是待審草稿數', async () => {
  mount('admin', '/admin/reviews', ALL, { api: SIGNAL_API })
  await screen.findByText('待複核內容')
  await waitFor(() => expect(panel().getByRole('link', { name: /待複核/ })).toHaveTextContent('16'))
  await waitFor(() => expect(panel().getByRole('link', { name: /上傳研報/ })).toHaveTextContent('3'))
  expect(panel().getByRole('link', { name: /待複核/ }).querySelector('[role="img"]')).toBeNull()
  expect(rail().getByRole('link', { name: '內容' })).toBeInTheDocument()
})

test('總覽領域多一組「異常」捷徑，bad 在前；圖示軌的總覽不亮點', async () => {
  mount('admin', '/admin', ALL, { api: SIGNAL_API })
  await screen.findByText('總覽內容')
  await panel().findByRole('link', { name: /服務.*有異常/ })
  expect(panel().getAllByRole('link').map(a => a.getAttribute('href'))).toEqual([
    '/admin', '/admin/operations/services', '/admin/operations/incidents', '/admin/operations/host',
  ])
  expect(panel().getByRole('link', { name: '總覽' })).toHaveAttribute('aria-current', 'page')
  expect(rail().getByRole('link', { name: '總覽' })).toBeInTheDocument()
})

test('主題：預設跟系統，按了切換就寫到 <html data-theme> 並存 localStorage', async () => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('dark'), media: q, addEventListener: () => {}, removeEventListener: () => {},
  }))
  mount('admin', '/admin/reviews', ALL)
  await screen.findByText('待複核內容')
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(screen.getByRole('button', { name: '深色' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: '亮色' }))
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(screen.getByRole('button', { name: '亮色' })).toHaveAttribute('aria-pressed', 'true')
  expect(window.localStorage.getItem('tf.admin.theme')).toBe('light')
})

test('⌘K／Ctrl+K 開頁面搜尋：只列看得到的頁，Enter 跳第一筆，Esc 關閉', async () => {
  mount('admin', '/admin/reviews', ['admin', 'ops.read'])
  await screen.findByText('待複核內容')
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  const dialog = within(await screen.findByRole('dialog', { name: '搜尋頁面' }))
  const input = dialog.getByRole('combobox')
  expect(dialog.queryByRole('option', { name: /研報管理/ })).not.toBeInTheDocument()
  fireEvent.change(input, { target: { value: '找不到的東西' } })
  expect(dialog.getByText('找不到這個頁面')).toBeInTheDocument()
  fireEvent.change(input, { target: { value: '執行' } })
  expect(dialog.getAllByRole('option').map(o => o.textContent)).toEqual([
    '排程工作維運 / 執行', '日誌維運 / 執行', '主機維運 / 執行', '診斷維運 / 執行',
  ])
  fireEvent.keyDown(input, { key: 'ArrowDown' })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(await screen.findByText('其他管理頁')).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: '搜尋頁面' })).not.toBeInTheDocument()
  expect(panel().getByRole('link', { name: '日誌' })).toHaveAttribute('aria-current', 'page')
  // 再開一次，Esc 關
  fireEvent.click(screen.getByRole('button', { name: /搜尋頁面/ }))
  expect(await screen.findByRole('dialog', { name: '搜尋頁面' })).toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: '搜尋頁面' })).not.toBeInTheDocument()
})

test('頁面搜尋關閉後，焦點回到開它之前的那個元素', async () => {
  mount('admin', '/admin/reviews', ALL)
  await screen.findByText('待複核內容')
  // 按鈕開的：回到頂列的「搜尋頁面」
  const opener = screen.getByRole('button', { name: /搜尋頁面/ })
  opener.focus()
  fireEvent.click(opener)
  expect(within(await screen.findByRole('dialog', { name: '搜尋頁面' })).getByRole('combobox')).toHaveFocus()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: '搜尋頁面' })).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
  // 快捷鍵開的：回到當時所在的那個連結
  const link = panel().getByRole('link', { name: /研報管理/ })
  link.focus()
  fireEvent.keyDown(window, { key: 'k', metaKey: true })
  expect(within(await screen.findByRole('dialog', { name: '搜尋頁面' })).getByRole('combobox')).toHaveFocus()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(link).toHaveFocus()
})

test('換頁後把目前所在的領域與頁面捲進畫面（手機寬度下圖示軌與次級面板是橫向捲動的一排）', async () => {
  const scrolled: string[] = []
  const original = Element.prototype.scrollIntoView
  Element.prototype.scrollIntoView = function (this: Element, arg?: boolean | ScrollIntoViewOptions) {
    expect(arg).toEqual({ block: 'nearest', inline: 'nearest' })
    scrolled.push(this.getAttribute('href') ?? '')
  }
  try {
    mount('admin', '/admin/flags', ALL)
    expect(await screen.findByText('其他管理頁')).toBeInTheDocument()
    expect(rail().getByRole('link', { name: '設定' })).toHaveAttribute('aria-current', 'true')
    expect(scrolled).toEqual(['/admin/flags', '/admin/flags'])
    // 換到別的領域：新的那一格再捲一次
    fireEvent.click(rail().getByRole('link', { name: '安全' }))
    await waitFor(() => expect(panel().getByRole('link', { name: '安全' })).toHaveAttribute('aria-current', 'page'))
    expect(scrolled.slice(2)).toEqual(['/admin/security', '/admin/security'])
  } finally {
    Element.prototype.scrollIntoView = original
  }
})

test('不在導覽裡的網址（404）：圖示軌不標任何領域，次級面板退回第一個領域當導覽', async () => {
  mount('admin', '/admin/不存在', ALL)
  expect(await screen.findByText('其他管理頁')).toBeInTheDocument()
  expect(rail().getAllByRole('link')).toHaveLength(7)
  for (const a of rail().getAllByRole('link')) expect(a).not.toHaveAttribute('aria-current')
  expect(panel().getByRole('link', { name: '總覽' })).not.toHaveAttribute('aria-current')
  expect(screen.getByRole('navigation', { name: '位置' })).toHaveTextContent('管理後台')
})

test('頂列：帳號與 super 標籤；重新驗證的視窗還開著時顯示「已驗證」', async () => {
  mount('admin', '/admin/reviews', ALL, {
    me: { is_super: true, elevated_until: new Date(Date.now() + 5 * 60_000).toISOString() },
  })
  await screen.findByText('待複核內容')
  expect(screen.getByText('root')).toBeInTheDocument()
  expect(screen.getByText('super')).toBeInTheDocument()
  expect(screen.getByText('已驗證')).toHaveAttribute('title', '10 分鐘內不用再驗證')
})

test('重新驗證過期或沒驗證過：不顯示「已驗證」', async () => {
  mount('admin', '/admin/reviews', ALL, { me: { elevated_until: new Date(Date.now() - 1000).toISOString() } })
  await screen.findByText('待複核內容')
  expect(screen.queryByText('已驗證')).not.toBeInTheDocument()
  expect(screen.queryByText('super')).not.toBeInTheDocument()
})

test('管理員 TOTP 強制：/api/me 說 mfa_enrollment_required 時，導覽與內容換成 TOTP 設定頁', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin'],
        totp_enabled: false, mfa_enrollment_required: true }), { status: 200 })
    }
    if (url === '/api/me/totp') return new Response(JSON.stringify({ enabled: false, pending: false }), { status: 200 })
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reviews']}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="reviews" element={<div>待複核內容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('heading', { name: '請先開啟兩步驟驗證' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理導覽' })).not.toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '管理領域' })).not.toBeInTheDocument()
  expect(screen.queryByText('待複核內容')).not.toBeInTheDocument()
  // 登出與回到研報平台仍在
  expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '設定兩步驟驗證' }))
  await waitFor(() => expect(fetchMock.mock.calls.map(c => c[0])).toContain('/api/me/totp'))
  // 不打任何管理 API
  expect(fetchMock.mock.calls.some(c => String(c[0]).startsWith('/api/admin/'))).toBe(false)
})

function ProbePage() {
  const q = useQuery({ queryKey: ['probe'], queryFn: () => requestJSON('/api/admin/probe', z.object({})), retry: false })
  return <div>{q.isError ? '探測失敗' : '探測內容'}</div>
}

test('任何管理 API 回 403 mfa_enrollment_required 時，立刻重取 /api/me 並切到 TOTP 設定頁', async () => {
  let meCalls = 0
  const fetchMock = vi.fn(async (url: string) => {
    if (url === '/api/me') {
      meCalls += 1
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin'],
        mfa_enrollment_required: meCalls > 1 }), { status: 200 })
    }
    if (url === '/api/admin/probe') {
      return new Response(JSON.stringify({ detail: '管理員必須先開啟兩步驟驗證', code: 'mfa_enrollment_required' }),
        { status: 403 })
    }
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/probe']}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="probe" element={<ProbePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('heading', { name: '請先開啟兩步驟驗證' })).toBeInTheDocument()
  expect(meCalls).toBeGreaterThanOrEqual(2)
})
