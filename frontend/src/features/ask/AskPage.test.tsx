import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from '../../lib/readSSE'

const streamAsk = vi.fn()
const getConversation = vi.fn(async () => [] as unknown[])
const getReportHash = vi.fn(async (_id: string) => 'f'.repeat(64) as string | null)
// 這個 mock 必須涵蓋 controller 與頁面用到的**每一個** askApi 匯出：vi.mock 會整包取代模組，
// 漏掉的匯出是 undefined，呼叫它拋的 TypeError 又常被 fail-open 的 catch 吞掉——
// 測試照樣全綠，功能靜默失效。
vi.mock('../../lib/askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  getConversation: (...a: unknown[]) => getConversation(...(a as [])),
  getReportHash: (id: string) => getReportHash(id),
  getQaVersions: vi.fn(async () => []),
  stopAsk: vi.fn(async () => ({ qa_id: 'qa-stop' })),
  sendFeedback: vi.fn(async () => {}),
}))
import { stopAsk } from '../../lib/askApi'
import AskPage from './AskPage'

afterEach(() => {
  vi.clearAllMocks()
  getConversation.mockResolvedValue([])
  getReportHash.mockResolvedValue('f'.repeat(64))
})

function immediate(events: RawSSEEvent[]) {
  return (async function* () { for (const e of events) yield e })()
}
/**
 * 「還在跑」的串流：吐完事件後**不結束**。
 *
 * 用 immediate 模擬進行中的生成是錯的——串流一結束而沒有 done/error，controller 會
 * 判定連線掉了，測試看到的就是錯誤態。
 */
function live(events: RawSSEEvent[]) {
  return (async function* () {
    for (const e of events) yield e
    await new Promise(() => { /* 永不 resolve：模擬生成仍在進行 */ })
  })()
}
/** 網址探針：MemoryRouter 沒有真實 location 可讀，靠它把路徑與 query string 攤進 DOM。 */
function LocationProbe() {
  const loc = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <div data-testid="search-params">{loc.search}</div>
      <div data-testid="pathname">{loc.pathname}</div>
      <button type="button" onClick={() => navigate(`/assistant?c=${OTHER}`)}>跳到另一串</button>
    </>
  )
}
const OTHER = '123e4567-e89b-12d3-a456-426614174999'

function wrap(entry = '/assistant') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}><AskPage /><LocationProbe /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const SOURCE = { n: 1, report_id: 'r1', file_name: 'f.pdf', title: '台積電展望', market: 'TW', report_date: '2026-06-20', is_latest: false }
const answered: RawSSEEvent[] = [
  { event: 'sources', data: [SOURCE] },
  { event: 'status', data: { stage: 'generating', thinking_ms: 3000 } },
  { event: 'token', data: '這是答案 [1]' },
  { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: ['r1'] } },
]

function ask(text: string) {
  const box = screen.getByLabelText('輸入問題')
  fireEvent.change(box, { target: { value: text } })
  fireEvent.keyDown(box, { key: 'Enter' })
}
const turns = () => screen.queryAllByRole('region', { name: '一輪對話' })
const drawer = () => screen.queryByRole('complementary', { name: '引用來源' })

test('空白頁：「今天想研究什麼？」、標題列是「新對話」、沒有新對話按鈕', () => {
  wrap()
  expect(screen.getByRole('heading', { name: '今天想研究什麼？' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '新對話' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /新對話/ })).toBeNull()
})

