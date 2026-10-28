import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, type InitialEntry } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as clipboard from '../../lib/clipboard'
import * as radarApi from '../../lib/radarApi'
import * as readingApi from '../../lib/readingApi'
import * as toast from '../../lib/toast'
import type { ReadingDoc, ReadingText, SimilarResponse } from '../../lib/readingSchemas'
import ReportPage from './ReportPage'

// 評等卡的「N 家券商共識」與目標價調幅走既有的券商共識 API；預設讓它們失敗，
// 驗卡片在派生資訊拿不到時照常顯示（fail-open）。
vi.mock('../../lib/radarApi', () => ({
  getInstrumentRadar: vi.fn(),
  getBrokerHistory: vi.fn(),
}))

vi.mock('../../lib/clipboard', () => ({ copyText: vi.fn() }))
vi.mock('../../lib/toast', () => ({ showToast: vi.fn() }))

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
// 假檢視器同時是 jump 通道的探針：記錄收到的 jump prop，並讓測試決定回報什麼結果。
// **這裡不驗引擎互動**（那在 PdfViewer.test.tsx），只驗左欄與檢視器之間的接線。
const viewer = {
  jumps: [] as { ordinal: number; quote: string; nonce: number }[],
  reply: null as { ok: boolean; page?: number; total?: number } | null,
  /** 設了就在掛載後回報 PDF 檔的載入結果（模擬「PDF 載入失敗」） */
  loadState: null as 'ready' | 'failed' | null,
  marks: null as unknown,
}
vi.mock('./pdf/PdfViewer', () => ({
  default: function FakeViewer({
    jump,
    marks,
    onJumpResult,
    onLoadState,
  }: {
    jump?: { ordinal: number; quote: string; nonce: number } | null
    marks?: unknown
    onJumpResult?: (r: { nonce: number; ok: boolean; page?: number; total?: number }) => void
    onLoadState?: (s: 'ready' | 'failed') => void
  }) {
    viewer.marks = marks
    useEffect(() => {
      if (viewer.loadState) onLoadState?.(viewer.loadState)
    }, [onLoadState])
    if (jump && viewer.jumps.at(-1)?.nonce !== jump.nonce) {
      viewer.jumps.push(jump)
      if (viewer.reply) onJumpResult?.({ nonce: jump.nonce, ...viewer.reply })
    }
    return <div data-testid="pdf-viewer" />
  },
}))

const HASH = 'a'.repeat(64)
const TEXT_SHA = 'b'.repeat(64)

function wrap(entry: InitialEntry | InitialEntry[]) {
  const entries = Array.isArray(entry) ? entry : [entry]
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <Routes>
          <Route path="/report/:hash" element={<ReportPage />} />
          <Route path="/library" element={<div>研報庫頁</div>} />
          <Route path="/consensus" element={<div>雷達頁</div>} />
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
  instrument_name: '南亞電路板',
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

/** 研報面板的頁首（市場、券商、日期、標的代號與動作列都在這裡） */
function header() {
  return within(screen.getByRole('heading', { level: 1 }).closest('header')!)
}

/** 「本篇評等」那一區 */
function ratingSection() {
  return within(screen.getByRole('heading', { name: '本篇評等' }).closest('section')!)
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(readingApi.getSimilarReports).mockResolvedValue(similar())
  vi.mocked(readingApi.getReadingText).mockResolvedValue(text())
  vi.mocked(radarApi.getInstrumentRadar).mockRejectedValue(new Error('radar down'))
  vi.mocked(radarApi.getBrokerHistory).mockRejectedValue(new Error('radar down'))
  viewer.jumps = []
  viewer.reply = null
  viewer.loadState = null
  viewer.marks = null
})

