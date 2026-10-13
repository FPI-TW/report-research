import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import AdminAuditPage from './AdminAuditPage'
import { auditCategory } from './auditCategories'
import { actionLabel, auditSummary } from './auditLabels'

afterEach(() => vi.unstubAllGlobals())

const ITEMS = [
  { id: 4, actor_user_id: 'me', actor_username: 'root', action: 'flag.update', target_type: 'flag', target_id: 'qa.agentic',
    detail: { key: 'qa.agentic', note_changed: true, after: { enabled: false } }, created_at: '2026-10-05T03:00:00Z' },
  { id: 3, actor_user_id: 'me', actor_username: 'root', action: 'ops.restart', target_type: 'service', target_id: 'web',
    detail: {}, created_at: '2026-10-05T02:00:00Z' },
  { id: 2, actor_user_id: null, actor_username: null, action: 'user.create', target_type: 'user', target_id: 'u1',
    detail: { username: 'root', role: 'admin', via: 'cli' }, created_at: '2026-10-05T01:00:00Z' },
  { id: 1, actor_user_id: 'me', actor_username: 'root', action: 'user.something_new', target_type: 'user',
    target_id: 'u2', detail: { username: 'alice' }, created_at: '2026-10-05T00:00:00Z' },
]

function Where() {
  return <output data-testid="where">{useLocation().search}</output>
}
const where = () => screen.getByTestId('where').textContent

/** 另一頁才有的一筆（`?entry=7` 不在第一頁時，要靠單筆端點取）。 */
const ELSEWHERE = { id: 7, actor_user_id: 'me', actor_username: 'root', action: 'report.hide', target_type: 'report',
  target_id: 'a'.repeat(64), detail: { file_name: '台積電.pdf', has_reason: true }, created_at: '2026-10-04T00:00:00Z' }

interface MountOptions {
  role?: 'admin' | 'user'
  search?: string
  /** 舊版後端：不認得 prefix（回沒篩的一頁、回應沒有 prefix 鍵），也沒有單筆端點。 */
  legacy?: boolean
  /** 單筆端點的回應；預設照 id 找（找不到 404）。 */
  entryStatus?: number
}

