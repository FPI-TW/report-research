import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import c from '../../components/console/Console.module.css'
import AdminReportsPage from './AdminReportsPage'
import s from './reports/Reports.module.css'

afterEach(() => { vi.unstubAllGlobals(); dismissToast() })

type Report = {
  report_id: string; file_hash: string; file_name: string; title: string | null; source: string | null
  market: string | null; report_date: string | null; created_at: string | null; hidden: boolean
  hidden_reason: string | null; visibility_updated_by: string | null; visibility_updated_at: string | null
  publication?: 'draft' | 'published'
}
type Reply = { status?: number; body: unknown; headers?: Record<string, string> }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const H1 = 'a'.repeat(64)
const H2 = 'b'.repeat(64)
const report = (hash: string, over: Partial<Report> = {}): Report => ({
  report_id: `r-${hash.slice(0, 4)}`, file_hash: hash, file_name: `${hash.slice(0, 4)}.pdf`, title: null,
  source: '凱基', market: 'TW', report_date: '2026-10-01', created_at: '2026-10-02T01:00:00Z', hidden: false,
  hidden_reason: null, visibility_updated_by: null, visibility_updated_at: null, ...over,
})

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname}{search}</output>
}

/** 小型假後端：清單隨隱藏／恢復變動；回傳 fetch mock。 */
function mount(opts: { scopes?: string[]; reports?: Report[]; override?: Override; path?: string } = {}) {
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
        const publication = url.searchParams.get('publication')
        const q = url.searchParams.get('q')
        let items = reports
        if (hidden != null) items = items.filter(r => r.hidden === (hidden === 'true'))
        if (publication) items = items.filter(r => (r.publication ?? 'published') === publication)
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
      if (path === '/api/admin/reports/bulk-visibility' && method === 'POST') {
        const body = JSON.parse(init!.body as string)
        const hide = body.action === 'hide'
        const results = (body.file_hashes as string[]).map(h => ({ file_hash: h, status: 'ok', hidden: hide }))
        reports = reports.map(r => body.file_hashes.includes(r.file_hash)
          ? { ...r, hidden: hide, hidden_reason: hide ? body.reason : null } : r)
        return { body: { action: body.action, requested: results.length, ok: results.length, skipped: 0, results } }
      }
      return { status: 404, body: { detail: 'not found', code: 'not_found' } }
    })()
    return new Response(typeof reply.body === 'string' ? reply.body : JSON.stringify(reply.body),
      { status: reply.status ?? 200, headers: reply.headers })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[opts.path ?? '/admin/reports']}><AdminReportsPage /><Where /><Toaster /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const puts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
/** 清單本身的查詢（頁首數字的查詢是 limit=1，不算）。 */
const listCalls = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.map(([p]) => String(p)).filter(p => p.startsWith('/api/admin/reports?') && p.includes('limit=50'))

async function row(name: string) {
  const table = within(await screen.findByRole('table'))
  const cell = await table.findByRole('cell', { name: new RegExp(`^${name}`) })
  return within(cell.closest('tr') as HTMLElement)
}

const toastText = async (text: string | RegExp) => expect(await screen.findByText(text)).toBeInTheDocument()

test('頁首：kicker、標題與一句摘要；長說明收在「說明」後面', async () => {
  mount()
  expect(await screen.findByRole('heading', { level: 1, name: '研報管理' })).toBeInTheDocument()
  expect(screen.getByText('內容')).toBeInTheDocument()
  expect(screen.queryByText(/重新入庫也不會失效/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/重新入庫也不會失效/)).toBeInTheDocument()
  expect(screen.getByText(/每一筆操作都會留在「操作紀錄」/)).toBeInTheDocument()
})

test('頁首數字：語料、已隱藏、上傳草稿各取清單端點的 total', async () => {
  const H3 = 'c'.repeat(64)
  mount({ reports: [report(H1), report(H2, { hidden: true }), report(H3, { publication: 'draft' })] })
  const tiles = within(await screen.findByRole('group', { name: '研報數量' }))
  const tile = (label: string) => within(tiles.getByText(label).parentElement as HTMLElement)
  await waitFor(() => expect(tile('語料').getByText('3')).toBeInTheDocument())
  expect(tile('已隱藏').getByText('1')).toBeInTheDocument()
  expect(tile('上傳草稿').getByText('1')).toBeInTheDocument()
})

