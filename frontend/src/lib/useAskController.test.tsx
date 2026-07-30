import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from './readSSE'

const streamAsk = vi.fn()
const streamReport = vi.fn()
const streamReportRun = vi.fn()
const cancelReportRun = vi.fn(async () => {})
const sendFeedback = vi.fn(async () => {})
// 必須列齊 controller 用到的每一個匯出：vi.mock 整包取代模組，漏掉的是 undefined，
// 而呼叫它拋的 TypeError 常被 fail-open 的 catch 吞掉 → 測試綠、功能死。
vi.mock('./askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: (...a: unknown[]) => streamReport(...a),
  streamReportRun: (...a: unknown[]) => streamReportRun(...a),
  getActiveReportRuns: vi.fn(async () => []),
  cancelReportRun: (...a: unknown[]) => cancelReportRun(...(a as [])),
  getConversation: vi.fn(),
  getQaVersions: vi.fn(async () => []),
  stopAsk: vi.fn(async () => ({ qa_id: 'qa-stop' })),
  sendFeedback: (...a: unknown[]) => sendFeedback(...(a as [])),
  getReportTemplates: vi.fn(async () => []),
}))
import { useAskController } from './useAskController'

afterEach(() => vi.clearAllMocks())

function withQueryClient() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { client, wrapper }
}

// 可控 async generator：每 yield 前等待外部 gate
function gated(events: RawSSEEvent[]) {
  const gates: Array<() => void> = []
  const gen = (async function* () {
    for (const ev of events) {
      await new Promise<void>(res => gates.push(res))
      yield ev
    }
  })()
  return { gen, release: () => gates.shift()?.() }
}

test('submit 串流：sources→token→done 寫入 state 並記 conversationId', async () => {
  const g = gated([
    { event: 'sources', data: [] },
    { event: 'token', data: 'Hi' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } },
  ])
  streamAsk.mockReturnValue(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  expect(result.current.state.turns[0].phase).toBe('thinking')
  await act(async () => { g.release(); await Promise.resolve() })
  await act(async () => { g.release(); await Promise.resolve() })
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  expect(result.current.state.turns[0].answer).toBe('Hi')
  expect(result.current.conversationId).toBe('c1')
})

test('latest-wins：第二次 submit 後，第一串流的後續事件被丟棄', async () => {
  const g1 = gated([{ event: 'token', data: 'A1' }, { event: 'token', data: 'A2' }])
  const g2 = gated([{ event: 'token', data: 'B1' }])
  streamAsk.mockReturnValueOnce(g1.gen).mockReturnValueOnce(g2.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q1'))
  await act(async () => { g1.release(); await Promise.resolve() }) // A1 到第一輪
  act(() => result.current.submit('Q2'))                            // 遞增 reqId、abort 舊
  await act(async () => { g1.release(); await Promise.resolve() })  // A2 應被丟棄
  await act(async () => { g2.release(); await Promise.resolve() })  // B1 寫入新輪
  const turns = result.current.state.turns
  expect(turns).toHaveLength(2)
  expect(turns[0].answer).toBe('A1')  // 舊輪停在 A1，未被 A2 汙染
  expect(turns[1].answer).toBe('B1')
})

/**
 * 生成改為背景執行後，「送出新問題」不再等於「放棄研報」。
 *
 * 舊行為（此測試的前身）是把舊輪研報打回 offered——當時是誠實的，因為斷線真的會中止
 * 生成。現在斷的只是訂閱，伺服器照跑；若還沿用舊行為，畫面會宣稱研報沒生成，
 * 但十分鐘後它其實好端端地躺在 report_doc 裡。
 */
test('研報生成中送出新問題：舊輪研報維持 generating（背景照跑）', async () => {
  const askGen = gated([
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', offer_report: true, report_title: 'T' } },
  ])
  const reportGen = gated([{ event: 'status', data: { stage: 'writing' } }, { event: 'status', data: { stage: 'writing' } }])
  const askGen2 = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa2' } }])
  streamAsk.mockReturnValueOnce(askGen.gen).mockReturnValueOnce(askGen2.gen)
  streamReport.mockReturnValueOnce(reportGen.gen)

  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q1'))
  await act(async () => { askGen.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].report.status).toBe('offered'))

  const turnId = result.current.state.turns[0].id
  act(() => result.current.generateReport(turnId, 'Q1', 'qa1'))
  await act(async () => { reportGen.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].report.status).toBe('generating'))

  act(() => result.current.submit('Q2'))
  expect(result.current.state.turns[0].report.status).toBe('generating')
  expect(cancelReportRun).not.toHaveBeenCalled()

  await act(async () => { askGen2.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[1].phase).toBe('done'))
})

