import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReadingDoc } from '../../lib/readingSchemas'
import { PdfPane } from './PdfPane'

/**
 * PdfPane 的分支表，重點在**降級**那一條。
 *
 * 引擎在 jsdom 永遠起不來（沒有 WebAssembly 資產、fetch 也拿不到），所以真檢視器在
 * 這裡只會把 PDFium 整包拖進模組圖卻驗不到任何東西。改以 stub 取代，並讓個別案例
 * 指定它成功或炸掉 —— 「炸掉」正是本檔存在的理由：ViewerBoundary 必須把讀者接回
 * 瀏覽器內建檢視。先前沒有任何斷言碰過那條路，它只是「剛好會發生」。
 */
let engineFails = false

vi.mock('./pdf/PdfViewer', () => ({
  default: () => {
    if (engineFails) throw new Error('WASM 載入失敗')
    return <div data-testid="pdf-viewer" />
  },
}))

const TITLE = '聯電 — 成熟製程價格落底'

function doc(partial?: Partial<ReadingDoc>): ReadingDoc {
  return {
    report_id: 'r1',
    file_hash: 'a'.repeat(64),
    // 檔名是券商流水號，正是 displayTitle 要擋掉的那種字串
    file_name: '624726992507895929_260728_gs_umt.pdf',
    title: TITLE,
    market: 'TW',
    source: 'gs',
    source_display: '高盛',
    report_date: '2026-07-28',
    report_type: '個股',
    summary: null,
    instrument_types: [],
    stock_targets: [],
    futures_targets: [],
    has_file: true,
    is_pdf: true,
    text_state: 'ok',
    text_chars: 0,
    text_sha256: null,
    takeaways: [],
    signals_state: 'none',
    signals: [],
    ...partial,
  }
}

beforeEach(() => {
  engineFails = false
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('PDF 且引擎正常 → 掛自訂檢視器，不掛內建 iframe', async () => {
  render(<PdfPane doc={doc()} />)

  expect(await screen.findByTestId('pdf-viewer')).toBeInTheDocument()
  expect(screen.queryByTitle(TITLE)).toBeNull()
})

it('引擎失敗 → 退回內建 iframe（含逃生口），並留下可觀測的痕跡', async () => {
  engineFails = true
  // React 自己也會把邊界捕捉的錯誤印出來；靜音是為了讀得到測試輸出，
  // 但**不是**放過它 —— 下面仍斷言我們自己那行診斷確實發出。
  const err = vi.spyOn(console, 'error').mockImplementation(() => {})

  render(<PdfPane doc={doc()} />)

  // iframe 的無障礙名稱走 displayTitle（顯示標題，不是流水號檔名）
  expect(await screen.findByTitle(TITLE)).toBeInTheDocument()
  expect(screen.queryByTestId('pdf-viewer')).toBeNull()

  // iOS／多數 Android 不在 iframe 內渲染 PDF 且不觸發 onError，
  // 沒有這兩條逃生口，降級後的空白框就是死路
  expect(screen.getByRole('link', { name: '在新分頁開啟' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '下載' })).toBeInTheDocument()

  // 降級對讀者是靜默的，少了這行就永遠查不出「為什麼大家的檢視器長得不一樣」
  expect(err.mock.calls.some(call => String(call[0]).includes('[pdf] 引擎不可用'))).toBe(true)
})

it('非 PDF → 給可下載的替代說明，兩種檢視都不掛', () => {
  render(<PdfPane doc={doc({ is_pdf: false, file_name: '產業展望.docx' })} />)

  expect(screen.getByText('DOCX 文件無法內嵌預覽，請下載查看。')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '下載原始檔' })).toBeInTheDocument()
  expect(screen.queryByTestId('pdf-viewer')).toBeNull()
  expect(screen.queryByTitle(TITLE)).toBeNull()
})

it('無原始檔 → 只說找不到，不給指向不存在檔案的連結', () => {
  render(<PdfPane doc={doc({ has_file: false, is_pdf: false })} />)

  expect(screen.getByText('找不到原始檔。')).toBeInTheDocument()
  expect(screen.queryByRole('link')).toBeNull()
})