test('清單：已發布的主要動作是隱藏、已隱藏顯示原因與操作者，主要動作是恢復', async () => {
  mount()
  const visible = await row('台積電深度報告')
  expect(visible.getByText('已發布')).toBeInTheDocument()
  expect(visible.getByRole('button', { name: '隱藏' })).toBeInTheDocument()
  expect(visible.queryByRole('button', { name: '恢復' })).not.toBeInTheDocument()
  const hidden = await row('bbbb.pdf')
  expect(hidden.queryByRole('link')).not.toBeInTheDocument()
  expect(hidden.getByText('已隱藏')).toBeInTheDocument()
  // 隱藏原因在清單裡截成一行，完整內容放 title（抽屜也有全文）
  expect(hidden.getByText('重複上傳')).toHaveAttribute('title', '重複上傳')
  expect(hidden.getByText('重複上傳')).toHaveClass(c.truncate)
  expect(hidden.getByText(/^root・/)).toBeInTheDocument()
  expect(hidden.getByRole('button', { name: '恢復' })).toBeInTheDocument()
  expect(hidden.queryByRole('button', { name: '隱藏' })).not.toBeInTheDocument()
  // 操作欄釘在右邊：表格橫向捲動時還看得到
  expect(screen.getByRole('columnheader', { name: '操作' })).toHaveClass(c.stickyRight)
  expect(visible.getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)
  expect(hidden.getAllByRole('cell').at(-1)).toHaveClass(c.stickyRight)
})

test('點列開詳情抽屜並寫進網址；已發布可連到閱讀頁，關閉後網址拿掉參數', async () => {
  mount()
  const visible = await row('台積電深度報告')
  fireEvent.click(visible.getByText('凱基'))
  const drawer = within(await screen.findByRole('dialog', { name: '台積電深度報告' }))
  expect(screen.getByTestId('where')).toHaveTextContent(`/admin/reports?report=${H1}`)
  expect(drawer.getByText('已發布')).toBeInTheDocument()
  expect(drawer.getByText('aaaa.pdf', { selector: 'dd' })).toBeInTheDocument()
  expect(drawer.getByText(H1)).toBeInTheDocument()
  expect(drawer.getByRole('link', { name: '開啟閱讀頁' })).toHaveAttribute('href', `/report/${H1}`)
  // 抽屜的「隱藏」只是開原因表單：紅字不填色；實心紅留給表單的最後一步
  expect(drawer.getByRole('button', { name: '隱藏' })).toHaveClass(c.btnDangerText)
  expect(drawer.getByRole('button', { name: '隱藏' })).not.toHaveClass(c.btnDanger)
  fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByTestId('where')).toHaveTextContent(/^\/admin\/reports$/)
})

test('列的鍵盤入口是標題按鈕；已隱藏的抽屜顯示原因，不連閱讀頁，動作是恢復', async () => {
  mount()
  fireEvent.click((await row('bbbb.pdf')).getByRole('button', { name: 'bbbb.pdf' }))
  const drawer = within(await screen.findByRole('dialog', { name: 'bbbb.pdf' }))
  expect(drawer.getByText('已隱藏')).toBeInTheDocument()
  expect(drawer.getByText('重複上傳')).toBeInTheDocument()
  expect(drawer.queryByRole('link')).not.toBeInTheDocument()
  expect(drawer.getByRole('button', { name: '恢復' })).toBeInTheDocument()
})

test('網址帶 ?report= 直接開抽屜；這一頁沒有那一篇時說明找不到', async () => {
  mount({ path: `/admin/reports?report=${H2}` })
  expect(await screen.findByRole('dialog', { name: 'bbbb.pdf' })).toBeInTheDocument()
})

test('網址帶的 file_hash 不在這一頁：說明找不到並列出 hash', async () => {
  const missing = 'e'.repeat(64)
  mount({ path: `/admin/reports?report=${missing}` })
  const drawer = within(await screen.findByRole('dialog', { name: '找不到這份研報' }))
  expect(drawer.getByText(/不在目前這一頁的清單裡/)).toBeInTheDocument()
  expect(drawer.getByText(missing)).toBeInTheDocument()
})

