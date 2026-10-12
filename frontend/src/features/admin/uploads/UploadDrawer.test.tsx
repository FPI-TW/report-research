import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { Toaster, dismissToast } from '../../../components/console'
import type { AdminUpload, AdminUploadDetail, AdminUploadPreview } from '../../../lib/generated/adminApi'
import AdminUploadsPage from '../AdminUploadsPage'
import { scanner, upload } from '../uploadTestKit'

// PDFium 的 WASM 在 jsdom 起不來：換成只顯示網址的替身，驗「把 presign 交給檢視器」這件事。
vi.mock('../../report/pdf/PdfViewer', () => ({
  default: ({ url }: { url: string }) => <div data-testid="pdf-viewer">{url}</div>,
}))

afterEach(() => { vi.unstubAllGlobals(); dismissToast() })

type Reply = { status?: number; body: unknown }
type Override = (path: string, init?: RequestInit) => Reply | undefined

const report = {
  report_id: 'r-1', title: null, publication: 'draft' as const, hidden: false, extractor: 'pdfplumber',
  extraction_version: 'v4', quality_score: 0.82, page_count: 12, pages_failed: [3], needs_review: true,
  created_at: '2026-10-06T10:00:00Z',
}

function preview(u: AdminUpload, over: Partial<AdminUploadPreview> = {}): AdminUploadPreview {
  return {
    upload: u, report_id: 'r-1', file_name: u.original_name, publication: 'draft', hidden: false,
    title: '台積電 3Q26 法說會重點', title_original: null, title_state: 'ready',
    summary: '營收優於預期。', summary_state: 'ready',
    tags: {
      market: 'TW', is_research: true, confidence: 0.93, source: '凱基', report_date: '2026-10-01', report_type: 'company',
      language: 'zh', stock_code: '2330', company_name: '台積電', instrument_types: ['stock'], stock_targets: ['2330'],
      futures_targets: [], relates_stock: true, relates_futures: false,
    },
    text: '第一段\n第二段', text_state: 'ready', text_chars: 7, text_truncated: false, text_sha256: 'x',
    takeaways_state: 'ready', takeaways: [{ ordinal: 1, claim: '毛利率上修', quote: '毛利率 58%', quote_start: 0, quote_end: 5, anchor_method: 'exact' }],
    ...over,
  }
}

function Where() {
  const { pathname, search } = useLocation()
  return <output data-testid="where">{pathname}{search}</output>
}

const NEXT_STATE = { publish: 'published', reject: 'rejected', unreject: 'draft', retry: 'clean' } as const

/**
 * 假後端：一筆上傳 `u-1` 的詳情、預覽、原檔與四個審核動作（動作會改變狀態）；清單端點回同一筆。
 * 頁面開在 `/admin/uploads?upload=u-1`，抽屜一進來就是開的。
 */
