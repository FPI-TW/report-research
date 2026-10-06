import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminReportsPage from './AdminReportsPage'

afterEach(() => vi.unstubAllGlobals())

type Report = {
  report_id: string; file_hash: string; file_name: string; title: string | null; source: string | null
  market: string | null; report_date: string | null; created_at: string | null; hidden: boolean
  hidden_reason: string | null; visibility_updated_by: string | null; visibility_updated_at: string | null
}
type Reply = { status?: number; body: unknown }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const H1 = 'a'.repeat(64)
const H2 = 'b'.repeat(64)
const report = (hash: string, over: Partial<Report> = {}): Report => ({
  report_id: `r-${hash.slice(0, 4)}`, file_hash: hash, file_name: `${hash.slice(0, 4)}.pdf`, title: null,
  source: '凱基', market: 'TW', report_date: '2026-10-01', created_at: '2026-10-02T01:00:00Z', hidden: false,
  hidden_reason: null, visibility_updated_by: null, visibility_updated_at: null, ...over,
})

/** 小型假後端：清單隨隱藏／恢復變動；回傳 fetch mock。 */
function mount(opts: { scopes?: string[]; reports?: Report[]; override?: Override } = {}) {
  let reports = opts.reports ?? [report(H1, { title: '台積電深度報告' }), report(H2, {
    hidden: true, hidden_reason: '重複上傳', visibility_updated_by: 'root', visibility_updated_at: '2026-10-05T01:00:00Z',
  })]
  const scopes = opts.scopes ?? ['admin', 'reports.manage']
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      const method = init?.method ?? 'GET'
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', scopes } }
      if (path.startsWith('/api/admin/reports?') || path === '/api/admin/reports') {
        const url = new URL(path, 'http://x')
        const hidden = url.searchParams.get('hidden')
        const q = url.searchParams.get('q')
        let items = reports
        if (hidden != null) items = items.filter(r => r.hidden === (hidden === 'true'))
        if (q) items = items.filter(r => (r.title ?? r.file_name).includes(q))
        return { body: { total: items.length, limit: 50, offset: 0, has_more: false, next_offset: null, items } }
      }
      const m = path.match(/^\/api\/admin\/reports\/([0-9a-f]+)\/visibility$/)
      if (m && method === 'PUT') {
        const body = JSON.parse(init!.body as string)
        reports = reports.map(r => r.file_hash === m[1]
          ? { ...r, hidden: body.hidden, hidden_reason: body.hidden ? body.reason : null,
              visibility_updated_by: 'root', visibility_updated_at: '2026-10-06T01:00:00Z' }
          : r)
        return { body: { file_hash: m[1], hidden: body.hidden, reason: body.reason ?? null, updated_by: 'root',
          updated_at: '2026-10-06T01:00:00Z' } }
      }
      return { status: 404, body: { detail: 'not found', code: 'not_found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/reports']}><AdminReportsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const puts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')

async function row(name: string) {
  const table = within(await screen.findByRole('table'))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr') as HTMLElement)
}

test('清單：顯示中可連到閱讀頁、已隱藏顯示原因與操作者', async () => {
  mount()
  expect(await screen.findByRole('heading', { name: '研報管理' })).toBeInTheDocument()
  const visible = await row('台積電深度報告')
  expect(visible.getByRole('link', { name: '台積電深度報告' })).toHaveAttribute('href', `/report/${H1}`)
  expect(visible.getByText('顯示中')).toBeInTheDocument()
  expect(visible.getByRole('button', { name: '隱藏' })).toBeInTheDocument()
  const hidden = await row('bbbb.pdf')
  expect(hidden.queryByRole('link')).not.toBeInTheDocument()
  expect(hidden.getByText('已隱藏')).toBeInTheDocument()
  expect(hidden.getByText('重複上傳')).toBeInTheDocument()
  expect(hidden.getByRole('button', { name: '恢復' })).toBeInTheDocument()
})

test('空清單：說明沒有研報；篩選後沒有結果則說沒有符合條件的', async () => {
  const fetchMock = mount({ reports: [] })
  expect(await screen.findByText('還沒有任何研報')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('狀態'), { target: { value: 'hidden' } })
  expect(await screen.findByText('沒有符合條件的研報')).toBeInTheDocument()
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p).includes('hidden=true'))).toBe(true))
})

test('搜尋：送出 q 參數', async () => {
  const fetchMock = mount()
  await row('台積電深度報告')
  fireEvent.change(screen.getByLabelText('關鍵字（標題／檔名／券商）'), { target: { value: '台積' } })
  fireEvent.click(screen.getByRole('button', { name: '搜尋' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p).includes(`q=${encodeURIComponent('台積')}`))).toBe(true))
})

test('隱藏必填原因：空白不送出；填了才 PUT，成功後重抓清單', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog'))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '   ' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await dialog.findByText('隱藏研報必須填寫原因')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)

  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '  內容有誤  ' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await screen.findByText('已隱藏「台積電深度報告」')).toBeInTheDocument()
  const [path, init] = puts(fetchMock)[0]
  expect(path).toBe(`/api/admin/reports/${H1}/visibility`)
  expect(JSON.parse(init!.body as string)).toEqual({ hidden: true, reason: '內容有誤' })
  const updated = await row('台積電深度報告')
  expect(await updated.findByText('內容有誤')).toBeInTheDocument()
  expect(updated.getByRole('button', { name: '恢復' })).toBeInTheDocument()
})

test('隱藏失敗（後端 400）：在對話框裡原樣顯示原因', async () => {
  mount({
    override: (_path, init) => init?.method === 'PUT'
      ? { status: 400, body: { detail: '原因最多 500 字', code: 'invalid_input' } } : undefined,
  })
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog'))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '太長' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await dialog.findByText('原因最多 500 字')).toBeInTheDocument()
})

test('恢復：確認後 PUT hidden=false（不帶原因），清單更新為顯示中', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('bbbb.pdf')).getByRole('button', { name: '恢復' }))
  const dialog = within(await screen.findByRole('dialog'))
  fireEvent.click(dialog.getByRole('button', { name: '恢復' }))
  expect(await screen.findByText('已恢復「bbbb.pdf」')).toBeInTheDocument()
  expect(JSON.parse(puts(fetchMock)[0][1]!.body as string)).toEqual({ hidden: false })
  const updated = await row('bbbb.pdf')
  expect(await updated.findByText('顯示中')).toBeInTheDocument()
})

test('後端 403：顯示錯誤訊息，不白屏', async () => {
  mount({
    override: path => path.startsWith('/api/admin/reports')
      ? { status: 403, body: { detail: '缺少權限：reports.manage', code: 'scope_required' } } : undefined,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('研報清單載入失敗：缺少權限：reports.manage')
})

test('沒有 reports.manage：只顯示需要權限的說明，不打研報 API', async () => {
  const fetchMock = mount({ scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「研報管理」權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin/reports'))).toBe(false)
})