test('列內的勾選框與按鈕不會開抽屜', async () => {
  mount()
  const visible = await row('台積電深度報告')
  fireEvent.click(visible.getByRole('checkbox'))
  fireEvent.click(visible.getByRole('button', { name: '隱藏' }))
  expect(await screen.findByRole('dialog', { name: '隱藏「台積電深度報告」' })).toBeInTheDocument()
  expect(screen.getByTestId('where')).toHaveTextContent(/^\/admin\/reports$/)
})

test('草稿：顯示草稿、不連閱讀頁、不提供隱藏與恢復，導向上傳審核', async () => {
  const H3 = 'c'.repeat(64)
  const fetchMock = mount({ reports: [report(H1, { title: '台積電深度報告' }), report(H3, { publication: 'draft' })] })
  const draft = await row('cccc.pdf')
  expect(draft.getByText('草稿')).toBeInTheDocument()
  expect(draft.queryByText('已發布')).not.toBeInTheDocument()
  // 不連閱讀頁（草稿一般頁面 404）；操作欄連到上傳清單的「待審草稿」分頁籤。
  expect(draft.getByRole('link', { name: '前往審核' })).toHaveAttribute('href', '/admin/uploads?tab=draft')
  expect(draft.getAllByRole('link').every(a => !a.getAttribute('href')?.startsWith('/report/'))).toBe(true)
  expect(draft.queryByRole('button', { name: '隱藏' })).not.toBeInTheDocument()
  expect(draft.queryByRole('button', { name: '恢復' })).not.toBeInTheDocument()
  // 抽屜裡同樣只有「前往審核」。
  fireEvent.click(draft.getByRole('button', { name: 'cccc.pdf' }))
  const drawer = within(await screen.findByRole('dialog', { name: 'cccc.pdf' }))
  expect(drawer.getByRole('link', { name: '前往審核' })).toHaveAttribute('href', '/admin/uploads?tab=draft')
  expect(drawer.getAllByRole('link').every(a => !a.getAttribute('href')?.startsWith('/report/'))).toBe(true)
  expect(drawer.queryByRole('button', { name: '隱藏' })).not.toBeInTheDocument()
  expect(drawer.queryByRole('button', { name: '恢復' })).not.toBeInTheDocument()
  // 舊回應沒有 publication 欄位時視同已發布。
  expect((await row('台積電深度報告')).getByRole('button', { name: '隱藏' })).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)
})

test('空清單：說明沒有研報；篩選後沒有結果則說沒有符合條件的', async () => {
  const fetchMock = mount({ reports: [] })
  expect(await screen.findByText('還沒有任何研報')).toBeInTheDocument()
  fireEvent.click(within(screen.getByRole('group', { name: '狀態' })).getByRole('button', { name: '已隱藏' }))
  expect(await screen.findByText('沒有符合條件的研報')).toBeInTheDocument()
  await waitFor(() => expect(listCalls(fetchMock).some(p => p.includes('hidden=true'))).toBe(true))
})

test('狀態篩選：已發布送 hidden=false＋publication=published，草稿送 publication=draft', async () => {
  const fetchMock = mount()
  await row('台積電深度報告')
  const seg = within(screen.getByRole('group', { name: '狀態' }))
  fireEvent.click(seg.getByRole('button', { name: '已發布' }))
  await waitFor(() => expect(listCalls(fetchMock).some(p => p.includes('hidden=false') && p.includes('publication=published'))).toBe(true))
  expect(seg.getByRole('button', { name: '已發布' })).toHaveAttribute('aria-pressed', 'true')
  await waitFor(() => expect(screen.queryByText('bbbb.pdf')).not.toBeInTheDocument())
  fireEvent.click(seg.getByRole('button', { name: '草稿' }))
  await waitFor(() => expect(listCalls(fetchMock).some(p => p.includes('publication=draft') && !p.includes('hidden='))).toBe(true))
})