function mount(start: Partial<AdminUploadDetail>, opts: { override?: Override; preview?: Partial<AdminUploadPreview> } = {}) {
  let row: AdminUploadDetail = { ...upload({ upload_id: 'u-1', original_name: 'tsmc.pdf' }), report: null, ...start }
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'reports.manage'] } }
      if (path.startsWith('/api/admin/uploads?')) {
        const state = new URL(path, 'http://x').searchParams.get('state')
        const items = row.state === state ? [row] : []
        return { body: { total: items.length, limit: 200, offset: 0, has_more: false, next_offset: null, items, scanner: scanner() } }
      }
      if (path === '/api/admin/uploads/u-1' && method === 'GET') return { body: row }
      if (path === '/api/admin/uploads/u-1/preview') return { body: preview(row, opts.preview) }
      if (path === '/api/admin/uploads/u-1/file') {
        return { body: { url: 'https://r2.example/originals/x.pdf?sig=1', expires_in: 900, file_name: 'tsmc.pdf' } }
      }
      const m = path.match(/^\/api\/admin\/uploads\/u-1\/(publish|reject|unreject|retry)$/)
      if (m && method === 'POST') {
        const kind = m[1] as keyof typeof NEXT_STATE
        row = { ...row, state: NEXT_STATE[kind], decision_reason: kind === 'reject' ? JSON.parse(init!.body as string).reason : row.decision_reason }
        return { body: row }
      }
      return { status: 404, body: { detail: 'not found', code: 'not_found' } }
    })()
    return new Response(JSON.stringify(reply.body), { status: reply.status ?? 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/admin/uploads?upload=u-1']}><AdminUploadsPage /><Where /><Toaster /></MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const posts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([p, init]) => [String(p), init?.body])

/** 抽屜（標題是檔名）。 */
async function drawer() {
  return within(await screen.findByRole('dialog', { name: 'tsmc.pdf' }))
}
/** 抽屜底部的審核動作列。 */
async function actions() {
  return within(await (await drawer()).findByRole('group', { name: '審核動作' }))
}
/** 抽屜裡某一個區塊。 */
async function section(title: string) {
  const heading = await (await drawer()).findByRole('heading', { name: title })
  return within(heading.closest('section') as HTMLElement)
}

test('草稿：底部是發布與退回；檔案、掃描、處理結果、標籤、內容、摘錄、正典文字與 PDF 預覽', async () => {
  mount({ state: 'draft', report, scan_engine: 'ClamAV 1.4.2/27420/2026-10-05', scanned_at: '2026-10-06T09:00:00Z' })
  const act = await actions()
  expect(act.getByRole('button', { name: '發布' })).toBeEnabled()
  expect(act.getByRole('button', { name: '退回' })).toBeEnabled()
  expect(act.queryByRole('button', { name: '重試' })).not.toBeInTheDocument()
  const d = await drawer()
  expect(d.getByText('待審草稿')).toBeInTheDocument()
  // 先等內容預覽載入：載入前後的區塊不是同一批節點。
  await d.findByText('台積電 3Q26 法說會重點')
  const file = await section('檔案資訊')
  expect(file.getByText('1.3 MB')).toBeInTheDocument()
  expect(file.getByText('root')).toBeInTheDocument()
  expect(file.getByText(/^0{50,}\d+$/)).toBeInTheDocument()
  expect((await section('掃描結果')).getByText('ClamAV 1.4.2/27420/2026-10-05')).toBeInTheDocument()
  const proc = await section('處理結果')
  expect(proc.getByText('pdfplumber v4')).toBeInTheDocument()
  expect(proc.getByText('0.82')).toBeInTheDocument()
  expect(proc.getByText('12')).toBeInTheDocument()
  expect(proc.getByText(/抽取品質需要複核；第 3 頁抽取失敗/)).toBeInTheDocument()
  const content = await section('內容')
  expect(await content.findByText('台積電 3Q26 法說會重點')).toBeInTheDocument()
  expect(content.getByText('營收優於預期。')).toBeInTheDocument()
  const tags = await section('標籤')
  expect(tags.getByText('凱基')).toBeInTheDocument()
  expect(tags.getByText('市場 TW')).toBeInTheDocument()
  expect(tags.getByText('台積電 2330')).toBeInTheDocument()
  expect(tags.getByText('0.93')).toBeInTheDocument()
  const takeaways = await section('摘錄')
  expect(takeaways.getByText('毛利率上修')).toBeInTheDocument()
  expect(takeaways.getByText('毛利率 58%')).toBeInTheDocument()
  expect((await section('正典文字')).getByText(/展開全文（7 字）/)).toBeInTheDocument()
  const pdf = await section('PDF 預覽')
  expect(await pdf.findByTestId('pdf-viewer')).toHaveTextContent('https://r2.example/originals/x.pdf?sig=1')
  expect(pdf.getByRole('link', { name: '下載' })).toHaveAttribute('download', 'tsmc.pdf')
  expect(pdf.getByRole('link', { name: '在新分頁開啟' })).toHaveAttribute('target', '_blank')
})

test('草稿的標題、摘要、摘錄還沒產出時顯示「產生中」', async () => {
  mount({ state: 'draft', report }, {
    preview: { title: null, title_state: 'pending', summary: null, summary_state: 'pending', takeaways_state: 'pending', takeaways: [] },
  })
  await drawer()
  await waitFor(() => expect(screen.getAllByText('產生中')).toHaveLength(3))
})

test('發布：要先在確認框確認，取消不送出', async () => {
  const fetchMock = mount({ state: 'draft', report })
  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  const dialog = within(await screen.findByRole('alertdialog', { name: '發布「tsmc.pdf」？' }))
  expect(dialog.getByText(/所有人都能在檢索、問答與閱讀頁看到/)).toBeInTheDocument()
  expect(dialog.getByText(/之後要下架只能到「研報管理」隱藏，不能再退回/)).toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  expect(posts(fetchMock)).toHaveLength(0)

  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '發布' }))
  expect(await screen.findByText('已發布「tsmc.pdf」')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/publish', undefined]])
  // 成功後重抓詳情：狀態變成已發布，發布鈕消失，改說明到研報管理隱藏
  const act = await actions()
  await waitFor(() => expect(act.queryByRole('button', { name: '發布' })).not.toBeInTheDocument())
  expect(act.getByText(/已發布的研報不能退回/)).toBeInTheDocument()
  expect(act.getByRole('link', { name: '前往研報管理' })).toHaveAttribute('href', '/admin/reports')
  expect(act.queryByRole('button', { name: '退回' })).not.toBeInTheDocument()
})

