import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { AdminUpload, AdminUploadScanner } from '../../lib/generated/adminApi'
import AdminUploadsPage from './AdminUploadsPage'
import { POLL_MS } from './useAdminUploads'
import { FakeXHR, MiB, pdfFile, scanner, upload } from './uploadTestKit'

beforeEach(() => { FakeXHR.reset(); vi.stubGlobal('XMLHttpRequest', FakeXHR) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

type Opts = { uploads?: AdminUpload[]; scanner?: AdminUploadScanner; scopes?: string[]; path?: string }

/** 假後端：清單依 state 篩選；回傳 fetch mock 與可替換的資料。 */
function mount(opts: Opts = {}) {
  const db = { uploads: opts.uploads ?? [], scanner: opts.scanner ?? scanner() }
  const fetchMock = vi.fn(async (path: string) => {
    if (path === '/api/me') {
      return new Response(JSON.stringify({ id: 'me', username: 'root', role: 'admin', scopes: opts.scopes ?? ['admin', 'reports.manage'] }))
    }
    if (path.startsWith('/api/admin/uploads?')) {
      const state = new URL(path, 'http://x').searchParams.get('state')
      const items = db.uploads.filter(it => !state || it.state === state)
      return new Response(JSON.stringify({
        total: items.length, limit: 200, offset: 0, has_more: false, next_offset: null, items, scanner: db.scanner,
      }))
    }
    return new Response(JSON.stringify({ detail: 'not found', code: 'not_found' }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[opts.path ?? '/admin/uploads']}><AdminUploadsPage /></MemoryRouter>
    </QueryClientProvider>,
  )
  return { fetchMock, db }
}

const listCalls = (fetchMock: ReturnType<typeof mount>['fetchMock']) =>
  fetchMock.mock.calls.map(([p]) => String(p)).filter(p => p.startsWith('/api/admin/uploads?'))

async function choose(files: File[]) {
  const input = await screen.findByLabelText('選擇要上傳的 PDF')
  fireEvent.change(input, { target: { files } })
}

test('沒有 reports.manage：只說明需要權限，不打上傳 API', async () => {
  const { fetchMock } = mount({ scopes: ['admin'] })
  expect(await screen.findByRole('heading', { name: '需要「研報管理」權限' })).toBeInTheDocument()
  expect(listCalls(fetchMock)).toHaveLength(0)
})

test('預檢：非 PDF 與超過 25 MB 的檔案不送出，說明原因', async () => {
  mount()
  await choose([pdfFile('memo.docx'), pdfFile('huge.pdf', { size: 26 * MiB })])
  const queue = within(await screen.findByRole('list', { name: '上傳佇列' }))
  expect(within(queue.getByRole('listitem', { name: 'memo.docx' })).getByText(/只接受 PDF/)).toBeInTheDocument()
  expect(within(queue.getByRole('listitem', { name: 'huge.pdf' })).getByText(/超過上限 25 MB/)).toBeInTheDocument()
  expect(FakeXHR.instances).toHaveLength(0)
})

test('多檔逐一上傳：一次只送一份，顯示進度，成功後才送下一份', async () => {
  mount()
  await choose([pdfFile('one.pdf'), pdfFile('two.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  const first = FakeXHR.last()
  expect(new URL(first.url, 'http://x').searchParams.get('filename')).toBe('one.pdf')
  act(() => first.progress(30, 100))
  const bar = await screen.findByRole('progressbar', { name: 'one.pdf 上傳進度' })
  expect(bar).toHaveAttribute('aria-valuenow', '30')
  expect(within(screen.getByRole('listitem', { name: 'two.pdf' })).getByText('排隊中')).toBeInTheDocument()
  expect(FakeXHR.instances).toHaveLength(1)
  act(() => first.respond(202, upload({ upload_id: 'up-1', original_name: 'one.pdf' })))
  const done = within(await screen.findByRole('listitem', { name: 'one.pdf' }))
  expect(await done.findByText('已上傳')).toBeInTheDocument()
  expect(done.getByRole('link', { name: '查看這筆上傳' })).toHaveAttribute('href', '/admin/uploads/up-1')
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(2))
  expect(new URL(FakeXHR.last().url, 'http://x').searchParams.get('filename')).toBe('two.pdf')
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
  const row = within(await screen.findByRole('listitem', { name: 'x.pdf' }))
  expect(await row.findByText('上傳失敗')).toBeInTheDocument()
  expect(row.getByRole('alert')).toHaveTextContent(text)
})

test('進行中的重複上傳：說明狀態並連到那一筆', async () => {
  mount()
  await choose([pdfFile('dup.pdf')])
  await waitFor(() => expect(FakeXHR.instances).toHaveLength(1))
  act(() => FakeXHR.last().respond(409, { code: 'upload_duplicate', existing: 'upload', status: 'draft', upload_id: 'u-77' }))
  const row = within(await screen.findByRole('listitem', { name: 'dup.pdf' }))
  expect(await row.findByText(/已在上傳流程中（待審草稿）/)).toBeInTheDocument()
  expect(row.getByRole('link', { name: '查看這筆上傳' })).toHaveAttribute('href', '/admin/uploads/u-77')
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

test('分頁籤：處理中合併四種狀態；切到其他分頁籤依狀態查詢並寫進網址', async () => {
  const { fetchMock } = mount({
    uploads: [
      upload({ original_name: 'q.pdf', state: 'quarantined' }),
      upload({ original_name: 'p.pdf', state: 'processing' }),
      upload({ original_name: 'd.pdf', state: 'draft' }),
      upload({ original_name: 'f.pdf', state: 'failed', failure_kind: 'scanned' }),
      upload({ original_name: 'v.pdf', state: 'infected', scan_signature: 'Eicar-Test-Signature' }),
      upload({ original_name: 'b.pdf', state: 'blocked' }),
    ],
  })
  const panel = within(await screen.findByRole('tabpanel'))
  expect(await panel.findByRole('link', { name: 'q.pdf' })).toBeInTheDocument()
  expect(panel.getByRole('link', { name: 'p.pdf' })).toBeInTheDocument()
  expect(panel.queryByRole('link', { name: 'd.pdf' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: /處理中/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: /處理中/ })).toHaveTextContent('2')
  expect(screen.getByRole('tab', { name: /待審草稿/ })).toHaveTextContent('1')

  fireEvent.click(screen.getByRole('tab', { name: /已攔截/ }))
  const blocked = within(await screen.findByRole('tabpanel'))
  expect(await blocked.findByRole('link', { name: 'v.pdf' })).toHaveAttribute('href', expect.stringMatching(/^\/admin\/uploads\/u-/))
  expect(blocked.getByText('Eicar-Test-Signature')).toBeInTheDocument()
  expect(blocked.getByRole('link', { name: 'b.pdf' })).toBeInTheDocument()
  expect(blocked.queryByRole('link', { name: 'f.pdf' })).not.toBeInTheDocument()
  const states = listCalls(fetchMock).map(p => new URL(p, 'http://x').searchParams.get('state'))
  expect(states).toEqual(expect.arrayContaining(['infected', 'blocked']))

  fireEvent.click(screen.getByRole('tab', { name: /失敗/ }))
  const failed = within(await screen.findByRole('tabpanel'))
  expect(await failed.findByRole('link', { name: 'f.pdf' })).toBeInTheDocument()
  expect(failed.getByText('掃描影像 PDF，抽不出文字')).toBeInTheDocument()
})

test('網址帶 ?tab=draft 時直接開在待審草稿（研報管理的草稿徽章連過來）', async () => {
  mount({ path: '/admin/uploads?tab=draft', uploads: [upload({ original_name: 'd.pdf', state: 'draft' })] })
  expect(await screen.findByRole('tab', { name: /待審草稿/ })).toHaveAttribute('aria-selected', 'true')
  expect(await within(screen.getByRole('tabpanel')).findByRole('link', { name: 'd.pdf' })).toBeInTheDocument()
})

test('掃描器橫幅：有錯誤時說明暫停與檔案保留在隔離區', async () => {
  mount({ scanner: scanner({ pending: 3, oldest_pending_seconds: 7200, last_error: 'clamd 連線逾時', last_error_at: '2026-10-06T09:00:00Z' }) })
  const banner = await screen.findByRole('alert', { name: '掃描器狀態' })
  expect(banner).toHaveTextContent('掃描服務暫停，檔案保留於隔離區')
  expect(banner).toHaveTextContent('待掃描 3 件')
  expect(banner).toHaveTextContent('最舊的已等待 2 小時 0 分')
  expect(banner).toHaveTextContent('最後錯誤：clamd 連線逾時')
})

test('掃描器橫幅：沒有錯誤時顯示待掃件數；沒有待掃時說明', async () => {
  mount({ scanner: scanner({ pending: 2, scanning: 1, oldest_pending_seconds: 90 }) })
  const banner = await screen.findByRole('status', { name: '掃描器狀態' })
  expect(banner).toHaveTextContent('待掃描 2 件、掃描中 1 件，最舊的已等待 1 分鐘')
})

test('輪詢：有處理中的項目才定期重抓，全部離開處理中就停', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock, db } = mount({ uploads: [upload({ original_name: 'p.pdf', state: 'scanning' })] })
  expect(await within(await screen.findByRole('tabpanel')).findByRole('link', { name: 'p.pdf' })).toBeInTheDocument()
  const before = listCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 100))
  await waitFor(() => expect(listCalls(fetchMock).length).toBeGreaterThan(before))
  // 項目進到草稿：下一輪重抓後處理中變空，之後不再輪詢
  db.uploads = db.uploads.map(it => ({ ...it, state: 'draft' as const }))
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS + 100))
  expect(await screen.findByText('沒有處理中的上傳')).toBeInTheDocument()
  await act(() => vi.advanceTimersByTimeAsync(100))
  const settled = listCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3))
  expect(listCalls(fetchMock).length).toBe(settled)
})

test('沒有處理中項目時一開始就不輪詢', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const { fetchMock } = mount({ uploads: [upload({ state: 'published' })] })
  expect(await screen.findByText('沒有處理中的上傳')).toBeInTheDocument()
  await act(() => vi.advanceTimersByTimeAsync(100))
  const settled = listCalls(fetchMock).length
  await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3))
  expect(listCalls(fetchMock).length).toBe(settled)
})
