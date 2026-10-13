import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../components/console'
import type { AdminUpload, AdminUploadScanner } from '../../lib/generated/adminApi'
import AdminUploadsPage from './AdminUploadsPage'
import { POLL_MS } from './useAdminUploads'
import { FakeXHR, MiB, pdfFile, scanner, upload } from './uploadTestKit'

beforeEach(() => { FakeXHR.reset(); vi.stubGlobal('XMLHttpRequest', FakeXHR) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); dismissToast() })

type Opts = { uploads?: AdminUpload[]; scanner?: AdminUploadScanner; scopes?: string[]; path?: string }

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname}{search}</output>
}

const NEXT_STATE = { publish: 'published', reject: 'rejected', unreject: 'draft', retry: 'clean' } as const

/** 假後端：清單依 state 篩選、單筆詳情、四個審核動作（會改變那一筆的狀態）；回傳 fetch mock 與可替換的資料。 */
function mount(opts: Opts = {}) {
  const db = { uploads: opts.uploads ?? [], scanner: opts.scanner ?? scanner() }
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === '/api/me') {
      return json({ id: 'me', username: 'root', role: 'admin', scopes: opts.scopes ?? ['admin', 'reports.manage'] })
    }
    if (path.startsWith('/api/admin/uploads?')) {
      const state = new URL(path, 'http://x').searchParams.get('state')
      const items = db.uploads.filter(it => !state || it.state === state)
      return json({ total: items.length, limit: 200, offset: 0, has_more: false, next_offset: null, items, scanner: db.scanner })
    }
    const m = path.match(/^\/api\/admin\/uploads\/([^/]+)(?:\/(publish|reject|unreject|retry))?$/)
    const row = m && db.uploads.find(it => it.upload_id === m[1])
    if (m && row && !m[2]) return json({ ...row, report: null })
    if (m && row && m[2] && init?.method === 'POST') {
      const kind = m[2] as keyof typeof NEXT_STATE
      const next = { ...row, state: NEXT_STATE[kind] }
      db.uploads = db.uploads.map(it => (it.upload_id === row.upload_id ? next : it))
      return json(next)
    }
    return json({ detail: 'not found', code: 'not_found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[opts.path ?? '/admin/uploads']}><AdminUploadsPage /><Where /><Toaster /></MemoryRouter>
    </QueryClientProvider>,
  )
  return { fetchMock, db }
}

type FetchMock = ReturnType<typeof mount>['fetchMock']
const uploadCalls = (fetchMock: FetchMock) =>
  fetchMock.mock.calls.map(([p]) => String(p)).filter(p => p.startsWith('/api/admin/uploads?'))
/** 清單本身的查詢（分頁籤筆數的查詢是 limit=1，不算）。 */
const listCalls = (fetchMock: FetchMock) => uploadCalls(fetchMock).filter(p => p.includes('limit=200'))
const posts = (fetchMock: FetchMock) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([p, init]) => [String(p), init?.body])

async function choose(files: File[]) {
  const input = await screen.findByLabelText('選擇要上傳的 PDF')
  fireEvent.change(input, { target: { files } })
}

async function row(name: string) {
  const cell = await within(await screen.findByRole('tabpanel')).findByRole('button', { name })
  return within(cell.closest('tr') as HTMLElement)
}

const where = () => screen.getByTestId('where')