test('退回：原因必填（空白不能送出）、超過 500 字不能送出，填了才送', async () => {
  const fetchMock = mount({ state: 'draft', report })
  fireEvent.click((await actions()).getByRole('button', { name: '退回' }))
  const dialog = within(await screen.findByRole('dialog', { name: '退回「tsmc.pdf」' }))
  const submit = dialog.getByRole('button', { name: '退回' })
  expect(submit).toBeDisabled()
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: '   ' } })
  expect(submit).toBeDisabled()
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: '字'.repeat(501) } })
  expect(submit).toBeDisabled()
  expect(dialog.getByText(/超過上限/)).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: '  不是研究報告  ' } })
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  expect(await screen.findByText('已退回「tsmc.pdf」')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/reject', JSON.stringify({ reason: '不是研究報告' })]])
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '退回「tsmc.pdf」' })).not.toBeInTheDocument())
})

test('退回遇到 409 upload_busy：在表單裡顯示中文說明', async () => {
  mount({ state: 'draft', report }, {
    override: (p, init) => (p.endsWith('/reject') && init?.method === 'POST'
      ? { status: 409, body: { detail: '這份上傳正在處理中', code: 'upload_busy', state: 'processing' } } : undefined),
  })
  fireEvent.click((await actions()).getByRole('button', { name: '退回' }))
  const dialog = within(await screen.findByRole('dialog', { name: '退回「tsmc.pdf」' }))
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: '重複' } })
  fireEvent.click(dialog.getByRole('button', { name: '退回' }))
  expect(await dialog.findByRole('alert')).toHaveTextContent('系統正在掃描或處理這份檔案，請等處理完再退回。')
})

test('發布遇到 409 upload_state_conflict：顯示中文說明並重抓詳情', async () => {
  const fetchMock = mount({ state: 'draft', report }, {
    override: (p, init) => (p.endsWith('/publish') && init?.method === 'POST'
      ? { status: 409, body: { detail: '只有草稿可以發布', code: 'upload_state_conflict', state: 'published' } } : undefined),
  })
  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '發布' }))
  expect(await screen.findByText(/這筆上傳的狀態已經改變/)).toBeInTheDocument()
  const detailGets = () => fetchMock.mock.calls.filter(([p, i]) => p === '/api/admin/uploads/u-1' && !i?.method).length
  await waitFor(() => expect(detailGets()).toBeGreaterThanOrEqual(2))
})