test('在另一輪開始生成：舊輪真的被取消（不只是斷訂閱）', async () => {
  const askGen = gated([
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', offer_report: true, report_title: 'T' } },
  ])
  const reportGen = gated([{ event: 'run', data: { run_id: 'run-a', elapsed_ms: 0 } }])
  streamAsk.mockReturnValueOnce(askGen.gen)
  streamReport.mockReturnValueOnce(reportGen.gen).mockReturnValueOnce(gated([]).gen)

  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q1'))
  await act(async () => { askGen.release(); await Promise.resolve() })
  const turnId = result.current.state.turns[0].id
  act(() => result.current.generateReport(turnId, 'Q1', 'qa1'))
  await act(async () => { reportGen.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].report.runId).toBe('run-a'))

  // 換一輪生成 → 舊 run 沒人看了，後端 semaphore 又是序列化的，留著只會排隊產廢稿
  act(() => result.current.generateReport('other-turn', 'Q2', 'qa2'))
  expect(cancelReportRun).toHaveBeenCalledWith('run-a')
  expect(result.current.state.turns[0].report.status).toBe('offered')
})

test('cancelReport 同時斷訂閱與通知後端', async () => {
  const askGen = gated([
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', offer_report: true, report_title: 'T' } },
  ])
  const reportGen = gated([{ event: 'run', data: { run_id: 'run-b', elapsed_ms: 0 } }])
  streamAsk.mockReturnValueOnce(askGen.gen)
  streamReport.mockReturnValueOnce(reportGen.gen)

  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q1'))
  await act(async () => { askGen.release(); await Promise.resolve() })
  const turnId = result.current.state.turns[0].id
  act(() => result.current.generateReport(turnId, 'Q1', 'qa1'))
  await act(async () => { reportGen.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].report.runId).toBe('run-b'))

  act(() => result.current.cancelReport(turnId, 'run-b'))
  expect(cancelReportRun).toHaveBeenCalledWith('run-b')
  expect(result.current.state.turns[0].report.status).toBe('offered')
})

test('問答串流提早結束但未收到 done 時，turn 會標成 error', async () => {
  const g = gated([{ event: 'token', data: '半句回答' }])
  streamAsk.mockReturnValue(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })

  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })

  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('error'))
  expect(result.current.state.turns[0].answer).toBe('半句回答')
})

test('setFeedback：null 送到後端要變成 none（取消），state 同步清空', async () => {
  // null → 'none' 這層轉換只存在於 controller。後端刻意不收「可為 null 的欄位」，
  // 因為那讓「漏送欄位」與「明確取消」長得一樣；而轉換寫錯是靜默的（回饋失敗不打擾）。
  const g = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } }])
  streamAsk.mockReturnValue(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  const turnId = result.current.state.turns[0].id

  act(() => result.current.setFeedback(turnId, 'qa1', 'like'))
  expect(sendFeedback).toHaveBeenLastCalledWith('qa1', 'like')
  expect(result.current.state.turns[0].feedback).toBe('like')

  act(() => result.current.setFeedback(turnId, 'qa1', null))
  expect(sendFeedback).toHaveBeenLastCalledWith('qa1', 'none')
  expect(result.current.state.turns[0].feedback).toBeNull()
})

test('每次問答完成都會刷新 conversations 快取', async () => {
  const askGen1 = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } }])
  const askGen2 = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa2' } }])
  streamAsk.mockReturnValueOnce(askGen1.gen).mockReturnValueOnce(askGen2.gen)

  const { client, wrapper } = withQueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const { result } = renderHook(() => useAskController(), { wrapper })

  act(() => result.current.submit('Q1'))
  await act(async () => { askGen1.release(); await Promise.resolve() })
  await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(1))

  act(() => result.current.submit('Q2'))
  await act(async () => { askGen2.release(); await Promise.resolve() })
  await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2))

  expect(invalidate).toHaveBeenNthCalledWith(1, { queryKey: ['conversations'] })
  expect(invalidate).toHaveBeenNthCalledWith(2, { queryKey: ['conversations'] })
})