test('沒有 reports.manage：只說明需要權限，不打上傳 API', async () => {
  const { fetchMock } = mount({ scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「研報管理」權限' })).toBeInTheDocument()
  expect(uploadCalls(fetchMock)).toHaveLength(0)
})

test('頁首：kicker、標題與一句摘要；長說明收在「說明」後面', async () => {
  mount()
  expect(await screen.findByRole('heading', { level: 1, name: '上傳研報' })).toBeInTheDocument()
  expect(screen.getByText('內容')).toBeInTheDocument()
  expect(screen.queryByText(/檔案先進隔離區掃毒/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '說明' }))
  expect(screen.getByText(/可一次選多份，會逐一上傳；只收 PDF/)).toBeInTheDocument()
  expect(screen.getByText(/發布前一般使用者看不到/)).toBeInTheDocument()
})

test('拖放區：點一下開檔案選擇，只收 PDF、可多選；拖進來的檔案進佇列', async () => {
  mount()
  const zone = await screen.findByTestId('upload-dropzone')
  expect(zone).toHaveTextContent('拖放 PDF 到這裡，或點一下選擇檔案（單檔上限 25 MB）')
  const input = screen.getByLabelText('選擇要上傳的 PDF') as HTMLInputElement
  expect(input).toHaveAttribute('accept', '.pdf,application/pdf')
  expect(input.multiple).toBe(true)
  const click = vi.spyOn(input, 'click').mockImplementation(() => {})
  fireEvent.click(zone)
  expect(click).toHaveBeenCalledTimes(1)
  fireEvent.drop(zone, { dataTransfer: { files: [pdfFile('dropped.pdf')] } })
  expect(await screen.findByRole('listitem', { name: 'dropped.pdf' })).toBeInTheDocument()
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
})

test('預檢：非 PDF 與超過 25 MB 的檔案不送出，說明原因', async () => {
  mount()
  await choose([pdfFile('memo.docx'), pdfFile('huge.pdf', { size: 26 * MiB })])
  const queue = within(await screen.findByRole('list', { name: '上傳佇列' }))
  expect(within(queue.getByRole('listitem', { name: 'memo.docx' })).getByText(/只接受 PDF/)).toBeInTheDocument()
  expect(within(queue.getByRole('listitem', { name: 'huge.pdf' })).getByText(/超過上限 25 MB/)).toBeInTheDocument()
  expect(FakeXHR.instances).toHaveLength(0)
})

test('多檔逐一上傳：一次只送一份，顯示進度，成功後才送下一份；可從佇列開那一筆的抽屜', async () => {
  const { db } = mount()
  await choose([pdfFile('one.pdf'), pdfFile('two.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  const first = FakeXHR.last()
  expect(new URL(first.url, 'http://x').searchParams.get('filename')).toBe('one.pdf')
  act(() => first.progress(30, 100))
  const bar = await screen.findByRole('progressbar', { name: 'one.pdf 上傳進度' })
  expect(bar).toHaveAttribute('aria-valuenow', '30')
  expect(within(screen.getByRole('listitem', { name: 'two.pdf' })).getByText('排隊中')).toBeInTheDocument()
  expect(screen.getByText('上傳中請留在這一頁，離開會中止還沒完成的上傳。')).toBeInTheDocument()
  expect(FakeXHR.instances).toHaveLength(1)
  const created = upload({ upload_id: 'up-1', original_name: 'one.pdf' })
  db.uploads = [created]
  act(() => first.respond(202, created))
  const done = within(await screen.findByRole('listitem', { name: 'one.pdf' }))
  expect(await done.findByText('已上傳')).toBeInTheDocument()
  expect(done.getByText(/已送進隔離區，等待掃描/)).toBeInTheDocument()
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(2))
  expect(new URL(FakeXHR.last().url, 'http://x').searchParams.get('filename')).toBe('two.pdf')
  fireEvent.click(done.getByRole('button', { name: '查看這筆上傳' }))
  expect(where()).toHaveTextContent('/admin/uploads?upload=up-1')
  expect(await screen.findByRole('dialog', { name: 'one.pdf' })).toBeInTheDocument()
})

test('清除已結束的項目：排隊與上傳中的留著', async () => {
  mount()
  await choose([pdfFile('memo.docx'), pdfFile('one.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: '清除已結束的項目' }))
  expect(screen.queryByRole('listitem', { name: 'memo.docx' })).not.toBeInTheDocument()
  expect(screen.getByRole('listitem', { name: 'one.pdf' })).toBeInTheDocument()
})

test.each([
  [503, { code: 'uploads_disabled', detail: '上傳功能尚未開放' }, '上傳功能尚未開啟'],
  [409, { code: 'upload_duplicate', existing: 'corpus', status: 'published', file_hash: 'f' }, '語料庫已有這份研報（已發布）'],
  [422, { code: 'upload_known_infected', file_hash: 'f' }, '先前已被判定含有惡意程式'],
  [429, { code: 'upload_quota_exceeded', quota: 'daily', limit: 30, used: 30 }, '每人每日上傳上限'],
  [503, { code: 'quarantine_unavailable', detail: 'x' }, '隔離區暫時無法使用'],
  [415, { code: 'upload_not_pdf', detail: '前 1024 bytes 沒有 PDF 檔頭' }, '不是有效的 PDF'],
])('上傳失敗 %i：錯誤碼轉成中文說明', async (status, body, text) => {
  mount()
  await choose([pdfFile('x.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  act(() => FakeXHR.last().respond(status, { ...body, request_id: 'r' }))
  const item = within(await screen.findByRole('listitem', { name: 'x.pdf' }))
  expect(await item.findByText('上傳失敗')).toBeInTheDocument()
  expect(item.getByRole('alert')).toHaveTextContent(text)
})

test('進行中的重複上傳：說明狀態並可開那一筆的抽屜', async () => {
  mount({ uploads: [upload({ upload_id: 'u-77', original_name: 'existing.pdf', state: 'draft' })] })
  await choose([pdfFile('dup.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  act(() => FakeXHR.last().respond(409, { code: 'upload_duplicate', existing: 'upload', status: 'draft', upload_id: 'u-77' }))
  const item = within(await screen.findByRole('listitem', { name: 'dup.pdf' }))
  expect(await item.findByText(/已在上傳流程中（待審草稿）/)).toBeInTheDocument()
  fireEvent.click(item.getByRole('button', { name: '查看這筆上傳' }))
  expect(where()).toHaveTextContent('/admin/uploads?upload=u-77')
  expect(await screen.findByRole('dialog', { name: 'existing.pdf' })).toBeInTheDocument()
})

test('413 帶回的 max_bytes 用於之後的預檢', async () => {
  mount()
  await choose([pdfFile('a.pdf', { size: 12 * MiB })])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  act(() => FakeXHR.last().respond(413, { code: 'upload_too_large', detail: 'x', max_bytes: 10 * MiB }))
  expect(await screen.findByText('檔案超過上限 10 MB。')).toBeInTheDocument()
  await choose([pdfFile('b.pdf', { size: 11 * MiB })])
  expect(await within(screen.getByRole('listitem', { name: 'b.pdf' })).findByText(/超過上限 10 MB/)).toBeInTheDocument()
  expect(FakeXHR.instances).toHaveLength(1)
  expect(screen.getByText(/單檔上限 10 MB/)).toBeInTheDocument()
})

test('取消排隊中與上傳中的檔案', async () => {
  mount()
  await choose([pdfFile('one.pdf'), pdfFile('two.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  fireEvent.click(within(screen.getByRole('listitem', { name: 'two.pdf' })).getByRole('button', { name: '取消' }))
  fireEvent.click(within(screen.getByRole('listitem', { name: 'one.pdf' })).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.getAllByText('已取消')).toHaveLength(2))
  expect(FakeXHR.instances[0].aborted).toBe(true)
  expect(FakeXHR.instances).toHaveLength(1)
})

const mixed = () => [
  upload({ original_name: 'q.pdf', state: 'quarantined' }),
  upload({ original_name: 'p.pdf', state: 'processing' }),
  upload({ original_name: 'd.pdf', state: 'draft' }),
  upload({ original_name: 'f.pdf', state: 'failed', failure_kind: 'scanned' }),
  upload({ original_name: 'v.pdf', state: 'infected', scan_signature: 'Eicar-Test-Signature' }),
  upload({ original_name: 'b.pdf', state: 'blocked' }),
]

test('分頁籤：處理中合併四種狀態；切到其他分頁籤依狀態查詢並寫進網址', async () => {
  const { fetchMock } = mount({ uploads: mixed() })
  const panel = within(await screen.findByRole('tabpanel'))
  expect(await panel.findByRole('button', { name: 'q.pdf' })).toBeInTheDocument()
  expect(panel.getByRole('button', { name: 'p.pdf' })).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: 'd.pdf' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: /處理中/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: /處理中/ })).toHaveTextContent('2')
  expect(screen.getByRole('tab', { name: /待審草稿/ })).toHaveTextContent('1')

  fireEvent.click(screen.getByRole('tab', { name: /已攔截/ }))
  expect(where()).toHaveTextContent('/admin/uploads?tab=blocked')
  const blocked = within(await screen.findByRole('tabpanel', { name: '已攔截' }))
  expect(await blocked.findByRole('button', { name: 'v.pdf' })).toBeInTheDocument()
  expect(blocked.getByText('Eicar-Test-Signature')).toBeInTheDocument()
  expect(blocked.getByRole('button', { name: 'b.pdf' })).toBeInTheDocument()
  expect(blocked.queryByRole('button', { name: 'f.pdf' })).not.toBeInTheDocument()
  const states = listCalls(fetchMock).map(p => new URL(p, 'http://x').searchParams.get('state'))
  expect(states).toEqual(expect.arrayContaining(['infected', 'blocked']))

  fireEvent.click(screen.getByRole('tab', { name: /失敗/ }))
  const failed = within(await screen.findByRole('tabpanel', { name: '失敗' }))
  expect(await failed.findByRole('button', { name: 'f.pdf' })).toBeInTheDocument()
  expect(failed.getByText('掃描影像 PDF，抽不出文字')).toBeInTheDocument()

  // 切回處理中：網址拿掉 tab 參數。
  fireEvent.click(screen.getByRole('tab', { name: /處理中/ }))
  await waitFor(() => expect(where()).toHaveTextContent(/^\/admin\/uploads$/))
})

test('分頁籤筆數：七個分頁籤都有數字（沒掛清單的另外用 limit=1 查）', async () => {
  const { fetchMock } = mount({ uploads: mixed() })
  await waitFor(() => expect(screen.getByRole('tab', { name: /已攔截/ })).toHaveTextContent('2'))
  expect(screen.getByRole('tab', { name: /失敗/ })).toHaveTextContent('1')
  expect(screen.getByRole('tab', { name: /已發布/ })).toHaveTextContent('0')
  expect(screen.getByRole('tab', { name: /已退回/ })).toHaveTextContent('0')
  expect(screen.getByRole('tab', { name: /重複/ })).toHaveTextContent('0')
  const counted = uploadCalls(fetchMock).filter(p => p.includes('limit=1&') || p.endsWith('limit=1'))
    .map(p => new URL(p, 'http://x').searchParams.get('state'))
  expect(counted.sort()).toEqual(['blocked', 'duplicate', 'failed', 'infected', 'published', 'rejected'])
})

test('上方數字：待審草稿與處理中的筆數', async () => {
  mount({ uploads: mixed() })
  const tiles = within(await screen.findByRole('group', { name: '上傳概況' }))
  const tile = (label: string) => within(tiles.getByText(label).parentElement as HTMLElement)
  await waitFor(() => expect(tile('待審草稿').getByText('1')).toBeInTheDocument())
  expect(tile('處理中').getByText('2')).toBeInTheDocument()
})

test('網址帶 ?tab=draft 時直接開在待審草稿（研報管理的草稿連過來）', async () => {
  mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ original_name: 'd.pdf', state: 'draft' })] })
  expect(await screen.findByRole('tab', { name: /待審草稿/ })).toHaveAttribute('aria-selected', 'true')
  expect(await within(screen.getByRole('tabpanel')).findByRole('button', { name: 'd.pdf' })).toBeInTheDocument()
})

test('點列開詳情抽屜並寫進網址，分頁籤參數保留；關閉後拿掉', async () => {
  mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ upload_id: 'u-d', original_name: 'd.pdf', state: 'draft' })] })
  const draft = await row('d.pdf')
  fireEvent.click(draft.getByText('root'))
  const drawer = within(await screen.findByRole('dialog', { name: 'd.pdf' }))
  expect(where()).toHaveTextContent('/admin/uploads?tab=draft&upload=u-d')
  fireEvent.click(drawer.getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(where()).toHaveTextContent(/^\/admin\/uploads\?tab=draft$/)
})

test('草稿的列動作：主要動作「審核」開抽屜；更多裡有發布與退回（退回是危險項、排最後）', async () => {
  const { fetchMock } = mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ upload_id: 'u-d', original_name: 'd.pdf', state: 'draft' })] })
  const draft = await row('d.pdf')
  fireEvent.click(draft.getByRole('button', { name: 'd.pdf 的更多操作' }))
  expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(b => b.textContent)).toEqual(['發布', '退回'])
  // 選單與列內按鈕都不會開抽屜。
  expect(where()).toHaveTextContent(/^\/admin\/uploads\?tab=draft$/)
  fireEvent.click(screen.getByRole('menuitem', { name: '發布' }))
  const confirm = within(await screen.findByRole('alertdialog', { name: '發布「d.pdf」？' }))
  expect(confirm.getByText(/發布後所有人都能在檢索、問答與閱讀頁看到這份研報/)).toBeInTheDocument()
  fireEvent.click(confirm.getByRole('button', { name: '發布' }))
  expect(await screen.findByText('已發布「d.pdf」')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-d/publish', undefined]])
  // 成功後重抓：這一筆離開待審草稿。
  expect(await screen.findByText('沒有待審的草稿')).toBeInTheDocument()
})