test('處理中：退回停用並說明原因，沒有發布與預覽', async () => {
  const fetchMock = mount({ state: 'processing' })
  const act = await actions()
  expect(act.getByRole('button', { name: '退回' })).toBeDisabled()
  expect(act.getByRole('button', { name: '退回' })).toHaveAttribute('title', expect.stringContaining('完成後才能退回'))
  expect(act.getByText(/正在掃描或處理這份檔案，完成後才能退回/)).toBeInTheDocument()
  expect(act.queryByRole('button', { name: '發布' })).not.toBeInTheDocument()
  const d = await drawer()
  expect(d.getByText(/處理中，畫面每 5 秒自動更新/)).toBeInTheDocument()
  expect(d.getByText(/內容與原檔預覽只在處理完成/)).toBeInTheDocument()
  expect(d.getByText(/還沒有入庫的研報/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).endsWith('/preview') || String(p).endsWith('/file'))).toBe(false)
})

test('失敗：可重試類有重試（要確認）；失敗類別與細節照樣顯示', async () => {
  const fetchMock = mount({ state: 'failed', failure_kind: 'ingest_error', failure_detail: 'DB 逾時' })
  const act = await actions()
  const proc = await section('處理結果')
  expect(proc.getByText('DB 逾時')).toBeInTheDocument()
  expect(proc.getByText('入庫失敗（可重試）')).toBeInTheDocument()
  fireEvent.click(act.getByRole('button', { name: '重試' }))
  fireEvent.click(within(await screen.findByRole('alertdialog', { name: '重試「tsmc.pdf」？' })).getByRole('button', { name: '重試' }))
  expect(await screen.findByText(/已排入重試/)).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/retry', undefined]])
})

test('失敗（不可重試）：重試停用，仍可退回', async () => {
  mount({ state: 'failed', failure_kind: 'encrypted' })
  const act = await actions()
  expect(act.getByRole('button', { name: '重試' })).toBeDisabled()
  expect(act.getByText(/「加密的 PDF」重試也不會成功/)).toBeInTheDocument()
  expect(act.getByRole('button', { name: '退回' })).toBeEnabled()
})

test('已退回：寬限期內可撤銷並顯示剩餘時間；審核決策列出原因', async () => {
  const soon = new Date(Date.now() + (5 * 60 + 20) * 60_000).toISOString()
  const fetchMock = mount({ state: 'rejected', decision_reason: '重複', decided_by: 'root', decided_at: '2026-10-06T09:00:00Z', purge_after: soon })
  const act = await actions()
  expect(act.getByText(/寬限期還剩 5 小時 2\d 分/)).toBeInTheDocument()
  const decision = await section('審核決策')
  expect(decision.getByText('重複')).toBeInTheDocument()
  expect(decision.getByText('root')).toBeInTheDocument()
  fireEvent.click(act.getByRole('button', { name: '撤銷退回' }))
  const confirm = within(await screen.findByRole('alertdialog', { name: '撤銷退回「tsmc.pdf」？' }))
  expect(confirm.getByText(/回到退回前的狀態.*寬限期還剩 5 小時/)).toBeInTheDocument()
  fireEvent.click(confirm.getByRole('button', { name: '撤銷退回' }))
  expect(await screen.findByText('「tsmc.pdf」回到待審草稿')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/unreject', undefined]])
})

test('已退回且寬限期已過：撤銷停用', async () => {
  mount({ state: 'rejected', decision_reason: '重複', purge_after: '2026-01-01T00:00:00Z' })
  const act = await actions()
  expect(act.getByRole('button', { name: '撤銷退回' })).toBeDisabled()
  expect(act.getByText(/寬限期已過/)).toBeInTheDocument()
})

test('已退回且已清除：沒有撤銷按鈕，說明清除時間', async () => {
  mount({ state: 'rejected', decision_reason: '重複', purge_after: '2026-01-01T00:00:00Z', purged_at: '2026-01-02T00:00:00Z' })
  const act = await actions()
  expect(act.queryByRole('button', { name: '撤銷退回' })).not.toBeInTheDocument()
  expect(act.getByText(/^已於 2026-01-0\d \d\d:\d\d 清除，無法撤銷。$/)).toBeInTheDocument()
})

test('感染：顯示病毒名，沒有任何動作按鈕也不取原檔', async () => {
  const fetchMock = mount({ state: 'infected', scan_signature: 'Win.Test.EICAR_HDB-1', scan_engine: 'ClamAV 1.4.2/27420' })
  const act = await actions()
  expect(act.queryAllByRole('button')).toHaveLength(0)
  expect(act.getByText(/被攔截的檔案留在隔離區/)).toBeInTheDocument()
  expect((await section('掃描結果')).getByText('Win.Test.EICAR_HDB-1')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).endsWith('/file'))).toBe(false)
})

