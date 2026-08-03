import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from '../../lib/readSSE'

const streamAsk = vi.fn()
const streamReportRun = vi.fn()
const getActiveReportRuns = vi.fn(async () => [] as unknown[])
const getConversation = vi.fn(async () => [] as unknown[])
// 這個 mock 必須涵蓋 controller 用到的**每一個** askApi 匯出：vi.mock 會整包取代模組，
// 漏掉的匯出是 undefined，呼叫它拋的 TypeError 又剛好會被 attachActiveRuns 的 catch
// 吞掉——測試照樣全綠，功能靜默失效。下面的重連測試就是為了讓這件事會被抓到。
vi.mock('../../lib/askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: vi.fn(),
  streamReportRun: (...a: unknown[]) => streamReportRun(...a),
  getActiveReportRuns: (...a: unknown[]) => getActiveReportRuns(...(a as [])),
  cancelReportRun: vi.fn(async () => {}),
  getConversation: (...a: unknown[]) => getConversation(...(a as [])),
  getQaVersions: vi.fn(async () => []),
  stopAsk: vi.fn(async () => ({ qa_id: 'qa-stop' })),
  sendFeedback: vi.fn(async () => {}),
  getReportTemplates: vi.fn(async () => []),
  setReportOffer: vi.fn(async () => {}),
}))
import AskPage from './AskPage'

afterEach(() => {
  vi.clearAllMocks()
  getActiveReportRuns.mockResolvedValue([])
  getConversation.mockResolvedValue([])
})

function immediate(events: RawSSEEvent[]) {
  return (async function* () { for (const e of events) yield e })()
}
/**
 * 「還在跑」的串流：吐完事件後**不結束**。
 *
 * 用 immediate 模擬進行中的生成是錯的——串流一結束而沒有 done/error，controller 會
 * 判定連線掉了並自動重連（那正是它該做的事），測試看到的就是重連耗盡後的錯誤態。
 */
function live(events: RawSSEEvent[]) {
  return (async function* () {
    for (const e of events) yield e
    await new Promise(() => { /* 永不 resolve：模擬生成仍在進行 */ })
  })()
}
/** 網址探針：MemoryRouter 沒有真實 location 可讀，靠它把 query string 攤進 DOM。 */
function LocationProbe() {
  return <div data-testid="search-params">{useLocation().search}</div>
}

function wrap(entry = '/ask') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}><AskPage /><LocationProbe /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('送出問題→串流答案顯示、資料來源鈕出現', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }] },
    { event: 'status', data: { stage: 'generating', thinking_ms: 3000 } },
    { event: 'token', data: '這是答案 [1]' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: ['r1'] } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '台積電評價' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByText('台積電評價')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(await screen.findByRole('button', { name: '資料來源 1' })).toBeInTheDocument()
})

test('資料來源鈕可切換開／關來源側欄', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }] },
    { event: 'status', data: { stage: 'generating', thinking_ms: 3000 } },
    { event: 'token', data: '這是答案 [1]' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: ['r1'] } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '台積電評價' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  const srcBtn = await screen.findByRole('button', { name: '資料來源 1' })

  // 首次點擊：開啟側欄
  fireEvent.click(srcBtn)
  expect(await screen.findByRole('complementary', { name: '引用來源' })).toBeInTheDocument()

  // 再次點擊同一鈕：關閉側欄（離場後卸載）
  fireEvent.click(srcBtn)
  await waitFor(() => expect(screen.queryByRole('complementary', { name: '引用來源' })).not.toBeInTheDocument())
})

// 閱讀頁的「就這篇提問」靠 ?q= 把報告名帶過來
test('?q= 預填 composer、不自動送出，並把 q 從網址清掉', async () => {
  const q = '關於《南亞電路板 — 基板價格漲幅持續超預期.pdf》：'
  wrap(`/ask?q=${encodeURIComponent(q)}`)
  expect(await screen.findByDisplayValue(q)).toBeInTheDocument()
  // 刻意不自動送出：讓使用者先看過、改過再按
  expect(streamAsk).not.toHaveBeenCalled()
  // q 用完即丟：留著的話，重整會拿舊題目蓋掉使用者已經編輯的內容
  // （toHaveTextContent('') 恆真，故直接比對 textContent）
  await waitFor(() => expect(screen.getByTestId('search-params').textContent).toBe(''))
})