function mount({ role = 'admin', search = '', legacy = false, entryStatus }: MountOptions = {}) {
  const fetchMock = vi.fn(async (path: string) => {
    if (path === '/api/me') return new Response(JSON.stringify({ id: 'me', username: 'root', role }))
    const url = new URL(path, 'http://x')
    const single = /^\/api\/admin\/audit\/(\d+)$/.exec(url.pathname)
    if (single) {
      const found = [...ITEMS, ELSEWHERE].find(e => String(e.id) === single[1])
      if (legacy) return new Response(JSON.stringify({ detail: 'Not Found', code: 'not_found' }), { status: 404 })
      if (entryStatus) return new Response(JSON.stringify({ detail: '帳號服務暫時無法使用', code: 'unavailable' }), { status: entryStatus })
      return found
        ? new Response(JSON.stringify(found))
        : new Response(JSON.stringify({ detail: '找不到這筆操作紀錄', code: 'not_found' }), { status: 404 })
    }
    const offset = Number(url.searchParams.get('offset'))
    const prefix = url.searchParams.getAll('prefix')
    const exclude = url.searchParams.getAll('exclude_prefix')
    if (legacy) {
      const body = offset === 0
        ? { total: 21, limit: 20, offset: 0, has_more: true, next_offset: 20, items: ITEMS }
        : { total: 21, limit: 20, offset: 20, has_more: false, next_offset: null, items: [ITEMS[3]] }
      return new Response(JSON.stringify(body))
    }
    // 新版後端：照前綴篩，total 與分頁都是篩選後的，並帶回套用的前綴。
    if (prefix.length > 0 || exclude.length > 0) {
      const items = ITEMS.filter(e => (prefix.length === 0 || prefix.some(p => e.action.startsWith(p)))
        && !exclude.some(p => e.action.startsWith(p)))
      return new Response(JSON.stringify({
        total: items.length, limit: 20, offset, has_more: false, next_offset: null, prefix, exclude_prefix: exclude,
        items: offset === 0 ? items : [],
      }))
    }
    const body = offset === 0
      ? { total: 21, limit: 20, offset: 0, has_more: true, next_offset: 20, items: ITEMS }
      : { total: 21, limit: 20, offset: 20, has_more: false, next_offset: null, items: [ITEMS[3]] }
    return new Response(JSON.stringify({ ...body, prefix: [], exclude_prefix: [] }))
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/admin/audit${search}`]}><AdminAuditPage /><Where /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

/** 打過的稽核清單請求（不含 /api/me 與單筆）。 */
const listCalls = (fetchMock: ReturnType<typeof mount>) => fetchMock.mock.calls
  .map(([p]) => new URL(String(p), 'http://x'))
  .filter(u => u.pathname === '/api/admin/audit')

test('操作紀錄：CLI 操作顯示為指令列、未知 action 原樣顯示、可翻頁（頁碼在 URL）', async () => {
  const fetchMock = mount()
  expect(await screen.findByRole('heading', { level: 1, name: '操作紀錄' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: '最近的操作' })).toBeInTheDocument()
  expect(await screen.findByRole('cell', { name: '指令列（CLI）' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'root・管理員・經指令列' })).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: 'user.something_new' })).toBeInTheDocument()
  expect(screen.getByText('第 1–4 筆，共 21 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '匯出 CSV' })).toBeEnabled()
  expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '下一頁' }))
  expect(where()).toBe('?offset=20')
  await screen.findByText('第 21–21 筆，共 21 筆')
  expect(fetchMock.mock.calls.some(([p]) => String(p).includes('offset=20'))).toBe(true)
  expect(screen.getByRole('button', { name: '下一頁' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '上一頁' }))
  expect(where()).toBe('')
  await screen.findByText('第 1–4 筆，共 21 筆')
})

test('分類篩選：交給後端篩，筆數與翻頁是篩選後的；分類放在 URL、換分類回第一頁', async () => {
  const fetchMock = mount({ search: '?offset=20' })
  await screen.findByText('第 21–21 筆，共 21 筆')
  const seg = screen.getByRole('group', { name: '分類' })
  // 後端篩的時候不知道其他分類各有幾筆：分段不帶數字，也沒有「只篩這一頁」的說明。
  expect(within(seg).getByRole('button', { name: '全部' })).toHaveTextContent(/^全部$/)
  expect(screen.queryByText(/只篩/)).not.toBeInTheDocument()
  fireEvent.click(within(seg).getByRole('button', { name: '帳號' }))
  expect(where()).toBe('?category=acct')
  expect(await screen.findByText('第 1–2 筆，共 2 筆')).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: '指令列（CLI）' })).toBeInTheDocument()
  expect(screen.queryByRole('cell', { name: '重新啟動服務' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '下一頁' })).toBeDisabled()
  const acct = listCalls(fetchMock).at(-1)!
  expect([acct.searchParams.getAll('prefix'), acct.searchParams.get('offset')]).toEqual([['user.'], '0'])

  // 「內容」是多個前綴；這份假資料裡沒有，說「沒有」而不是「這一頁沒有」。
  fireEvent.click(within(seg).getByRole('button', { name: '內容' }))
  expect(await screen.findByText('沒有「內容」的操作')).toBeInTheDocument()
  expect(screen.getByText('共 0 筆')).toBeInTheDocument()
  expect(listCalls(fetchMock).at(-1)!.searchParams.getAll('prefix')).toEqual(['review.', 'report.', 'qa_content.', 'upload.'])

  // 「安全與設定」是其餘全部：排除其他三類的前綴，後端沒見過的 action 也落在這裡。
  fireEvent.click(within(seg).getByRole('button', { name: '安全與設定' }))
  expect(where()).toBe('?category=sec')
  expect(await screen.findByText('第 1–1 筆，共 1 筆')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '變更功能旗標' })).toBeInTheDocument()
  const sec = listCalls(fetchMock).at(-1)!
  expect(sec.searchParams.getAll('prefix')).toEqual([])
  expect(sec.searchParams.getAll('exclude_prefix')).toEqual(['user.', 'review.', 'report.', 'qa_content.', 'upload.', 'ops.'])

  fireEvent.click(within(seg).getByRole('button', { name: '全部' }))
  expect(where()).toBe('')
  await screen.findByText('第 1–4 筆，共 21 筆')
  expect(screen.getAllByRole('row')).toHaveLength(5)
})

test('連結帶分類進來：第一個請求就帶前綴；匯出 CSV 跟著目前的分類', async () => {
  const fetchMock = mount({ search: '?category=ops' })
  expect(await screen.findByText('第 1–1 筆，共 1 筆')).toBeInTheDocument()
  expect(listCalls(fetchMock).every(u => u.searchParams.getAll('prefix').join() === 'ops.')).toBe(true)
  fetchMock.mockImplementationOnce(async () => new Response('id\n3\n', { headers: { 'X-Export-Rows': '1' } }))
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([p]) => String(p) === '/api/admin/export/audit.csv?prefix=ops.')).toBe(true))
})

test('舊版後端不認得分類參數：退回只篩目前這一頁，照舊標明並帶這一頁的筆數', async () => {
  mount({ legacy: true, search: '?offset=0' })
  await screen.findByRole('cell', { name: '指令列（CLI）' })
  const seg = screen.getByRole('group', { name: '分類（只篩這一頁）' })
  expect(within(seg).getByRole('button', { name: /全部/ })).toHaveTextContent('4')
  expect(within(seg).getByRole('button', { name: /帳號/ })).toHaveTextContent('2')
  fireEvent.click(within(seg).getByRole('button', { name: /維運/ }))
  expect(where()).toBe('?offset=0&category=ops')
  expect(await screen.findByText('分類只篩目前這一頁')).toBeInTheDocument()
  expect(screen.getByRole('cell', { name: '重新啟動服務' })).toBeInTheDocument()
  expect(screen.queryByRole('cell', { name: '指令列（CLI）' })).not.toBeInTheDocument()
  expect(screen.getByText('第 1–4 筆，共 21 筆・這一頁符合 1 筆')).toBeInTheDocument()
  fireEvent.click(within(seg).getByRole('button', { name: /內容/ }))
  expect(await screen.findByText('這一頁沒有「內容」的操作')).toBeInTheDocument()
})

test('稽核 action 的分類只看前綴；沒見過的歸安全與設定', () => {
  expect(auditCategory('user.bulk_action')).toBe('acct')
  expect(auditCategory('upload.publish')).toBe('content')
  expect(auditCategory('qa_content.read')).toBe('content')
  expect(auditCategory('ops.run')).toBe('ops')
  expect(auditCategory('session.admin_revoke')).toBe('sec')
  expect(auditCategory('data.export')).toBe('sec')
  expect(auditCategory('brand.new')).toBe('sec')
})

test('點列開詳情抽屜（?entry=）：顯示完整 detail；關閉後參數拿掉', async () => {
  mount()
  fireEvent.click(await screen.findByRole('cell', { name: 'flag qa.agentic' }))
  expect(where()).toBe('?entry=4')
  const drawer = await screen.findByRole('dialog', { name: '變更功能旗標' })
  expect(within(drawer).getByText('flag.update')).toBeInTheDocument()
  // 對象與內容摘要各一次
  expect(within(drawer).getAllByText('flag qa.agentic')).toHaveLength(2)
  expect(within(drawer).getByText('安全與設定')).toBeInTheDocument()
  const detail = within(drawer).getByLabelText('完整 detail')
  expect(JSON.parse(detail.textContent ?? '')).toEqual({ key: 'qa.agentic', note_changed: true, after: { enabled: false } })
  fireEvent.click(within(drawer).getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(where()).toBe('')
})

test('列內的按鈕是鍵盤入口：開同一個抽屜，只多一筆瀏覽紀錄；沒有 detail 的列講明', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '重新啟動服務' }))
  expect(where()).toBe('?entry=3')
  const drawer = await screen.findByRole('dialog', { name: '重新啟動服務' })
  expect(within(drawer).getByText('這筆操作沒有額外的 detail。')).toBeInTheDocument()
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('連結帶頁碼與 entry 進來：直接開那一筆；不在這一頁的 entry 講明找不到', async () => {
  mount({ search: '?offset=20&entry=1' })
  const drawer = await screen.findByRole('dialog', { name: 'user.something_new' })
  expect(within(drawer).getByText('alice')).toBeInTheDocument()
  fireEvent.click(within(drawer).getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(where()).toBe('?offset=20'))
})

test('entry 不在目前這一頁：向後端取那一筆，抽屜照樣開得出來', async () => {
  const fetchMock = mount({ search: '?entry=7' })
  const drawer = await screen.findByRole('dialog', { name: '隱藏研報' })
  expect(within(drawer).getByText('#7')).toBeInTheDocument()
  expect(within(drawer).getByText('report.hide')).toBeInTheDocument()
  expect(JSON.parse(within(drawer).getByLabelText('完整 detail').textContent ?? '')).toEqual({ file_name: '台積電.pdf', has_reason: true })
  expect(fetchMock.mock.calls.filter(([p]) => String(p) === '/api/admin/audit/7')).toHaveLength(1)
  // 清單裡沒有這一筆，表格照舊。
  expect(screen.getAllByRole('row')).toHaveLength(5)
})

test('entry 在目前這一頁：不另外打單筆端點', async () => {
  const fetchMock = mount({ search: '?entry=3' })
  await screen.findByRole('dialog', { name: '重新啟動服務' })
  expect(fetchMock.mock.calls.some(([p]) => /^\/api\/admin\/audit\/\d+$/.test(String(p)))).toBe(false)
})

test('entry 不存在：抽屜說沒有這個編號，不再說「不在目前這一頁」', async () => {
  const fetchMock = mount({ search: '?entry=999' })
  const drawer = await screen.findByRole('dialog', { name: '找不到這筆紀錄' })
  expect(within(drawer).getByText('沒有編號 #999 的操作紀錄。')).toBeInTheDocument()
  expect(within(drawer).queryByText(/不在目前這一頁/)).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([p]) => String(p) === '/api/admin/audit/999')).toHaveLength(1)
  fireEvent.click(within(drawer).getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(where()).toBe(''))
})

test('亂填的 entry：當成找不到，不拿去打 API', async () => {
  const fetchMock = mount({ search: '?entry=abc' })
  expect(await screen.findByRole('dialog', { name: '找不到這筆紀錄' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin/audit/'))).toBe(false)
})

test('單筆載入失敗（不是 404）：說出原因，不說成找不到', async () => {
  mount({ search: '?entry=7', entryStatus: 503 })
  const drawer = await screen.findByRole('dialog', { name: '無法載入這筆紀錄' })
  expect(within(drawer).getByRole('alert')).toHaveTextContent('#7 載入失敗：帳號服務暫時無法使用')
})

test('舊版後端沒有單筆端點：不去打，照舊說不在目前這一頁', async () => {
  const fetchMock = mount({ legacy: true, search: '?entry=999' })
  const drawer = await screen.findByRole('dialog', { name: '找不到這筆紀錄' })
  expect(within(drawer).getByText(/#999 不在目前這一頁/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin/audit/'))).toBe(false)
})

test('一般使用者：無權限頁，不打稽核 API', async () => {
  const fetchMock = mount({ role: 'user' })
  expect(await screen.findByRole('heading', { name: '需要管理員權限' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).startsWith('/api/admin'))).toBe(false)
})

test('查看問答內容的稽核：中文標籤＋對象 qa id＋佇列種類（detail 本來就沒有內容可顯示）', () => {
  const entry = { id: 9, actor_user_id: 'me', actor_username: 'qa', action: 'qa_content.read', target_type: 'qa',
    target_id: 'q-1', detail: { qa_id: 'q-1', kinds: ['faithfulness', 'feedback'] }, created_at: null }
  expect(actionLabel(entry.action)).toBe('查看問答內容')
  expect(auditSummary(entry)).toBe('qa q-1・忠實度低分、倒讚')
})

test('研報隱藏／恢復的稽核：中文標籤＋檔名（原因全文不在 detail 裡）', () => {
  const hide = { id: 10, actor_user_id: 'me', actor_username: 'root', action: 'report.hide', target_type: 'report',
    target_id: 'a'.repeat(64), detail: { hidden: true, previous_hidden: false, has_reason: true, file_name: '台積電.pdf' },
    created_at: null }
  expect(actionLabel(hide.action)).toBe('隱藏研報')
  expect(auditSummary(hide)).toBe('台積電.pdf・已填原因')
  const restore = { ...hide, id: 11, action: 'report.restore', detail: { hidden: false, previous_hidden: true, has_reason: false } }
  expect(actionLabel(restore.action)).toBe('恢復研報')
  expect(auditSummary(restore)).toBe(`研報 ${'a'.repeat(12)}`)
})

test('調整權限的稽核：中文標籤＋授予／收回的 scope 與 super 變化', () => {
  const entry = { id: 12, actor_user_id: 'me', actor_username: 'root', action: 'user.set_privileges', target_type: 'user',
    target_id: 'u2', detail: { username: 'alice', is_super: { from: false, to: true }, scopes_added: ['ops.operate'],
      scopes_removed: ['qa_content.read'], via: 'web' }, created_at: null }
  expect(actionLabel(entry.action)).toBe('調整權限')
  expect(auditSummary(entry)).toBe('alice・設為 super admin・授予 ops.operate・收回 qa_content.read')
})