test('搜尋：送出 q 參數', async () => {
  const fetchMock = mount()
  await row('台積電深度報告')
  fireEvent.change(screen.getByLabelText('標題／檔名／券商關鍵字'), { target: { value: '台積' } })
  fireEvent.click(screen.getByRole('button', { name: '搜尋' }))
  await waitFor(() => expect(listCalls(fetchMock).some(p => p.includes(`q=${encodeURIComponent('台積')}`))).toBe(true))
})

test('匯出 CSV：帶目前的篩選條件', async () => {
  const fetchMock = mount({
    override: path => path.startsWith('/api/admin/export/reports.csv')
      ? { body: 'a,b\n', headers: { 'X-Export-Rows': '1' } } : undefined,
  })
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} }))
  await row('台積電深度報告')
  fireEvent.click(within(screen.getByRole('group', { name: '狀態' })).getByRole('button', { name: '已隱藏' }))
  await waitFor(() => expect(screen.queryByText('台積電深度報告')).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: /匯出 CSV/ }))
  expect(await screen.findByText('已下載 1 筆')).toBeInTheDocument()
  const exported = fetchMock.mock.calls.map(([p]) => String(p)).find(p => p.startsWith('/api/admin/export/reports.csv'))
  expect(exported).toContain('hidden=true')
})

test('隱藏必填原因：空白不送出；填了才 PUT，成功後重抓清單', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏「台積電深度報告」' }))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '   ' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await dialog.findByText('隱藏研報必須填寫原因')).toBeInTheDocument()
  expect(puts(fetchMock)).toHaveLength(0)

  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '  內容有誤  ' } })
  expect(dialog.getByText('4／500')).toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  await toastText('已隱藏「台積電深度報告」')
  const [path, init] = puts(fetchMock)[0]
  expect(path).toBe(`/api/admin/reports/${H1}/visibility`)
  expect(JSON.parse(init!.body as string)).toEqual({ hidden: true, reason: '內容有誤' })
  const updated = await row('台積電深度報告')
  expect(await updated.findByText('內容有誤')).toBeInTheDocument()
  expect(updated.getByRole('button', { name: '恢復' })).toBeInTheDocument()
})

test('隱藏原因超過 500 字：不送出並說明上限', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏「台積電深度報告」' }))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '字'.repeat(501) } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('原因最多 500 字')
  expect(puts(fetchMock)).toHaveLength(0)
})

test('隱藏失敗（後端 400）：在對話框裡原樣顯示原因', async () => {
  mount({
    override: (_path, init) => init?.method === 'PUT'
      ? { status: 400, body: { detail: '原因最多 500 字', code: 'invalid_input' } } : undefined,
  })
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏「台積電深度報告」' }))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '太長' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  expect(await dialog.findByText('原因最多 500 字')).toBeInTheDocument()
})

test('從抽屜隱藏：成功後抽屜關閉、網址拿掉參數', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('台積電深度報告')).getByRole('button', { name: '台積電深度報告' }))
  fireEvent.click(within(await screen.findByRole('dialog', { name: '台積電深度報告' })).getByRole('button', { name: '隱藏' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏「台積電深度報告」' }))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '版權疑慮' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏' }))
  await toastText('已隱藏「台積電深度報告」')
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByTestId('where')).toHaveTextContent(/^\/admin\/reports$/)
  expect(puts(fetchMock)).toHaveLength(1)
})

test('恢復：確認後 PUT hidden=false（不帶原因），清單更新為已發布', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('bbbb.pdf')).getByRole('button', { name: '恢復' }))
  const dialog = within(await screen.findByRole('alertdialog', { name: '恢復「bbbb.pdf」？' }))
  fireEvent.click(dialog.getByRole('button', { name: '恢復' }))
  await toastText('已恢復「bbbb.pdf」')
  expect(JSON.parse(puts(fetchMock)[0][1]!.body as string)).toEqual({ hidden: false })
  const updated = await row('bbbb.pdf')
  expect(await updated.findByText('已發布')).toBeInTheDocument()
})