test('草稿的「審核」開抽屜', async () => {
  mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ upload_id: 'u-d', original_name: 'd.pdf', state: 'draft' })] })
  fireEvent.click((await row('d.pdf')).getByRole('button', { name: '審核' }))
  expect(await screen.findByRole('dialog', { name: 'd.pdf' })).toBeInTheDocument()
  expect(where()).toHaveTextContent('upload=u-d')
})

test('從列退回：原因必填，送出後跳 toast', async () => {
  const { fetchMock } = mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ upload_id: 'u-d', original_name: 'd.pdf', state: 'draft' })] })
  fireEvent.click((await row('d.pdf')).getByRole('button', { name: 'd.pdf 的更多操作' }))
  fireEvent.click(screen.getByRole('menuitem', { name: '退回' }))
  const dialog = within(await screen.findByRole('dialog', { name: '退回「d.pdf」' }))
  expect(dialog.getByRole('button', { name: '退回' })).toBeDisabled()
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: ' 不是研究報告 ' } })
  fireEvent.click(dialog.getByRole('button', { name: '退回' }))
  expect(await screen.findByText('已退回「d.pdf」')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-d/reject', JSON.stringify({ reason: '不是研究報告' })]])
})

test('失敗的列動作：可重試類主要動作是重試（要確認）；不可重試類停用並在 title 說明原因', async () => {
  const { fetchMock } = mount({
    path: '/admin/uploads?tab=failed',
    uploads: [
      upload({ upload_id: 'u-ok', original_name: 'retry.pdf', state: 'failed', failure_kind: 'ingest_error' }),
      upload({ upload_id: 'u-no', original_name: 'enc.pdf', state: 'failed', failure_kind: 'encrypted' }),
    ],
  })
  const no = await row('enc.pdf')
  expect(no.getByRole('button', { name: '重試' })).toBeDisabled()
  expect(no.getByRole('button', { name: '重試' })).toHaveAttribute('title', expect.stringContaining('「加密的 PDF」重試也不會成功'))
  fireEvent.click(no.getByRole('button', { name: 'enc.pdf 的更多操作' }))
  expect(screen.getByRole('menuitem', { name: '退回' })).toBeEnabled()
  fireEvent.keyDown(document, { key: 'Escape' })

  fireEvent.click((await row('retry.pdf')).getByRole('button', { name: '重試' }))
  fireEvent.click(within(await screen.findByRole('alertdialog', { name: '重試「retry.pdf」？' })).getByRole('button', { name: '重試' }))
  expect(await screen.findByText('已排入重試，等 worker 下一輪處理')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-ok/retry', undefined]])
})

test('已退回的列動作：寬限期內可撤銷（toast 說明回到哪個狀態）；過期停用並說明', async () => {
  const soon = new Date(Date.now() + 3 * 3600_000).toISOString()
  const { fetchMock } = mount({
    path: '/admin/uploads?tab=rejected',
    uploads: [
      upload({ upload_id: 'u-in', original_name: 'in.pdf', state: 'rejected', decision_reason: '重複', purge_after: soon }),
      upload({ upload_id: 'u-out', original_name: 'out.pdf', state: 'rejected', decision_reason: '不是研報', purge_after: '2026-01-01T00:00:00Z' }),
    ],
  })
  const out = await row('out.pdf')
  expect(out.getByText('不是研報')).toBeInTheDocument()
  expect(out.getByRole('button', { name: '撤銷退回' })).toBeDisabled()
  expect(out.getByRole('button', { name: '撤銷退回' })).toHaveAttribute('title', expect.stringContaining('寬限期已過'))
  fireEvent.click((await row('in.pdf')).getByRole('button', { name: '撤銷退回' }))
  fireEvent.click(within(await screen.findByRole('alertdialog', { name: '撤銷退回「in.pdf」？' })).getByRole('button', { name: '撤銷退回' }))
  expect(await screen.findByText('「in.pdf」回到待審草稿')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-in/unreject', undefined]])
})

test('處理中的清單沒有操作欄；隔離中掃描未完成時說明第幾次與錯誤', async () => {
  mount({ uploads: [upload({ original_name: 'q.pdf', state: 'quarantined', scan_attempts: 2, scan_last_error: 'clamd 逾時' })] })
  const q = await row('q.pdf')
  expect(q.getByText('隔離中（待掃描）')).toBeInTheDocument()
  expect(q.getByText('掃描未完成（第 2 次）：clamd 逾時')).toBeInTheDocument()
  expect(screen.queryByRole('columnheader', { name: '操作' })).not.toBeInTheDocument()
  expect(screen.getByText(/共 1 筆。清單每 5 秒自動更新，直到沒有處理中的項目。/)).toBeInTheDocument()
})

test('掃描器：有錯誤時橫幅說明暫停與檔案保留在隔離區', async () => {
  mount({ scanner: scanner({ pending: 3, oldest_pending_seconds: 7200, last_error: 'clamd 連線逾時', last_error_at: '2026-10-06T09:00:00Z' }) })
  const banner = await screen.findByRole('alert', { name: '掃描器狀態' })
  expect(banner).toHaveTextContent('掃描服務暫停，檔案保留於隔離區')
  expect(banner).toHaveTextContent('待掃描 3 件')
  expect(banner).toHaveTextContent('最舊的已等待 2 小時 0 分')
  expect(banner).toHaveTextContent('最後錯誤：clamd 連線逾時')
})

test('掃描器：沒有錯誤時不出橫幅，「掃描佇列」顯示待掃件數、掃描中件數與最舊的等待', async () => {
  mount({ scanner: scanner({ pending: 2, scanning: 1, oldest_pending_seconds: 90 }) })
  const tile = await screen.findByRole('status', { name: '掃描器狀態' })
  await waitFor(() => expect(tile).toHaveTextContent('2件待掃描'))
  expect(tile).toHaveTextContent('掃描中 1 件，最舊的已等待 1 分鐘')
  expect(screen.queryByRole('alert', { name: '掃描器狀態' })).not.toBeInTheDocument()
})

test('掃描器：沒有待掃時說明', async () => {
  mount()
  const tile = await screen.findByRole('status', { name: '掃描器狀態' })
  await waitFor(() => expect(tile).toHaveTextContent('目前沒有等待掃描的檔案'))
})

test('清單載入失敗：顯示後端訊息', async () => {
  const fetchMock = vi.fn(async (path: string) => (path === '/api/me'
    ? new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'reports.manage'] }))
    : new Response(JSON.stringify({ detail: '缺少權限：reports.manage', code: 'scope_required' }), { status: 403 })))
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={['/admin/uploads']}><AdminUploadsPage /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByRole('alert')).toHaveTextContent('上傳清單載入失敗：缺少權限：reports.manage')
})

