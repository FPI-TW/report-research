import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { AdminUpload, AdminUploadDetail, AdminUploadPreview } from '../../lib/generated/adminApi'
import AdminUploadDetailPage from './AdminUploadDetailPage'
import { upload } from './uploadTestKit'

// PDFium 的 WASM 在 jsdom 起不來：換成只顯示網址的替身，驗「把 presign 交給檢視器」這件事。
vi.mock('../report/pdf/PdfViewer', () => ({
  default: ({ url }: { url: string }) => <div data-testid="pdf-viewer">{url}</div>,
}))

afterEach(() => vi.unstubAllGlobals())

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

/** 假後端：詳情、預覽、原檔與四個審核動作；動作會改變詳情的狀態。 */
function mount(start: Partial<AdminUploadDetail>, opts: { override?: Override; preview?: Partial<AdminUploadPreview> } = {}) {
  let row: AdminUploadDetail = { ...upload({ upload_id: 'u-1', original_name: 'tsmc.pdf' }), report: null, ...start }
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const o = opts.override?.(path, init)
    const reply: Reply = o ?? (() => {
      if (path === '/api/me') return { body: { id: 'me', username: 'root', role: 'admin', scopes: ['admin', 'reports.manage'] } }
      if (path === '/api/admin/uploads/u-1' && method === 'GET') return { body: row }
      if (path === '/api/admin/uploads/u-1/preview') return { body: preview(row, opts.preview) }
      if (path === '/api/admin/uploads/u-1/file') {
        return { body: { url: 'https://r2.example/originals/x.pdf?sig=1', expires_in: 900, file_name: 'tsmc.pdf' } }
      }
      const m = path.match(/^\/api\/admin\/uploads\/u-1\/(publish|reject|unreject|retry)$/)
      if (m && method === 'POST') {
        const next = { publish: 'published', reject: 'rejected', unreject: 'draft', retry: 'clean' }[m[1]] as AdminUpload['state']
        row = { ...row, state: next, decision_reason: m[1] === 'reject' ? JSON.parse(init!.body as string).reason : row.decision_reason }
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
      <MemoryRouter initialEntries={['/admin/uploads/u-1']}>
        <Routes><Route path="/admin/uploads/:uploadId" element={<AdminUploadDetailPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetchMock
}

const posts = (fetchMock: ReturnType<typeof mount>) =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([p, init]) => [String(p), init?.body])

async function actions() {
  return within(await screen.findByRole('region', { name: '審核動作' }))
}

test('草稿：顯示發布與退回；內容、標籤、摘錄、正典文字與 PDF 預覽', async () => {
  mount({ state: 'draft', report, scan_engine: 'ClamAV 1.4.2/27420/2026-10-05', scanned_at: '2026-10-06T09:00:00Z' })
  const act = await actions()
  expect(act.getByRole('button', { name: '發布' })).toBeEnabled()
  expect(act.getByRole('button', { name: '退回' })).toBeEnabled()
  expect(act.queryByRole('button', { name: '重試' })).not.toBeInTheDocument()
  expect(screen.getByText('ClamAV 1.4.2/27420/2026-10-05')).toBeInTheDocument()
  expect(screen.getByText(/抽取品質需要複核；第 3 頁抽取失敗/)).toBeInTheDocument()
  expect(await screen.findByText('台積電 3Q26 法說會重點')).toBeInTheDocument()
  expect(screen.getByText('營收優於預期。')).toBeInTheDocument()
  expect(screen.getByText('凱基')).toBeInTheDocument()
  expect(screen.getByText('毛利率上修')).toBeInTheDocument()
  expect(screen.getByText(/展開全文（7 字）/)).toBeInTheDocument()
  expect(await screen.findByTestId('pdf-viewer')).toHaveTextContent('https://r2.example/originals/x.pdf?sig=1')
})

test('草稿的標題、摘要、摘錄還沒產出時顯示「產生中」', async () => {
  mount({ state: 'draft', report }, {
    preview: { title: null, title_state: 'pending', summary: null, summary_state: 'pending', takeaways_state: 'pending', takeaways: [] },
  })
  await waitFor(() => expect(screen.getAllByText('產生中')).toHaveLength(3))
})

test('發布：要先在確認對話框確認，取消不送出', async () => {
  const fetchMock = mount({ state: 'draft', report })
  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  const dialog = within(await screen.findByRole('dialog'))
  expect(dialog.getByText(/所有使用者立刻能在檢索、問答與閱讀頁看到/)).toBeInTheDocument()
  fireEvent.click(dialog.getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(posts(fetchMock)).toHaveLength(0)

  fireEvent.click((await actions()).getByRole('button', { name: '發布' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '發布' }))
  expect(await screen.findByText(/已發布，所有使用者現在都看得到/)).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/publish', undefined]])
  // 成功後重抓詳情：狀態變成已發布，發布鈕消失，改說明到研報管理隱藏
  const act = await actions()
  await waitFor(() => expect(act.queryByRole('button', { name: '發布' })).not.toBeInTheDocument())
  expect(act.getByText(/已發布的研報不能退回/)).toBeInTheDocument()
})

test('退回：原因必填（空白不能送出）、超過 500 字不能送出，填了才送', async () => {
  const fetchMock = mount({ state: 'draft', report })
  fireEvent.click((await actions()).getByRole('button', { name: '退回' }))
  const dialog = within(await screen.findByRole('dialog'))
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
  expect(await screen.findByText('已退回。寬限期內可以撤銷。')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/reject', JSON.stringify({ reason: '不是研究報告' })]])
})

test('退回遇到 409 upload_busy：在對話框裡顯示中文說明', async () => {
  mount({ state: 'draft', report }, {
    override: (p, init) => (p.endsWith('/reject') && init?.method === 'POST'
      ? { status: 409, body: { detail: '這份上傳正在處理中', code: 'upload_busy', state: 'processing' } } : undefined),
  })
  fireEvent.click((await actions()).getByRole('button', { name: '退回' }))
  const dialog = within(await screen.findByRole('dialog'))
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
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '發布' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('這筆上傳的狀態已經改變')
  const detailGets = () => fetchMock.mock.calls.filter(([p, i]) => p === '/api/admin/uploads/u-1' && !i?.method).length
  await waitFor(() => expect(detailGets()).toBeGreaterThanOrEqual(2))
})

test('處理中：退回停用並說明原因，沒有發布與預覽', async () => {
  const fetchMock = mount({ state: 'processing' })
  const act = await actions()
  expect(act.getByRole('button', { name: '退回' })).toBeDisabled()
  expect(act.getByText(/正在掃描或處理這份檔案，完成後才能退回/)).toBeInTheDocument()
  expect(act.queryByRole('button', { name: '發布' })).not.toBeInTheDocument()
  expect(screen.getByText(/內容與原檔預覽只在處理完成/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([p]) => String(p).endsWith('/preview') || String(p).endsWith('/file'))).toBe(false)
})

test('失敗：可重試類有重試（要確認）；不可重試類停用並說明', async () => {
  const fetchMock = mount({ state: 'failed', failure_kind: 'ingest_error', failure_detail: 'DB 逾時' })
  const act = await actions()
  expect(screen.getByText('DB 逾時')).toBeInTheDocument()
  fireEvent.click(act.getByRole('button', { name: '重試' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '重試' }))
  expect(await screen.findByText(/已排入重新處理/)).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/retry', undefined]])
})

test('失敗（不可重試）：重試停用，仍可退回', async () => {
  mount({ state: 'failed', failure_kind: 'encrypted' })
  const act = await actions()
  expect(act.getByRole('button', { name: '重試' })).toBeDisabled()
  expect(act.getByText(/「加密的 PDF」重試也不會成功/)).toBeInTheDocument()
  expect(act.getByRole('button', { name: '退回' })).toBeEnabled()
})

test('已退回：寬限期內可撤銷並顯示剩餘時間；過期則停用', async () => {
  const soon = new Date(Date.now() + (5 * 60 + 20) * 60_000).toISOString()
  const fetchMock = mount({ state: 'rejected', decision_reason: '重複', decided_by: 'root', decided_at: '2026-10-06T09:00:00Z', purge_after: soon })
  const act = await actions()
  expect(act.getByText(/寬限期還剩 5 小時 2\d 分/)).toBeInTheDocument()
  fireEvent.click(act.getByRole('button', { name: '撤銷退回' }))
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '撤銷退回' }))
  expect(await screen.findByText('已撤銷退回。')).toBeInTheDocument()
  expect(posts(fetchMock)).toEqual([['/api/admin/uploads/u-1/unreject', undefined]])
})

test('已退回且寬限期已過：撤銷停用', async () => {
  mount({ state: 'rejected', decision_reason: '重複', purge_after: '2026-01-01T00:00:00Z' })
  const act = await actions()
  expect(act.getByRole('button', { name: '撤銷退回' })).toBeDisabled()
  expect(act.getByText(/寬限期已過/)).toBeInTheDocument()
})

test('感染：顯示病毒名，沒有任何動作按鈕也不取原檔', async () => {
  const fetchMock = mount({ state: 'infected', scan_signature: 'Win.Test.EICAR_HDB-1', scan_engine: 'ClamAV 1.4.2/27420' })
  const act = await actions()
  expect(act.queryAllByRole('button')).toHaveLength(0)
  expect(screen.getAllByText('Win.Test.EICAR_HDB-1').length).toBeGreaterThan(0)
  expect(fetchMock.mock.calls.some(([p]) => String(p).endsWith('/file'))).toBe(false)
})

test('PDF 預覽：原檔端點 404（local 模式）時顯示「此環境無法預覽原檔」', async () => {
  mount({ state: 'draft', report }, {
    override: p => (p.endsWith('/file')
      ? { status: 404, body: { detail: '這份上傳的原檔目前無法提供', code: 'upload_file_unavailable' } } : undefined),
  })
  const pdf = within(await screen.findByRole('region', { name: 'PDF 預覽' }))
  expect(await pdf.findByText('此環境無法預覽原檔')).toBeInTheDocument()
  expect(pdf.getByText('這份上傳的原檔目前無法提供')).toBeInTheDocument()
  expect(screen.queryByTestId('pdf-viewer')).not.toBeInTheDocument()
})

test('找不到上傳紀錄：說明可能已被清除', async () => {
  mount({}, { override: p => (p === '/api/admin/uploads/u-1' ? { status: 404, body: { detail: 'x', code: 'not_found' } } : undefined) })
  expect(await screen.findByRole('alert')).toHaveTextContent('找不到這筆上傳紀錄')
})