test('恢復：取消不送出', async () => {
  const fetchMock = mount()
  fireEvent.click((await row('bbbb.pdf')).getByRole('button', { name: '恢復' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  expect(puts(fetchMock)).toHaveLength(0)
})

test('恢復失敗：toast 顯示後端訊息', async () => {
  mount({
    override: (_path, init) => init?.method === 'PUT'
      ? { status: 409, body: { detail: '這份研報是尚未發布的草稿', code: 'report_is_draft' } } : undefined,
  })
  fireEvent.click((await row('bbbb.pdf')).getByRole('button', { name: '恢復' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '恢復' }))
  await toastText('恢復失敗：這份研報是尚未發布的草稿')
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

const posts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')

const H3 = 'c'.repeat(64)
const H4 = 'd'.repeat(64)
const four = () => [
  report(H1, { title: '台積電深度報告' }),
  report(H2, { hidden: true, hidden_reason: '重複上傳' }),
  report(H3, { publication: 'draft' }),
  report(H4, { title: '聯發科季報' }),
]

test('勾選：草稿不可勾；全選本頁只選可操作的列，動作列依狀態分別計數', async () => {
  mount({ reports: four() })
  const draft = await row('cccc.pdf')
  expect(draft.getByRole('checkbox')).toBeDisabled()
  expect(draft.getByRole('checkbox')).toHaveAttribute('title', expect.stringContaining('草稿不能隱藏或恢復'))
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: '全選本頁' }))
  const bar = within(await screen.findByRole('toolbar', { name: '批次操作' }))
  expect(bar.getByText('已選 3 篇')).toBeInTheDocument()
  // 有勾選時整條工具列釘在頁面頂端：捲到下面的列也按得到批次按鈕
  expect(screen.getByRole('toolbar', { name: '批次操作' }).parentElement).toHaveClass(s.toolbarSticky)
  expect(bar.getByRole('button', { name: '批次隱藏（2）' })).toBeEnabled()
  expect(bar.getByRole('button', { name: '批次恢復（1）' })).toBeEnabled()
  expect(draft.getByRole('checkbox')).not.toBeChecked()
  // 取消一列 → 全選變半勾；再按清除 → 全部取消
  fireEvent.click((await row('聯發科季報')).getByRole('checkbox'))
  expect(bar.getByText('已選 2 篇')).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: '全選本頁' })).toHaveProperty('indeterminate', true)
  fireEvent.click(bar.getByRole('button', { name: '清除' }))
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
})

test('批次列：沒有可套用的列時按鈕停用並說明原因', async () => {
  mount({ reports: four() })
  fireEvent.click((await row('bbbb.pdf')).getByRole('checkbox'))
  const bar = within(await screen.findByRole('toolbar', { name: '批次操作' }))
  const hide = bar.getByRole('button', { name: '批次隱藏（0）' })
  expect(hide).toBeDisabled()
  expect(hide).toHaveAttribute('title', '選取的研報都已隱藏')
  expect(bar.getByRole('button', { name: '批次恢復（1）' })).toBeEnabled()
})

