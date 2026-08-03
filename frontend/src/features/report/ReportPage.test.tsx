import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as readingApi from '../../lib/readingApi'
import type { ReadingDoc, ReadingText, SimilarResponse } from '../../lib/readingSchemas'
import ReportPage from './ReportPage'

vi.mock('../../lib/readingApi', async importOriginal => {
  const actual = await importOriginal<typeof readingApi>()
  return {
    ...actual,
    getReadingDoc: vi.fn(),
    getReadingText: vi.fn(),
    getSimilarReports: vi.fn(),
  }
})

// 閱讀頁只有一種文件檢視，所以這份檔案除了 docx／無檔的案例之外，每一個都會掛上
// PdfPane → lazy(PdfViewer)。
// 真檢視器會把 PDFium/WASM 整包拉進模組圖，而 jsdom 既載不到 WASM 也驗不到引擎行為
// —— 純粹是每個案例多背一份引擎。實測那份負載足以把並行跑的 App.test.tsx 推過
// vitest 5s 預設 testTimeout（單跑則過）。
// **這不是把降級路徑消音**：引擎失敗 → 退回內建 iframe 改由 PdfPane.test.tsx 直接斷言，
// 而非依賴這裡「WASM 剛好載不到」的副作用（那條路徑先前從未被任何斷言碰過）。
vi.mock('./pdf/PdfViewer', () => ({ default: () => <div data-testid="pdf-viewer" /> }))

const HASH = 'a'.repeat(64)
const TEXT_SHA = 'b'.repeat(64)