describe('ReportPage', () => {
  it('非 64-hex 的 hash → 找不到頁面，且完全不打 API', async () => {
    wrap('/report/not-a-hash')
    expect(screen.getByRole('heading', { name: '找不到這篇研報' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回到研報庫' })).toHaveAttribute('href', '/library')
    expect(readingApi.getReadingDoc).not.toHaveBeenCalled()
    expect(readingApi.getSimilarReports).not.toHaveBeenCalled()
  })

  it('返回：回到剛才所在的頁面（不是固定回研報庫）；不知道來源時寫「上一頁」', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(['/consensus', `/report/${HASH}`])
    fireEvent.click(await screen.findByRole('button', { name: '上一頁' }))
    expect(await screen.findByText('雷達頁')).toBeInTheDocument()
  })

  it('返回：來源頁帶了 from → 文字是來源頁名', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(['/consensus', { pathname: `/report/${HASH}`, state: { from: 'consensus' } }])
    fireEvent.click(await screen.findByRole('button', { name: '券商共識' }))
    expect(await screen.findByText('雷達頁')).toBeInTheDocument()
  })

  // 原型的語意是「返回來源頁」，不是一篇一篇退回上一篇研報
  it('返回：從相似研報一路點下去，一次退回來源頁', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap([
      '/consensus',
      { pathname: `/report/${'d'.repeat(64)}`, state: { from: 'consensus' } },
      { pathname: `/report/${HASH}`, state: { from: 'consensus', depth: 2 } },
    ])
    fireEvent.click(await screen.findByRole('button', { name: '券商共識' }))
    expect(await screen.findByText('雷達頁')).toBeInTheDocument()
  })

  it('返回：直接開啟研報（站內沒有上一頁）時退回研報庫，不離開本站', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    fireEvent.click(await screen.findByRole('button', { name: '研報庫' }))
    expect(await screen.findByText('研報庫頁')).toBeInTheDocument()
  })

  it('報頭標題用報告內部標題，不是檔名', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ file_name: '6247269925_260714_dw_nypcb.pdf', title: 'NYPCB：基板漲價超預期，重申買進' }))
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'NYPCB：基板漲價超預期，重申買進' })).toBeInTheDocument())
    // 檔名只留在「原始 PDF」鈕的 title，不當頁面標題
    expect(screen.queryByRole('heading', { name: /6247269925/ })).toBeNull()
    expect(screen.getByRole('link', { name: /原始 PDF/ })).toHaveAttribute('title', '6247269925_260714_dw_nypcb.pdf')
  })

  it('載入後顯示報頭：報告名、市場、券商、日期、標的代號', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    // 市場標籤在頁首與相似研報都會出現（同為台股）：斷言限縮到頁首才唯一
    expect(header().getByText('台股')).toBeInTheDocument()
    expect(header().getByText('大和')).toBeInTheDocument()
    expect(header().getByText('2026-07-14')).toBeInTheDocument()
    expect(header().getByText('8046')).toBeInTheDocument()
    // 產品 UI 規則：同列資訊靠間距區分，不用「·」
    expect(screen.queryByText('·')).toBeNull()
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
      `/assistant?q=${encodeURIComponent('關於《南亞電路板 — 基板價格漲幅持續超預期.pdf》：')}`,
    )
    expect(link).toHaveAccessibleName(/全語料/)
  })

  // report_type 原本抓了不用（死欄）：有值就顯示、空值不留空 chip
  it('報頭顯示 report_type（有值時）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ report_type: '個股報告' }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(header().getByText('個股報告')).toBeInTheDocument())
  })

  it('報告類型的英文碼轉中文（snapshot → 快照）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ report_type: 'snapshot' }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(header().getByText('快照')).toBeInTheDocument())
  })

  it('report_type 為空 → 報頭不留空 chip', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ report_type: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(header().queryByText('個股報告')).toBeNull()
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
    await waitFor(() => expect(header().getAllByText('2330')).toHaveLength(2))
    const logged = spy.mock.calls.map(c => String(c[0])).join('\n')
    expect(logged).not.toMatch(/same key|two children/)
    spy.mockRestore()
  })

  // 多數研報沒有訊號 —— 無訊號是常態不是錯誤，整區不得留下空框或骨架
  it('signals_state 為 none → 本篇評等整區不在 DOM，也不查券商共識', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ signals_state: 'none', signals: [] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重點摘錄')).toBeInTheDocument())
    expect(screen.queryByText('本篇評等')).toBeNull()
    // 摘要本身也提到目標價，所以只找評等卡的欄位標題
    expect(screen.queryByText(/^目標價/)).toBeNull()
    expect(radarApi.getInstrumentRadar).not.toHaveBeenCalled()
    expect(radarApi.getBrokerHistory).not.toHaveBeenCalled()
  })

  it('本篇評等：評等徽章、目標價（含幣別）、標的，整張連到該標的的券商共識', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('本篇評等')).toBeInTheDocument())
    const sec = ratingSection()
    expect(sec.getByText('買進')).toBeInTheDocument()
    expect(sec.getByText('目標價（TWD）')).toBeInTheDocument()
    expect(sec.getByText('2,444')).toBeInTheDocument()
    expect(sec.getByText('南亞電路板')).toBeInTheDocument()
    expect(sec.getByText('8046')).toBeInTheDocument()
    const card = sec.getByRole('link', { name: /買進/ })
    expect(card).toHaveAttribute('href', '/consensus?market=TW&code=8046&window=90')
    expect(card).toHaveAccessibleName(/前往券商共識/)
  })

  // 券商數與調幅是派生資訊：拿得到才顯示，拿不到卡片照常（fail-open）
  it('本篇評等：查得到券商共識 → 標「N 家券商共識」與本篇目標價調幅', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    vi.mocked(radarApi.getInstrumentRadar).mockResolvedValue(
      { coverage: { brokers_in_consensus: 9 } } as unknown as Awaited<ReturnType<typeof radarApi.getInstrumentRadar>>)
    vi.mocked(radarApi.getBrokerHistory).mockResolvedValue({
      // diffs[i] 對應 snapshots[i]（後端 build_broker_history 的相鄰差異）
      snapshots: [{ report_id: 'r1' }, { report_id: 'r0' }],
      diffs: [
        { changes: [{ field: 'target_price', comparable: true, pct_change: 8, direction: 'up' }] },
        { changes: [] },
      ],
    } as unknown as Awaited<ReturnType<typeof radarApi.getBrokerHistory>>)
    wrap(`/report/${HASH}`)
    const sec = await waitFor(() => {
      const s = ratingSection()
      expect(s.getByRole('link', { name: /9 家券商共識/ })).toBeInTheDocument()
      return s
    })
    expect(sec.getByRole('link', { name: /9 家券商共識/ }))
      .toHaveAttribute('href', '/consensus?market=TW&code=8046&window=90')
    expect(await sec.findByText(/上調 8%/)).toBeInTheDocument()
    expect(radarApi.getBrokerHistory).toHaveBeenCalledWith('8046', 'daiwa', 'TW', '90', expect.anything())
  })

  it('本篇評等：調幅不可比 → 不標調幅', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    vi.mocked(radarApi.getBrokerHistory).mockResolvedValue({
      snapshots: [{ report_id: 'r1' }],
      diffs: [{ changes: [{ field: 'target_price', comparable: false, pct_change: null, direction: 'incomparable' }] }],
    } as unknown as Awaited<ReturnType<typeof radarApi.getBrokerHistory>>)
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(radarApi.getBrokerHistory).toHaveBeenCalled())
    await waitFor(() => expect(ratingSection().getByText('2,444')).toBeInTheDocument())
    // 摘要裡也有「上調」，只看評等區
    expect(ratingSection().queryByText(/上調|下修/)).toBeNull()
  })

  it('本篇評等：券商共識查不到 → 卡片照常顯示，標題列只寫「券商共識」', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [SIGNAL] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(radarApi.getInstrumentRadar).toHaveBeenCalled())
    const sec = ratingSection()
    expect(sec.getByRole('link', { name: '券商共識' })).toBeInTheDocument()
    expect(sec.getByText('2,444')).toBeInTheDocument()
  })

  it('本篇評等：名稱缺值 → 只顯示代號一次', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [{ ...SIGNAL, instrument_name: null }] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('本篇評等')).toBeInTheDocument())
    expect(ratingSection().getAllByText('8046')).toHaveLength(1)
  })

  // 一份研報可能同時對多檔標的有訊號：每張卡各自標出在講誰、各自連往自己的共識
  it('本篇評等：多檔標的 → 每張卡各有自己的標的與連結，標題列不放共識連結', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({
      signals_state: 'available',
      signals: [SIGNAL, { ...SIGNAL, instrument_code: '2330', instrument_name: '台積電', rating_normalized: 'neutral' }],
    }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('本篇評等')).toBeInTheDocument())
    const sec = ratingSection()
    expect(sec.getByText('南亞電路板')).toBeInTheDocument()
    expect(sec.getByText('台積電')).toBeInTheDocument()
    expect(sec.getByRole('link', { name: /中立/ })).toHaveAttribute('href', '/consensus?market=TW&code=2330&window=90')
    expect(sec.queryByRole('link', { name: /^(\d+ 家)?券商共識$/ })).toBeNull()
  })

  // 券商共識只認 findb 的市場代碼；認不得的市場連過去只會 422
  it('本篇評等：市場不在券商共識清單內 → 卡片照常顯示但不給連結', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ signals_state: 'available', signals: [{ ...SIGNAL, market: 'JP' }] }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('本篇評等')).toBeInTheDocument())
    expect(ratingSection().getByText('買進')).toBeInTheDocument()
    expect(ratingSection().queryByRole('link')).toBeNull()
    expect(radarApi.getInstrumentRadar).not.toHaveBeenCalled()
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
    expect(screen.getByText(/需要原始版面請下載原始檔/)).toBeInTheDocument()
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
    expect(screen.queryByText('找不到原始檔')).toBeNull()
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
    await waitFor(() => expect(screen.getByRole('heading', { name: 'DOCX 文件無法內嵌預覽' })).toBeInTheDocument())
    expect(screen.queryByTestId('text-skeleton')).toBeNull()
    expect(screen.getByRole('link', { name: '下載原始檔' })).toBeInTheDocument()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('無原始檔且無全文 → 明確終態，不是永遠的載入骨架', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ has_file: false, is_pdf: false, text_state: 'missing', text_chars: 0, text_sha256: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: '找不到原始檔' })).toBeInTheDocument())
    expect(screen.queryByTestId('text-skeleton')).toBeNull()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  // 摘錄的引文沒有可跳的落點了。留著 role=button／箭頭／hover 態＝承諾一個按下去
  // 什麼也不會發生的動作，而且 console 全乾淨、不會有任何錯誤。
  it('有引文的摘錄可互動，動詞是「尋找」而非承諾跳轉', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重申買進評級')).toBeInTheDocument())
    // 逐字引文仍要顯示（那是摘錄可查證的部分）
    expect(screen.getByText('視 NYPCB 為基板族群首選')).toBeInTheDocument()
    // 可及名稱由**內容**組成（不可用 aria-label 覆蓋，那會把 claim 與引文對 AT 關掉）
    const btn = screen.getByRole('button', { name: /重申買進評級/ })
    expect(btn).toHaveAccessibleName(/視 NYPCB 為基板族群首選/)
    expect(btn).toHaveAccessibleName(/在原文中尋找這段引文/)
    // 沒有引文就沒有可搜尋的東西 —— 第 2 條的 quote 是 null，不得給互動
    expect(screen.getByText('亞洲兩家基板廠停止接 BT 訂單')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /第 2 條摘錄/ })).toBeNull()
    // 舊的承諾式措辭不得復活
    expect(screen.queryByRole('button', { name: /跳至/ })).toBeNull()
  })

  it('點摘錄 → 帶引文送進檢視器；在途重複點同一條會被忽略', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    const btn = await screen.findByRole('button', { name: /重申買進評級/ })

    fireEvent.click(btn)
    await waitFor(() => expect(viewer.jumps).toHaveLength(1))
    expect(viewer.jumps[0]).toMatchObject({ ordinal: 1, quote: '視 NYPCB 為基板族群首選' })

    // 在途（還沒回報結果）時再點同一條 → 忽略。外掛的 searchAllPages 對「query 已等於
    // 這個關鍵字」直接回快取，而搜尋一開始就把 results 清空，重按會拿到假的「找不到」。
    fireEvent.click(btn)
    await waitFor(() => expect(viewer.jumps).toHaveLength(1))
  })

  it('結果回來之後再點同一條 → 重跑，且 nonce 遞增', async () => {
    viewer.reply = { ok: true, page: 12, total: 1 }
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    const btn = await screen.findByRole('button', { name: /重申買進評級/ })

    fireEvent.click(btn)
    await waitFor(() => expect(viewer.jumps).toHaveLength(1))
    fireEvent.click(btn)
    await waitFor(() => expect(viewer.jumps).toHaveLength(2))
    expect(viewer.jumps[1].nonce).toBeGreaterThan(viewer.jumps[0].nonce)
  })

  it('找到 → 條目標第 N 頁，多重命中再標共 N 處；播報區同步說', async () => {
    viewer.reply = { ok: true, page: 12, total: 2 }
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    fireEvent.click(await screen.findByRole('button', { name: /重申買進評級/ }))
    expect(await screen.findByText('共 2 處')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: /重申買進評級/ })
    expect(within(btn).getByText('第 12 頁')).toBeInTheDocument()
    // 焦點停在按鈕上，按鈕內部的變動不會被播報 —— live region 那一份不可少
    expect(screen.getByRole('status').textContent).toContain('已在第 12 頁找到')
  })

  // 找不到是一個可呈現的結果，不是靜默失敗 —— 這條就是整個設計的守門
  it('找不到 → 明說，而不是什麼都沒發生', async () => {
    viewer.reply = { ok: false }
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    fireEvent.click(await screen.findByRole('button', { name: /重申買進評級/ }))
    // 條目上看得到，而且 live region 也會播報（兩處都要有，故用 findAllByText）
    expect(await screen.findAllByText('原文中找不到這段文字')).toHaveLength(2)
    expect(screen.getByRole('status').textContent).toBe('原文中找不到這段文字')
  })

  // 內建檢視沒有搜尋能力，摘錄必須整批退回非互動（不是逐條標灰）
  it('非 PDF 研報 → 摘錄不可互動', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ is_pdf: false }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重申買進評級')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /重申買進評級/ })).toBeNull()
  })

  // 全文 /text 失敗曾是死路（只有一行「請稍後再試」、無任何動作）：改為可重試。
  // 走得到這條的只有內嵌不了 PDF 的研報，故 fixture 必須是 docx —— 用 PDF 研報會
  // 根本不呼叫 /text，測試退化成恆真。
  it('文字後備載入失敗 → 顯示重試，點擊後重新抓取成功', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ is_pdf: false }))
    vi.mocked(readingApi.getReadingText).mockRejectedValueOnce(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: '全文載入失敗' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    // refetch → 落回 beforeEach 的成功回應
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
  })

  // 相似研報直接列出（交接包「閱讀頁」第 6 點）：市場、標題、券商、日期
  it('相似研報：直接列出，連往閱讀頁', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    const link = await screen.findByRole('link', { name: /欣興/ })
    expect(link).toHaveAttribute('href', `/report/${'c'.repeat(64)}`)
    expect(within(link).getByText('元大')).toBeInTheDocument()
    expect(within(link).getByText('2026-07-10')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /相似研報/ })).toBeNull()
  })

  it('相似研報：只抓版面要列的 3 篇', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await screen.findByRole('link', { name: /欣興/ })
    expect(readingApi.getSimilarReports).toHaveBeenCalledWith(HASH, 3)
  })

  // 點進下一篇後，返回鈕要一次退回來源頁而不是上一篇（見 backNav.ts）
  it('相似研報：點進下一篇後，返回仍是來源頁', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(['/consensus', { pathname: `/report/${HASH}`, state: { from: 'consensus' } }])
    fireEvent.click(await screen.findByRole('link', { name: /欣興/ }))
    fireEvent.click(await screen.findByRole('button', { name: '券商共識' }))
    expect(await screen.findByText('雷達頁')).toBeInTheDocument()
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
    await waitFor(() => expect(screen.getByText('本篇評等')).toBeInTheDocument())
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
    await waitFor(() => expect(screen.getByRole('heading', { name: '找不到這篇研報' })).toBeInTheDocument())
  })

  it('非 404 的載入錯誤 → 可重試', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockRejectedValue(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: '研報載入失敗' })).toBeInTheDocument())
    // 重試成功 → 回到正常頁面
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(await screen.findByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument()
  })

  // PDF 檔本身開不起來：畫布給錯誤狀態，左側摘錄整批不可點（沒有文件可搜尋）
  it('PDF 載入失敗 → 摘錄整批不可點，內容仍讀得到', async () => {
    viewer.loadState = 'failed'
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await screen.findByTestId('pdf-viewer')
    await waitFor(() => expect(screen.queryByRole('button', { name: /重申買進評級/ })).toBeNull())
    expect(screen.getByText('重申買進評級')).toBeInTheDocument()
  })

  it('PDF 載入成功 → 摘錄可點；縮圖列拿到有引文的摘錄', async () => {
    viewer.loadState = 'ready'
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    expect(await screen.findByRole('button', { name: /重申買進評級/ })).toBeInTheDocument()
    // 第 2 條沒有引文，沒有落點可找，不送進縮圖列
    expect(viewer.marks).toEqual([{ ordinal: 1, quote: '視 NYPCB 為基板族群首選' }])
  })

  // 走 lib/clipboard（區網 HTTP 下 navigator.clipboard 不存在）；分享的是乾淨的閱讀頁網址
  it('複製連結 → 綠勾與提示訊息', async () => {
    const copy = vi.mocked(clipboard.copyText).mockResolvedValue(undefined)
    const show = vi.mocked(toast.showToast)
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}?chunk=4`)
    fireEvent.click(await screen.findByRole('button', { name: '複製連結' }))
    expect(await screen.findByRole('button', { name: '已複製連結' })).toBeInTheDocument()
    expect(copy).toHaveBeenCalledWith(`${window.location.origin}/report/${HASH}`)
    expect(show).toHaveBeenCalledWith('已複製連結', { ok: true })
  })

  it('複製連結失敗 → 據實說，不顯示綠勾', async () => {
    vi.mocked(clipboard.copyText).mockRejectedValue(new Error('copy failed'))
    const show = vi.mocked(toast.showToast)
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    fireEvent.click(await screen.findByRole('button', { name: '複製連結' }))
    await waitFor(() => expect(show).toHaveBeenCalledWith('複製失敗，請手動複製網址'))
    expect(screen.queryByRole('button', { name: '已複製連結' })).toBeNull()
  })

  it('無原始檔 → 報頭不給原始 PDF 鈕', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ has_file: false, is_pdf: false }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: /原始 PDF/ })).toBeNull()
  })
})