// ── 重整/開新分頁後接回背景生成 ──────────────────────────────────────────
// 這是本功能的整個重點：生成的真相在伺服器，不在這個分頁的記憶體。載入對話時問一下
// /api/report-runs，有進行中的就把進度框接回來——重整、開新分頁、換裝置都一樣。
test('載入對話時自動接回仍在背景生成的研報', async () => {
  getConversation.mockResolvedValue([{
    id: 'qa1', question: '台積電評價', answer: '答案', created_at: null, feedback: null,
    sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
    stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false,
  }])
  getActiveReportRuns.mockResolvedValue([
    { run_id: '11111111-2222-4333-8444-555555555555', qa_id: 'qa1', question: '台積電評價', elapsed_ms: 90_000 },
  ])
  streamReportRun.mockImplementation(() => live([
    { event: 'run', data: { run_id: '11111111-2222-4333-8444-555555555555', elapsed_ms: 90_000 } },
    { event: 'outline', data: { title: 'T', sections: [
      { position: 0, section_key: 'exec_summary', heading: '執行摘要' },
      { position: 1, section_key: 'analysis', heading: '競爭格局' },
    ] } },
    { event: 'status', data: { stage: 'writing' } },
    { event: 'section_draft', data: { position: 0, heading: '執行摘要', markdown: '…' } },
  ]))

  wrap('/ask?c=123e4567-e89b-12d3-a456-426614174000')

  expect(await screen.findByText('深度研報生成中')).toBeInTheDocument()
  // 重連是拿 run_id 去接既有 run，不是重新發動一次生成
  await waitFor(() => expect(streamReportRun).toHaveBeenCalledWith(
    '11111111-2222-4333-8444-555555555555', expect.anything(),
  ))
  // elapsed_ms 回推起始時刻 → 已耗時顯示 01:30 而不是從 00:00 重數
  expect(await screen.findByText('01:30')).toBeInTheDocument()
  expect(await screen.findByText(/撰寫研報中（2\/2）/)).toBeInTheDocument()
})

test('沒有進行中的 run 就不畫進度框', async () => {
  getConversation.mockResolvedValue([{
    id: 'qa1', question: 'Q', answer: 'A', created_at: null, feedback: null,
    sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
    stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false,
  }])
  wrap('/ask?c=123e4567-e89b-12d3-a456-426614174000')
  expect(await screen.findByText('Q')).toBeInTheDocument()
  expect(screen.queryByText('深度研報生成中')).toBeNull()
  expect(streamReportRun).not.toHaveBeenCalled()
})

test('重生非最後一輪時 Composer 變成停止鈕（busy 要掃全部輪次）', async () => {
  // 重新生成可以發生在任何一輪。busy 若只看最後一輪，重生中間輪時 Composer 停在
  // 送出模式——使用者無法中斷，其他輪的動作也沒被鎖住，再點一下就開出第二條串流。
  const turn = (id: string, q: string) => ({
    id, question: q, answer: `${q} 的回答`, created_at: null, feedback: null,
    sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
    stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false,
  })
  getConversation.mockResolvedValue([turn('qa1', '第一題'), turn('qa2', '第二題')])
  streamAsk.mockImplementation(() => live([{ event: 'status', data: { stage: 'understanding' } }]))
  wrap('/ask?c=123e4567-e89b-12d3-a456-426614174000')
  expect(await screen.findByText('第二題')).toBeInTheDocument()

  fireEvent.click(screen.getAllByRole('button', { name: '重新生成' })[0])
  expect(await screen.findByRole('button', { name: '停止生成' })).toBeInTheDocument()
  // 互鎖也要跟上：重生中，另一輪的重新生成鈕不可再點。
  // （第 1 輪已進 thinking、動作列收起，畫面只剩第 2 輪這一顆。）
  const remaining = screen.getAllByRole('button', { name: '重新生成' })
  expect(remaining).toHaveLength(1)
  expect(remaining[0]).toBeDisabled()
})

test('離題→Callout warning', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [] },
    { event: 'notice', data: '無法回答此問題' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '今天天氣' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByRole('alert')).toHaveTextContent('無法回答此問題')
})