test('送出第一題：空白頁立即消失、輸入框換到底部，答案串完出現來源卡片', async () => {
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  // 標題、建議題不等淡出，同一次 render 就卸載
  expect(screen.queryByRole('heading', { name: '今天想研究什麼？' })).toBeNull()
  expect(turns()).toHaveLength(1)
  expect(within(turns()[0]).getByText('台積電評價')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: '台積電評價' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(await screen.findByRole('button', { name: '全部 1 則來源' })).toBeInTheDocument()
  expect(screen.getByLabelText('輸入問題')).toHaveAttribute('placeholder', '繼續追問…')
})

test('「全部 N 則來源」開抽屜、關閉鈕收起（抽屜常駐 DOM，收起後對輔助技術隱藏）', async () => {
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  fireEvent.click(await screen.findByRole('button', { name: '全部 1 則來源' }))
  const panel = drawer()!
  expect(panel).toBeInTheDocument()
  expect(within(panel).getByRole('heading', { name: '台積電展望' })).toBeInTheDocument()
  fireEvent.click(within(panel).getByRole('button', { name: '關閉' }))
  await waitFor(() => expect(drawer()).toBeNull())
})

test('點引用編號：抽屜打開並標出那一則', async () => {
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  fireEvent.click(await screen.findByRole('button', { name: '1' }))
  const card = drawer()!.querySelector('article[data-n="1"]')!
  expect(card.className).toMatch(/flash/)
})

test('「在閱讀頁開啟」以 report_id 換到 file_hash，導到閱讀頁', async () => {
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  fireEvent.click(await screen.findByRole('button', { name: '全部 1 則來源' }))
  fireEvent.click(within(drawer()!).getByRole('button', { name: /在閱讀頁開啟/ }))
  await waitFor(() => expect(screen.getByTestId('pathname').textContent).toBe(`/report/${'f'.repeat(64)}`))
  expect(getReportHash).toHaveBeenCalledWith('r1')
})

test('換不到 file_hash（研報被隱藏、查無）時留在原頁並提示', async () => {
  getReportHash.mockResolvedValue(null)
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  fireEvent.click(await screen.findByRole('button', { name: '全部 1 則來源' }))
  fireEvent.click(within(drawer()!).getByRole('button', { name: /在閱讀頁開啟/ }))
  await waitFor(() => expect(getReportHash).toHaveBeenCalled())
  expect(screen.getByTestId('pathname').textContent).toBe('/assistant')
})

test('失敗後重試：同一輪原地重送，不多一輪；帶 edit_of 停用補存的那一列', async () => {
  streamAsk
    .mockReturnValueOnce(immediate([
      { event: 'status', data: { stage: 'understanding' } },
      { event: 'error', data: { detail: '問答服務發生錯誤，請稍後再試' } },
    ]))
    .mockReturnValueOnce(immediate(answered))
  wrap()
  ask('台積電評價')
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('回答沒有完成')
  expect(alert).toHaveTextContent('問答服務發生錯誤，請稍後再試')
  // 沒跑完的新提問照樣落庫（stopAsk 回 qa-stop），對話才會留在側欄
  await waitFor(() => expect(stopAsk).toHaveBeenCalledWith(expect.objectContaining({ question: '台積電評價' })))
  await waitFor(() => expect(screen.getByTestId('search-params').textContent).toBe('?c=qa-stop'))
  fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(turns()).toHaveLength(1)
  expect(streamAsk).toHaveBeenCalledTimes(2)
  const body = streamAsk.mock.calls[1][0] as Record<string, unknown>
  expect(body.question).toBe('台積電評價')
  // 停用失敗的那一列再重送：紀錄裡不留下失敗的那次
  expect(body.edit_of).toBe('qa-stop')
  expect(body.conversation_id).toBe('qa-stop')
  expect(body.regenerate_of).toBeUndefined()
})

test('失敗後重試：補存沒成功（沒有 qaId）就不帶 edit_of，當一題普通的提問', async () => {
  vi.mocked(stopAsk).mockRejectedValueOnce(new Error('stop failed: 503'))
  streamAsk
    .mockReturnValueOnce(immediate([{ event: 'error', data: { detail: '問答服務發生錯誤，請稍後再試' } }]))
    .mockReturnValueOnce(immediate(answered))
  wrap()
  ask('台積電評價')
  const alert = await screen.findByRole('alert')
  await waitFor(() => expect(stopAsk).toHaveBeenCalled())
  fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(turns()).toHaveLength(1)
  const body = streamAsk.mock.calls[1][0] as Record<string, unknown>
  expect(body.edit_of).toBeUndefined()
})

// 閱讀頁的「就這篇提問」靠 ?q= 把報告名帶過來
test('?q= 預填 composer、不自動送出，並把 q 從網址清掉', async () => {
  const q = '關於《南亞電路板 — 基板價格漲幅持續超預期.pdf》：'
  wrap(`/assistant?q=${encodeURIComponent(q)}`)
  expect(await screen.findByDisplayValue(q)).toBeInTheDocument()
  // 刻意不自動送出：讓使用者先看過、改過再按
  expect(streamAsk).not.toHaveBeenCalled()
  // q 用完即丟：留著的話，重整會拿舊題目蓋掉使用者已經編輯的內容
  // （toHaveTextContent('') 恆真，故直接比對 textContent）
  await waitFor(() => expect(screen.getByTestId('search-params').textContent).toBe(''))
})

test('重生非最後一輪時 Composer 變成停止鈕（busy 要掃全部輪次）', async () => {
  // 重新生成可以發生在任何一輪。busy 若只看最後一輪，重生中間輪時 Composer 停在
  // 送出模式——使用者無法中斷，其他輪的動作也沒被鎖住，再點一下就開出第二條串流。
  const turn = (id: string, q: string) => ({
    id, question: q, answer: `${q} 的回答`, created_at: null, feedback: null,
    sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null,
    stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false,
  })
  getConversation.mockResolvedValue([turn('qa1', '第一題'), turn('qa2', '第二題')])
  streamAsk.mockImplementation(() => live([{ event: 'status', data: { stage: 'understanding' } }]))
  wrap('/assistant?c=123e4567-e89b-12d3-a456-426614174000')
  expect(await screen.findByText('第二題')).toBeInTheDocument()

  fireEvent.click(screen.getAllByRole('button', { name: '重新生成' })[0])
  expect(await screen.findByRole('button', { name: '停止生成' })).toBeInTheDocument()
  expect(screen.getByLabelText('輸入問題')).toHaveAttribute('placeholder', '回答生成中，可先輸入下一題…')
  // 互鎖也要跟上：重生中，另一輪的重新生成鈕不可再點。
  // （第 1 輪已進 thinking、動作列收起，畫面只剩第 2 輪這一顆。）
  const remaining = screen.getAllByRole('button', { name: '重新生成' })
  expect(remaining).toHaveLength(1)
  expect(remaining[0]).toBeDisabled()
})

test('換到別的對話時收起抽屜（裡面是上一串的來源）；網址同步成自己的 c 不算換', async () => {
  streamAsk.mockReturnValue(immediate(answered))
  wrap()
  ask('台積電評價')
  fireEvent.click(await screen.findByRole('button', { name: '全部 1 則來源' }))
  expect(drawer()).toBeInTheDocument()
  // 網址的 c 被自己同步成 c1（首個 done 之後）：同一串，抽屜仍開著
  await waitFor(() => expect(screen.getByTestId('search-params').textContent).toBe('?c=c1'))
  expect(drawer()).toBeInTheDocument()
  getConversation.mockResolvedValue([{
    id: 'qa9', question: '另一串的題目', answer: '另一串的回答', created_at: null, feedback: null,
    sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null,
    stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false,
  }])
  fireEvent.click(screen.getByRole('button', { name: '跳到另一串' }))
  expect(await screen.findByText('另一串的回答')).toBeInTheDocument()
  expect(drawer()).toBeNull()
})

test('離題→Callout warning', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [] },
    { event: 'notice', data: '無法回答此問題' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ]))
  wrap()
  ask('今天天氣')
  expect(await screen.findByRole('alert')).toHaveTextContent('無法回答此問題')
})