test('批次隱藏：確認框列出對象且必填原因；送出 file_hashes＋原因，顯示逐筆結果（含略過原因）', async () => {
  const fetchMock = mount({
    reports: four(),
    override: (path, init) => path === '/api/admin/reports/bulk-visibility' && init?.method === 'POST'
      ? { body: { action: 'hide', requested: 2, ok: 1, skipped: 1, results: [
        { file_hash: H1, status: 'ok', hidden: true },
        { file_hash: H4, status: 'skipped', code: 'report_is_draft', detail: '這份研報是尚未發布的草稿' },
      ] } }
      : undefined,
  })
  fireEvent.click((await row('台積電深度報告')).getByRole('checkbox'))
  fireEvent.click((await row('聯發科季報')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '批次隱藏（2）' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏 2 篇研報' }))
  expect(dialog.getByText('「台積電深度報告」、「聯發科季報」')).toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: '隱藏 2 篇' }))
  expect(await dialog.findByText('隱藏研報必須填寫原因')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: ' 版權疑慮 ' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏 2 篇' }))
  const result = await screen.findByRole('status', { name: '批次結果' })
  expect(result).toHaveTextContent('批次隱藏：成功 1 筆、略過 1 筆')
  expect(result).toHaveTextContent('聯發科季報：這份研報是尚未發布的草稿')
  const [path, init] = posts(fetchMock)[0]
  expect(path).toBe('/api/admin/reports/bulk-visibility')
  expect(JSON.parse(init!.body as string)).toEqual({ action: 'hide', file_hashes: [H1, H4], reason: '版權疑慮' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
  // 逐筆結果可以關掉。
  fireEvent.click(within(result).getByRole('button', { name: '關閉' }))
  expect(screen.queryByRole('status', { name: '批次結果' })).not.toBeInTheDocument()
})

test('批次隱藏失敗（422）：在對話框裡顯示後端訊息，選取保留', async () => {
  mount({
    reports: four(),
    override: (path, init) => path === '/api/admin/reports/bulk-visibility' && init?.method === 'POST'
      ? { status: 422, body: { detail: '原因最多 500 字', code: 'invalid_input' } } : undefined,
  })
  fireEvent.click((await row('台積電深度報告')).getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: '批次隱藏（1）' }))
  const dialog = within(await screen.findByRole('dialog', { name: '隱藏 1 篇研報' }))
  fireEvent.change(dialog.getByLabelText(/原因/), { target: { value: '太長' } })
  fireEvent.click(dialog.getByRole('button', { name: '隱藏 1 篇' }))
  expect(await dialog.findByText('原因最多 500 字')).toBeInTheDocument()
  expect(screen.getByRole('toolbar', { name: '批次操作' })).toHaveTextContent('已選 1 篇')
})

test('批次恢復：只送已隱藏的列、不帶原因，清單更新；沒有略過就只跳 toast', async () => {
  const fetchMock = mount({ reports: four() })
  await row('台積電深度報告')
  fireEvent.click(screen.getByRole('checkbox', { name: '全選本頁' }))
  fireEvent.click(await screen.findByRole('button', { name: '批次恢復（1）' }))
  const dialog = within(await screen.findByRole('alertdialog', { name: '恢復 1 篇研報？' }))
  expect(dialog.getByText(/「bbbb.pdf」/)).toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: '恢復 1 篇' }))
  await toastText('批次恢復：成功 1 筆、略過 0 筆')
  expect(screen.queryByRole('status', { name: '批次結果' })).not.toBeInTheDocument()
  expect(JSON.parse(posts(fetchMock)[0][1]!.body as string)).toEqual({ action: 'restore', file_hashes: [H2] })
  expect(await (await row('bbbb.pdf')).findByText('已發布')).toBeInTheDocument()
})

test('換篩選就清掉選取（批次只作用在目前這一頁）', async () => {
  mount({ reports: four() })
  fireEvent.click((await row('台積電深度報告')).getByRole('checkbox'))
  expect(await screen.findByRole('toolbar', { name: '批次操作' })).toBeInTheDocument()
  fireEvent.click(within(screen.getByRole('group', { name: '狀態' })).getByRole('button', { name: '已發布' }))
  await waitFor(() => expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument())
})

test('分頁：顯示筆數範圍，下一頁帶 offset，換頁清掉選取', async () => {
  const fetchMock = mount({
    override: path => {
      if (!path.startsWith('/api/admin/reports?') || !path.includes('limit=50')) return undefined
      const offset = Number(new URL(path, 'http://x').searchParams.get('offset') ?? 0)
      const items = offset === 0 ? [report(H1, { title: '台積電深度報告' })] : [report(H2, { title: '第二頁的研報' })]
      return { body: { total: 51, limit: 50, offset, has_more: offset === 0, next_offset: offset === 0 ? 50 : null, items } }
    },
  })
  fireEvent.click((await row('台積電深度報告')).getByRole('checkbox'))
  expect(screen.getByText('第 1–1 筆，共 51 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '下一頁' }))
  await row('第二頁的研報')
  expect(listCalls(fetchMock).some(p => p.includes('offset=50'))).toBe(true)
  expect(screen.getByText('第 51–51 筆，共 51 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '下一頁' })).toBeDisabled()
  expect(screen.queryByRole('toolbar', { name: '批次操作' })).not.toBeInTheDocument()
})
