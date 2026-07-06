import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from './readSSE'

const streamAsk = vi.fn()
const streamReport = vi.fn()
vi.mock('./askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: (...a: unknown[]) => streamReport(...a),
  getConversation: vi.fn(),
  sendFeedback: vi.fn(async () => {}),
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

test('研報生成中送出新問題：舊輪研報從 generating 還原為 offered', async () => {
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
  expect(result.current.state.turns[0].report.status).toBe('offered')

  await act(async () => { askGen2.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[1].phase).toBe('done'))
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