test('重複：說明不會再處理，沒有動作按鈕', async () => {
  mount({ state: 'duplicate' })
  const act = await actions()
  expect(act.queryAllByRole('button')).toHaveLength(0)
  expect(act.getByText('語料庫已有同一份檔案，這筆上傳不會再處理。')).toBeInTheDocument()
})

test('隔離中且掃描出過錯：說明檔案保留在隔離區、恢復後自動重掃', async () => {
  mount({ state: 'quarantined', scan_attempts: 2, scan_last_error: 'clamd 逾時' })
  const scan = await section('掃描結果')
  expect(scan.getByText('clamd 逾時')).toBeInTheDocument()
  expect(scan.getByText(/恢復後自動重掃，不會放行未掃描的檔案/)).toBeInTheDocument()
  expect((await actions()).getByRole('button', { name: '退回' })).toBeEnabled()
})

test('PDF 預覽：原檔端點 404（local 模式）時顯示「此環境無法預覽原檔」', async () => {
  mount({ state: 'draft', report }, {
    override: p => (p.endsWith('/file')
      ? { status: 404, body: { detail: '這份上傳的原檔目前無法提供', code: 'upload_file_unavailable' } } : undefined),
  })
  const pdf = await section('PDF 預覽')
  expect(await pdf.findByText('此環境無法預覽原檔')).toBeInTheDocument()
  expect(pdf.getByText('這份上傳的原檔目前無法提供')).toBeInTheDocument()
  expect(screen.queryByTestId('pdf-viewer')).not.toBeInTheDocument()
})

test('內容預覽載入失敗：在抽屜裡顯示錯誤，其他區塊照常', async () => {
  mount({ state: 'draft', report }, {
    override: p => (p.endsWith('/preview')
      ? { status: 409, body: { detail: 'x', code: 'upload_report_missing' } } : undefined),
  })
  const content = await section('內容')
  expect(await content.findByRole('alert')).toHaveTextContent('內容預覽載入失敗：語料裡找不到這份研報')
  expect((await section('檔案資訊')).getByText('root')).toBeInTheDocument()
})

test('找不到上傳紀錄：說明可能已被清除', async () => {
  mount({}, { override: p => (p === '/api/admin/uploads/u-1' ? { status: 404, body: { detail: 'x', code: 'not_found' } } : undefined) })
  const d = within(await screen.findByRole('dialog', { name: '上傳詳情' }))
  expect(await d.findByRole('alert')).toHaveTextContent('找不到這筆上傳紀錄')
})

test('關閉抽屜（按鈕或 Esc）：網址拿掉 upload 參數', async () => {
  mount({ state: 'draft', report })
  await drawer()
  expect(screen.getByTestId('where')).toHaveTextContent('/admin/uploads?upload=u-1')
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByTestId('where')).toHaveTextContent(/^\/admin\/uploads$/)
})

test('確認框開著時 Esc 只關確認框，抽屜留著', async () => {
  mount({ state: 'draft', report })
  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  await screen.findByRole('alertdialog')
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  expect(screen.getByRole('dialog', { name: 'tsmc.pdf' })).toBeInTheDocument()
})