test('輪詢：有處理中的項目才定期重抓，全部離開處理中就停', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock, db } = mount({ uploads: [upload({ original_name: 'p.pdf', state: 'scanning' })] })
  expect(await within(await screen.findByRole('tabpanel')).findByRole('button', { name: 'p.pdf' })).toBeInTheDocument()
  const before = listCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 100))
  await waitFor(() => expect(listCalls(fetchMock).length).toBeGreaterThan(before))
  // 項目進到草稿：下一輪重抓後處理中變空，之後不再輪詢
  db.uploads = db.uploads.map(it => ({ ...it, state: 'draft' as const }))
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 100))
  expect(await screen.findByText('沒有處理中的上傳')).toBeInTheDocument()
  // 處理中的項目離開：待審草稿的清單跟著重抓，分頁籤上的數字更新。
  await waitFor(() => expect(screen.getByRole('tab', { name: /待審草稿/ })).toHaveTextContent('1'))
  await act(() => vi.advanceTimersByTimeAsync(100))
  const settled = uploadCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3))
  expect(uploadCalls(fetchMock).length).toBe(settled)
})

test('沒有處理中項目時一開始就不輪詢', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock } = mount({ uploads: [upload({ state: 'published' })] })
  expect(await screen.findByText('沒有處理中的上傳')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('tab', { name: /已發布/ })).toHaveTextContent('1'))
  await act(() => vi.advanceTimersByTimeAsync(100))
  const settled = uploadCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3))
  expect(uploadCalls(fetchMock).length).toBe(settled)
})
