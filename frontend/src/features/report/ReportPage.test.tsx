import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
    market_display: '台股',
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

// jsdom 完全沒有 scrollIntoView（TextPane 因此有 typeof 守門），不 stub 就驗不到
// 「跳轉真的發生」—— 沒有這顆 stub，底下的捲動斷言會永遠是綠的。
const scrollIntoView = vi.fn()

beforeEach(() => {
  vi.resetAllMocks()
  HTMLElement.prototype.scrollIntoView = scrollIntoView
  vi.mocked(readingApi.getSimilarReports).mockResolvedValue(similar())
  vi.mocked(readingApi.getReadingText).mockResolvedValue(text())
})

afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
})

/** 手動控制 /text 何時抵達：重現「TextPane 掛載時全文還沒到」的真實時序。 */
function deferText() {
  let resolve!: (v: ReadingText) => void
  vi.mocked(readingApi.getReadingText).mockReturnValue(
    new Promise<ReadingText>(r => { resolve = r }),
  )
  return { arrive: (v: ReadingText = text()) => resolve(v) }
}

describe('ReportPage', () => {
  it('非 64-hex 的 hash → 找不到頁面，且完全不打 API', async () => {
    wrap('/report/not-a-hash')
    expect(screen.getByText('找不到這份研報')).toBeInTheDocument()
    expect(readingApi.getReadingDoc).not.toHaveBeenCalled()
    expect(readingApi.getSimilarReports).not.toHaveBeenCalled()
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
    expect(screen.getByRole('link', { name: /原始 PDF/ })).toBeInTheDocument()
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

  // text 缺席只代表「只能看 PDF」，不是錯誤：不得出現錯誤態或檢視切換
  it('text_state 為 missing → PDF-only 版面（無檢視切換、不抓全文）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ text_state: 'missing', text_chars: 0, text_sha256: null }))
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(screen.queryByRole('radiogroup', { name: '文件檢視' })).toBeNull()
    expect(screen.queryByText(/載入失敗/)).toBeNull()
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('text_state 為 missing 且帶 ?chunk → 仍退回 PDF（不會空白）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(
      doc({ text_state: 'missing', text_sha256: null }))
    wrap(`/report/${HASH}?chunk=3`)
    await waitFor(() => expect(screen.getByRole('heading', { name: /南亞電路板/ })).toBeInTheDocument())
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('預設為原文檢視，不抓全文', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('radio', { name: '原文' })).toBeChecked())
    expect(readingApi.getReadingText).not.toHaveBeenCalled()
  })

  it('?chunk=N → 預設文字檢視、抓全文時帶 chunk、顯示命中導航', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    vi.mocked(readingApi.getReadingText).mockResolvedValue(text({ chunk_start: 0, chunk_end: 2 }))
    wrap(`/report/${HASH}?chunk=4`)
    await waitFor(() => expect(screen.getByRole('radio', { name: '文字' })).toBeChecked())
    // chunk 必須一路帶到後端：命中段的 offset 由 anchor.py 算，前端不重造比對
    await waitFor(() => expect(readingApi.getReadingText).toHaveBeenCalledWith(HASH, 4))
    expect(await screen.findByRole('button', { name: '回到命中處' })).toBeInTheDocument()
    expect(screen.getByLabelText('關閉命中導航')).toBeInTheDocument()
    // 只錨得到一段命中 → 不得出現 n/N 計數與上下鍵（會讓人以為還有別的命中可翻）
    expect(screen.queryByText(/命中 1 \/ 1/)).toBeNull()
    expect(screen.queryByLabelText('上一個命中')).toBeNull()
    expect(screen.queryByLabelText('下一個命中')).toBeNull()
  })

  // 這一條是整頁的重點：從檢索命中點進來，就是要看那一段
  it('?chunk=N 且後端錨到 → 依 offset 標出命中段', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ takeaways: [] }))
    vi.mocked(readingApi.getReadingText).mockResolvedValue(text({ chunk_start: 2, chunk_end: 6 }))
    wrap(`/report/${HASH}?chunk=4`)
    // chunk_start 2 / chunk_end 6 → 「三四五六」
    await waitFor(() => expect(document.querySelector('[data-hit]')?.textContent).toBe('三四五六'))
  })

  // 錨不到（後端回 null）不是錯誤：不高亮、不給命中導航，其餘照常
  it('?chunk=N 但後端錨不到 → 無高亮、無命中導航，全文照常顯示', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ takeaways: [] }))
    vi.mocked(readingApi.getReadingText).mockResolvedValue(
      text({ chunk_start: null, chunk_end: null }))
    wrap(`/report/${HASH}?chunk=4`)
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
    expect(document.querySelector('[data-hit]')).toBeNull()
    expect(screen.queryByRole('button', { name: '回到命中處' })).toBeNull()
    expect(screen.queryByLabelText('關閉命中導航')).toBeNull()
    expect(screen.queryByText(/載入失敗/)).toBeNull()
  })

  it('關閉命中導航 → 命中列與高亮消失（檢視態不變）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ takeaways: [] }))
    vi.mocked(readingApi.getReadingText).mockResolvedValue(text({ chunk_start: 2, chunk_end: 6 }))
    wrap(`/report/${HASH}?chunk=4`)
    await waitFor(() => expect(screen.getByLabelText('關閉命中導航')).toBeInTheDocument())
    fireEvent.click(screen.getByLabelText('關閉命中導航'))
    await waitFor(() => expect(screen.queryByLabelText('關閉命中導航')).toBeNull())
    expect(document.querySelector('[data-hit]')).toBeNull()
    // 收掉命中不該把讀者正在讀的文字檢視一起帶走
    expect(screen.getByRole('radio', { name: '文字' })).toBeChecked()
  })

  it('切到文字檢視才抓全文，並依 offset 標出引文', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('radio', { name: '文字' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('radio', { name: '文字' }))
    await waitFor(() => expect(readingApi.getReadingText).toHaveBeenCalledWith(HASH, null))
    // quote_start 2 / quote_end 8 → 「三四五六七八」
    await waitFor(() => expect(document.querySelector('[data-q="q1"]')?.textContent).toBe('三四五六七八'))
  })

  it('點可跳的摘錄 → 切文字檢視並標出該段', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('重申買進評級')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /跳至第 1 條摘錄/ }))
    await waitFor(() => expect(screen.getByRole('radio', { name: '文字' })).toBeChecked())
    await waitFor(() => expect(document.querySelector('[data-q="q1"]')).not.toBeNull())
  })

  // 這一條走的是**預設檢視**（原文）：點下去時 /text 才剛開始抓，TextPane 掛載時
  // 走的是載入分支。aria-label 承諾「跳至原文位置」，第一次點就必須真的跳。
  // （曾經：deps 只有 [jump]，全文抵達後 jump 沒變 → effect 不再執行 → 第一次點
  //   不捲不 flash，要點第二次才動。只斷言 data-q 存在的測試抓不到。）
  it('從預設的原文檢視點摘錄 → 全文抵達後真的捲到該段（第一次點就要動）', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    const gate = deferText()
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByRole('radio', { name: '原文' })).toBeChecked())

    fireEvent.click(screen.getByRole('button', { name: /跳至第 1 條摘錄/ }))
    await waitFor(() => expect(screen.getByRole('radio', { name: '文字' })).toBeChecked())
    // 全文還沒到 → 沒有可捲的目標（此時捲了才是錯的）
    expect(scrollIntoView).not.toHaveBeenCalled()

    gate.arrive()
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())
    // 捲的必須是那一條摘錄的引文段，不是隨便一個元素
    expect((scrollIntoView.mock.contexts[0] as HTMLElement).dataset.q).toBe('q1')
  })

  it('已在文字檢視時點摘錄 → 立即捲到該段', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}?view=text`)
    // 不可用 getByText('一二三四五六七八九十') 等全文：buildTextSegments 會依 offset 把
    // 正典文字切成「一二」+「三四五六七八」(data-q) +「九十」三個元素，沒有任何單一
    // 元素的 textContent 等於整串，該查詢必然逾時。等的應該是引文段真的被標出來。
    await waitFor(() => expect(document.querySelector('[data-q="q1"]')).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /跳至第 1 條摘錄/ }))
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())
    expect((scrollIntoView.mock.contexts[0] as HTMLElement).dataset.q).toBe('q1')
  })

  // 命中段有等價的補救（依 hitStart 觸發），這條把它一起釘住
  it('?chunk=N 且後端錨到 → 全文抵達後自動捲到命中段', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ takeaways: [] }))
    const gate = deferText()
    wrap(`/report/${HASH}?chunk=4`)
    await waitFor(() => expect(screen.getByRole('radio', { name: '文字' })).toBeChecked())
    gate.arrive(text({ chunk_start: 2, chunk_end: 6 }))
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())
    expect((scrollIntoView.mock.contexts[0] as HTMLElement).textContent).toBe('三四五六')
  })

  // quote_start 為 null＝錨不到：條目照常顯示，但不可跳、不給箭頭 hover 態
  it('quote_start 為 null 的摘錄顯示但不可跳', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('亞洲兩家基板廠停止接 BT 訂單')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /跳至第 2 條摘錄/ })).toBeNull()
    expect(screen.getByRole('button', { name: /跳至第 1 條摘錄/ })).toBeInTheDocument()
  })

  it('全文 sha 與骨架不符 → 摘錄照常顯示但整篇不上引文標記', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    vi.mocked(readingApi.getReadingText).mockResolvedValue(text({ text_sha256: 'f'.repeat(64) }))
    wrap(`/report/${HASH}?chunk=1`)
    await waitFor(() => expect(screen.getByText('一二三四五六七八九十')).toBeInTheDocument())
    expect(document.querySelector('[data-q="q1"]')).toBeNull()
    expect(screen.getByText('重申買進評級')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /跳至第 1 條摘錄/ })).toBeNull()
  })

  // 全文 /text 失敗曾是死路（只有一行「請稍後再試」、無任何動作）：改為可重試
  it('全文載入失敗 → 顯示重試，點擊後重新抓取成功', async () => {
    const { ApiError } = await import('../../lib/api')
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    vi.mocked(readingApi.getReadingText).mockRejectedValueOnce(new ApiError(500, '壞了'))
    wrap(`/report/${HASH}?view=text`)
    await waitFor(() => expect(screen.getByText('全文載入失敗。')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    // refetch → 落回 beforeEach 的成功回應 → 引文段標出
    await waitFor(() => expect(document.querySelector('[data-q="q1"]')).not.toBeNull())
  })

  // 無原始檔時根本沒有「原文」檢視可切，提示不得叫讀者去切一個不存在的檢視
  it('無原始檔的文字檢視 → 提示不出現「切原文」死路', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc({ has_file: false, is_pdf: false }))
    wrap(`/report/${HASH}?view=text`)
    await waitFor(() => expect(document.querySelector('[data-q="q1"]')).not.toBeNull())
    expect(screen.getByText(/圖表與表格排版不會保留/)).toBeInTheDocument()
    expect(screen.queryByText(/請切「原文」/)).toBeNull()
  })

  it('相似研報：顯示「9/12 段相符」且連往閱讀頁', async () => {
    vi.mocked(readingApi.getReadingDoc).mockResolvedValue(doc())
    wrap(`/report/${HASH}`)
    await waitFor(() => expect(screen.getByText('相似研報')).toBeInTheDocument())
    expect(screen.getByText('9/12 段相符')).toBeInTheDocument()
    // 說法要對得上演算法：均勻「取樣」而非把全文「切成」N 段
    expect(screen.getByText(/沿全文均勻取樣 12 個段落比對/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /欣興/ }))
      .toHaveAttribute('href', `/report/${'c'.repeat(64)}`)
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