function wrap(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/report/:hash" element={<ReportPage />} />
          <Route path="/search" element={<div>檢索頁</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function doc(partial?: Partial<ReadingDoc>): ReadingDoc {
  return {
    report_id: 'r1',
    file_hash: HASH,
    file_name: '南亞電路板 — 基板價格漲幅持續超預期.pdf',
    market: 'TW',
    source: 'daiwa',
    source_display: '大和',
    report_date: '2026-07-14',
    report_type: '個股',
    summary: '大和證券上調目標價至 2,444 元。',
    instrument_types: ['equity'],
    stock_targets: ['8046'],
    futures_targets: [],
    has_file: true,
    is_pdf: true,
    text_state: 'ok',
    text_chars: 1200,
    text_sha256: TEXT_SHA,
    takeaways: [
      { ordinal: 1, claim: '重申買進評級', quote: '視 NYPCB 為基板族群首選', quote_start: 2, quote_end: 8, anchor_method: 'exact' },
      { ordinal: 2, claim: '亞洲兩家基板廠停止接 BT 訂單', quote: null, quote_start: null, quote_end: null, anchor_method: null },
    ],
    signals_state: 'none',
    signals: [],
    ...partial,
  }
}

function text(partial?: Partial<ReadingText>): ReadingText {
  return {
    file_hash: HASH,
    text: '一二三四五六七八九十',
    text_sha256: TEXT_SHA,
    text_chars: 10,
    truncated: false,
    ...partial,
  }
}

function similar(partial?: Partial<SimilarResponse>): SimilarResponse {
  return {
    file_hash: HASH,
    items: [{
      file_hash: 'c'.repeat(64),
      file_name: '欣興 — ABF 產能轉換下的 BT 缺口受益者.pdf',
      market: 'TW', source: 'yuanta', source_display: '元大',
      report_date: '2026-07-10', summary: null,
      matched_probes: 9, total_probes: 12,
    }],
    ...partial,
  }
}

const SIGNAL: ReadingDoc['signals'][number] = {
  instrument_code: '8046',
  market: 'TW',
  broker: 'daiwa',
  broker_display: '大和',
  rating_raw: 'Buy (1)',
  rating_normalized: 'buy',
  target_price: 2444,
  target_currency: 'TWD',
  target_horizon: '12 個月',
  eps_estimates: [],
  thesis: [
    { key: 'outlook', stance: 'positive', summary: '毛利率自 2027 年起擴張至歷史高點。', evidence: null },
    { key: 'risk', stance: 'stable', summary: '終端需求不如預期為主要下行風險。', evidence: null },
  ],
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(readingApi.getSimilarReports).mockResolvedValue(similar())
  vi.mocked(readingApi.getReadingText).mockResolvedValue(text())
})

describe('ReportPage', () => {
  it('非 64-hex 的 hash → 找不到頁面，且完全不打 API', async () => {
    wrap('/report/not-a-hash')
    expect(screen.getByText('找不到這份研報')).toBeInTheDocument()
    expect(readingApi.getReadingDoc).not.toHaveBeenCalled()
    expect(readingApi.getSimilarReports).not.toHaveBeenCalled()
  })

  it('報頭標題用報告內部標題，不是檔名', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ file_name: '6247269925_260714_dw_nypcb.pdf', title: 'NYPCB：基板漲價超預期，重申買進' }))
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'NYPCB：基板漲價超預期，重申買進' })).toBeInTheDocument())
    // 檔名只留在文件列（等寬小字），不再當頁面標題
    expect(screen.queryByRole('heading', { name: /6247269925/ })).toBeNull()
  })

  it('載入後顯示報頭：報告名、市場、券商、日期、標的代號', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    // 市場標籤在報頭與相似卡片都會出現（同為台股）：報頭斷言限縮到 banner 區才唯一
    expect(within(screen.getByRole('banner')).getByText('台股')).toBeInTheDocument()
    expect(screen.getByText('大和')).toBeInTheDocument()
    expect(screen.getByText('2026-07-14')).toBeInTheDocument()
    expect(screen.getByText('8046')).toBeInTheDocument()
    // PDF 由後端以 inline 提供 → 開新分頁直接看得到，不該強制下載
    const pdfLink = screen.getByRole('link', { name: /原始 PDF/ })
    expect(pdfLink).toHaveAttribute('target', '_blank')
    expect(pdfLink).not.toHaveAttribute('download')
    expect(screen.getByRole('link', { name: /就這篇提問/ })).toBeInTheDocument()
  })

  // 這顆鈕只把報告名帶去問答頁預填，不會縮限檢索範圍 —— 名稱得說實話
  it('「就這篇提問」帶報告名到問答頁預填，且據實標示檢索仍為全語料', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    const link = await screen.findByRole('link', { name: /就這篇提問/ })
    expect(link).toHaveAttribute(
      'href',
      `/ask?q=${encodeURIComponent('關於《南亞電路板 — 基板價格漲幅持續超預期.pdf》：')}`,
    )
    expect(link).toHaveAccessibleName(/全語料/)
  })

  // report_type 原本抓了不用（死欄）：有值就顯示、空值不留空 chip
  it('報頭顯示 report_type（有值時）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ report_type: '個股報告' }))
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(within(screen.getByRole('banner')).getByText('個股報告')).toBeInTheDocument())
  })

  it('report_type 為空 → 報頭不留空 chip', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ report_type: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(within(screen.getByRole('banner')).queryByText('個股報告')).toBeNull()
  })

  it('報頭把券商粗體', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('大和')).toBeInTheDocument())
    expect(screen.getByText('大和').tagName).toBe('B')
  })

  // 缺券商時 metaParts 讓日期落在 index 0，舊碼 i===0 會把日期粗體當券商名
  it('報頭缺券商時不把日期誤當券商名粗體', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ source: null, source_display: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('2026-07-14')).toBeInTheDocument())
    expect(screen.getByText('2026-07-14').closest('b')).toBeNull()
  })

  // 同代號同時是 stock 與 futures 標的時，併陣列後 key 會撞號 → 補 index 才不觸發警告
  it('同代號同時是 stock/futures 標的不觸發重複 key 警告', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ stock_targets: ['2330'], futures_targets: ['2330'] }))
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(within(screen.getByRole('banner')).getAllByText('2330')).toHaveLength(2))
    const logged = spy.mock.calls.map(c => String(c[0])).join('\n')
    expect(logged).not.toMatch(/same key|two children/)
    spy.mockRestore()
  })

  // 全語料僅 0.68% 有訊號 —— 無訊號是常態不是錯誤，整區不得留下空框或骨架
  it('signals_state 為 none → 觀點區完全不在 DOM', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ signals_state: 'none', signals: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重點摘錄')).toBeInTheDocument())
    expect(screen.queryByText('觀點')).toBeNull()
    expect(screen.queryByText('評等')).toBeNull()
    expect(screen.queryByText('目標價')).toBeNull()
    expect(screen.queryByText(/全語料僅 0.68% 有/)).toBeNull()
  })

  it('signals_state 為 available → 渲染評等/目標價與四維論點', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('觀點')).toBeInTheDocument())
    expect(screen.getByText('評等')).toBeInTheDocument()
    expect(screen.getByText('買進')).toBeInTheDocument()
    expect(screen.getByText('Buy (1)')).toBeInTheDocument()
    expect(screen.getByText('2,444')).toBeInTheDocument()
    expect(screen.getByText('TWD · 12 個月')).toBeInTheDocument()
    expect(screen.getByText('展望')).toBeInTheDocument()
    // 單篇是靜態立場，不是跨報告變化：用「正面」而非雷達的「轉強」
    expect(screen.getByText('正面')).toBeInTheDocument()
    expect(screen.getByText('風險')).toBeInTheDocument()
    expect(screen.getByText('持平')).toBeInTheDocument()
  })

  it('takeaways 為空 → 重點摘錄整區不渲染', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ takeaways: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('摘要')).toBeInTheDocument())
    expect(screen.queryByText('重點摘錄')).toBeNull()
  })

  // 文件檢視只有一種，由資料現實決定：能內嵌 PDF 就掛檢視器，否則落到文字後備。
  // 以下四條把「讀者拿不到任何檢視選項」釘死 —— 少了它們，日後把切換鈕加回來卻沒有
  // 落點也不會有任何訊號。
  it('PDF 研報 → 掛 PDF 檢視器，完全不抓全文', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    expect(await screen.findByTestId('pdf-viewer')).toBeInTheDocument()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('頁面不存在任何文件檢視切換控制（反向釘死）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.queryByRole('radiogroup')).toBeNull()
    expect(screen.queryByRole('radio', { name: '原文' })).toBeNull()
    expect(screen.queryByRole('radio', { name: '文字' })).toBeNull()
  })

  // text_state 曾經會左右版面（missing → 不給切換鈕）。現在它只決定「要不要抓全文」，
  // 對 PDF 研報一律無感 —— 這條擋的是「有人又把 text_state 接回版面判斷」。
  it('text_state 為 missing 的 PDF 研報 → 版面不變、不抓全文、無錯誤態', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ text_state: 'missing', text_chars: 0, text_sha256: null }))
    wrap(`/report/${HASH}`)
    expect(await screen.findByTestId('pdf-viewer')).toBeInTheDocument()
    expect(screen.queryByText(/載入失敗/)).toBeNull()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  // 已分享出去的舊網址仍帶著這兩個參數（四個檢索元件曾經每一筆都產生 ?chunk=N）。
  // 它們現在沒有消費端，必須是「完全無作用」而不是「讓頁面走進別的分支」。
  it('網址殘留 ?chunk／?view=text → 完全無作用', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}?chunk=4&view=text`)
    expect(await screen.findByTestId('pdf-viewer')).toBeInTheDocument()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '回到命中處' })).toBeNull()
  })

  // 34 篇 .docx（2026-08-03 全語料實測）內嵌不了，站內只剩這條路徑可讀。
  it('非 PDF（docx）→ 落到文字後備，不掛 PDF 檢視器', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ file_name: '南亞電路板-華南Memo20250331.docx', is_pdf: false }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
    expect(screen.queryByTestId('pdf-viewer')).toBeNull()
    expect(readingApi.getReadingText).toHaveBeenCalledWith(HASH)
    // 有原始檔可下載時才指路，且指的是真的存在的動作（頁首那顆下載鈕）
    expect(screen.getByText(/需要原始版面請由頁首下載原始檔/)).toBeInTheDocument()
    // 那顆鈕在 .docx 上不得標成「原始 PDF」，否則與上面這句指路對不上；
    // 且不得開新分頁 —— 後端對非 PDF 回 attachment，target="_blank" 只會留下空白分頁。
    const dl = screen.getByRole('link', { name: /原始檔/ })
    expect(screen.queryByRole('link', { name: /原始 PDF/ })).toBeNull()
    expect(dl).toHaveAttribute('download')
    expect(dl).not.toHaveAttribute('target')
  })

  it('無原始檔 → 落到文字後備，不出現「找不到原始檔」死路', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ has_file: false, is_pdf: false }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
    expect(screen.queryByText('找不到原始檔。')).toBeNull()
    // 無檔時不得指路去下載一個不存在的東西，也不得叫讀者去切一個不存在的檢視
    expect(screen.getByText(/圖表與表格排版不會保留/)).toBeInTheDocument()
    expect(screen.queryByText(/下載原始檔/)).toBeNull()
    expect(screen.queryByText(/請切「原文」/)).toBeNull()
  })

  // pdfViewable 的 has_file 那一半：只看 is_pdf 的話這條會紅。
  // （現實語料上缺檔為 0，但 has_file 是 request-time 的 os.path.isfile，
  //   repo 一搬家就整批命中。）
  it('is_pdf 為真但檔案不存在 → 仍落到文字後備，不掛 PDF 檢視器', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ has_file: false, is_pdf: true }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
    expect(screen.queryByTestId('pdf-viewer')).toBeNull()
  })

  // 第三態：內嵌不了**又**沒有全文。查詢被停用（enabled=false）時 react-query 的
  // isLoading 是 false 而 data 是 undefined，若讓它落進 TextPane 的 `!text` 分支，
  // 畫面會永遠停在骨架 —— 無錯誤、無重試、連請求都不發，唯一的訊號是使用者抱怨。
  it('非 PDF 且無全文 → 給可下載的終態，不是永遠的載入骨架', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ file_name: 'x.docx', is_pdf: false, text_state: 'missing', text_chars: 0, text_sha256: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText(/無法內嵌預覽，請下載查看/)).toBeInTheDocument())
    expect(screen.queryByTestId('text-skeleton')).toBeNull()
    expect(screen.getByRole('link', { name: '下載原始檔' })).toBeInTheDocument()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('無原始檔且無全文 → 明確終態，不是永遠的載入骨架', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ has_file: false, is_pdf: false, text_state: 'missing', text_chars: 0, text_sha256: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('找不到原始檔。')).toBeInTheDocument())
    expect(screen.queryByTestId('text-skeleton')).toBeNull()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  // 摘錄的引文沒有可跳的落點了。留著 role=button／箭頭／hover 態＝承諾一個按下去
  // 什麼也不會發生的動作，而且 console 全乾淨、不會有任何錯誤。
  it('重點摘錄一律以非互動元素呈現（無跳轉承諾）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重申買進評級')).toBeInTheDocument())
    // 逐字引文仍要顯示（那是摘錄可查證的部分）
    expect(screen.getByText('視 NYPCB 為基板族群首選')).toBeInTheDocument()
    expect(screen.getByText('亞洲兩家基板廠停止接 BT 訂單')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /跳至第 1 條摘錄/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /跳至/ })).toBeNull()
    expect(document.querySelector('[data-jumpable]')).toBeNull()
  })

  // 全文 /text 失敗曾是死路（只有一行「請稍後再試」、無任何動作）：改為可重試。
  // 走得到這條的只有內嵌不了 PDF 的研報，故 fixture 必須是 docx —— 用 PDF 研報會
  // 根本不呼叫 /text，測試退化成恆真。
  it('文字後備載入失敗 → 顯示重試，點擊後重新抓取成功', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ is_pdf: false }))
    vi.mocked(readingApi.getReadingText).mockRejectedValueOnce(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('全文載入失敗。')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    // refetch → 落回 beforeEach 的成功回應
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
  })

  // 相似研報改為卡片頁腳的收合列：預設只有一行（標題＋篇數），展開才掛清單。
  // 收合時刻意整段不進 DOM —— 清單列是連結，只用 CSS 藏起來仍會進 Tab 序。
  it('相似研報：預設收合，展開後顯示「9/12 段相符」且連往閱讀頁', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('相似研報')).toBeInTheDocument())
    expect(screen.queryByText('9/12 段相符')).toBeNull()
    expect(screen.queryByRole('link', { name: /欣興/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /相似研報/ }))

    expect(screen.getByText('9/12 段相符')).toBeInTheDocument()
    // 說法要對得上演算法：均勻「取樣」而非把全文「切成」N 段
    expect(screen.getByText(/沿全文均勻取樣 12 個段落比對/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /欣興/ }))
      .toHaveAttribute('href', `/report/${'c'.repeat(64)}`)
  })

  it('相似研報：收合列回報 aria-expanded，可再點收回', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    const row = await screen.findByRole('button', { name: /相似研報/ })
    expect(row).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('9/12 段相符')).toBeNull()
  })

  it('相似研報為空 → 整區不渲染', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    vi.mocked(readingApi.getSimilarReports).mockResolvedValue(similar({ items: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.queryByText('相似研報')).toBeNull()
  })

  // 相似研報 500 曾整區憑空消失、讀者無從得知：改為顯示區塊＋可重試
  it('相似研報載入失敗 → 顯示失敗與重試（不靜默消失）', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    vi.mocked(readingApi.getSimilarReports).mockRejectedValue(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('相似研報載入失敗。')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })

  // 免責曾在雷達改版中從兩處無聲消失、審查才揪出來；這幾條把它釘死在頁底。
  it('頁底免責恆存在（完整態）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('觀點')).toBeInTheDocument())
    expect(screen.getByText(/不構成任何投資建議或要約/)).toBeInTheDocument()
  })

  it('頁底免責恆存在（無訊號態 —— 99.3% 的常態）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ signals_state: 'none', signals: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('摘要')).toBeInTheDocument())
    expect(screen.getByText(/不構成任何投資建議或要約/)).toBeInTheDocument()
  })

  it('頁底免責恆存在（無摘錄、無相似、無全文的最貧乏態）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ takeaways: [], summary: null, text_state: 'missing', text_sha256: null }))
    vi.mocked(readingApi.getSimilarReports).mockResolvedValue(similar({ items: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.getByText(/不構成任何投資建議或要約/)).toBeInTheDocument()
  })

  it('404 → 找不到頁面', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockRejectedValue(new ApiError(404, '查無此研報'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('找不到這份研報')).toBeInTheDocument())
  })

  it('非 404 的載入錯誤 → 可重試', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockRejectedValue(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('載入研報時發生問題')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重試' })).toBeInTheDocument()
  })

  it('無原始檔 → 報頭不給原始 PDF 鈕', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ has_file: false, is_pdf: false }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: /原始 PDF/ })).toBeNull()
  })
})
