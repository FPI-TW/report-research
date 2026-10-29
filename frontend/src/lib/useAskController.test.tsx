import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from './readSSE'

const streamAsk = vi.fn()
const sendFeedback = vi.fn(async () => {})
// 必須列齊 controller 用到的每一個匯出：vi.mock 整包取代模組，漏掉的是 undefined，
// 而呼叫它拋的 TypeError 常被 fail-open 的 catch 吞掉 → 測試綠、功能死。
vi.mock('./askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  getConversation: vi.fn(),
  getQaVersions: vi.fn(async () => []),
  stopAsk: vi.fn(async () => ({ qa_id: 'qa-stop' })),
  sendFeedback: (...a: unknown[]) => sendFeedback(...(a as [])),
}))
import { ApiError } from './api'
import { getConversation, stopAsk } from './askApi'
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

test('載入對話 404（不存在或不是自己的）→ 改當新對話，下一題不帶那個 conversation_id', async () => {
  vi.mocked(getConversation).mockRejectedValueOnce(new ApiError(404, 'HTTP 404'))
  const g = gated([{ event: 'done', data: { conversation_id: 'c-new', qa_id: 'qa1' } }])
  streamAsk.mockReturnValueOnce(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })

  await act(async () => { await result.current.loadConversation('someone-elses') })
  expect(result.current.conversationId).toBeNull()

  act(() => result.current.submit('新題'))
  expect(streamAsk.mock.calls[0][0]).not.toHaveProperty('conversation_id')
  await act(async () => { g.release(); await Promise.resolve() })
})

test('載入對話的其他失敗（網路、500）維持現況', async () => {
  vi.mocked(getConversation).mockRejectedValueOnce(new ApiError(500, 'HTTP 500'))
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  await act(async () => { await result.current.loadConversation('c1') })
  expect(result.current.conversationId).toBe('c1')
})

// ── 新對話送出當下就進側欄「最近」（pendingConversation.ts）────────────────────────
// 暫存列（清單，新的在前）；測試一次只有一輪在開新對話，取第一筆。空清單＝null。
const pendingOf = (client: QueryClient) =>
  (client.getQueryData<{ key: string; title: string; conversationId: string | null }[]>(['pendingConversations']) ?? [])[0] ?? null

test('新對話：送出當下就記下暫存列（標題＝問題），done 之後清單重抓完就清掉', async () => {
  const g = gated([
    { event: 'token', data: 'Hi' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } },
  ])
  streamAsk.mockReturnValue(g.gen)
  const { client, wrapper } = withQueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('半導體庫存何時見底'))
  expect(pendingOf(client)).toEqual({ key: result.current.state.turns[0].id, title: '半導體庫存何時見底', conversationId: null })
  await act(async () => { g.release(); await Promise.resolve() })
  expect(pendingOf(client)).not.toBeNull()  // 還在跑：暫存列留著
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['conversations'] })
  await waitFor(() => expect(pendingOf(client)).toBeNull())
})

// ── 回答沒跑完的新提問照樣落庫（/api/ask/stop），對話留在側欄「最近」────────────────────

test('新提問沒收到 done 就結束：補送停止請求落庫，這一串接到那一列、畫面仍是失敗', async () => {
  const g = gated([{ event: 'token', data: '半句' }])
  streamAsk.mockReturnValue(g.gen)
  const { client, wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  const turnId = result.current.state.turns[0].id
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('error'))
  await waitFor(() => expect(stopAsk).toHaveBeenCalledTimes(1))
  expect(stopAsk).toHaveBeenCalledWith(expect.objectContaining({ question: 'Q', partial_answer: '半句', conversation_id: null }))
  // 接到停止列：續問與重生都留在同一串，重生帶 regenerate_of 不會多出一題
  await waitFor(() => expect(result.current.conversationId).toBe('qa-stop'))
  expect(result.current.state.turns[0].qaId).toBe('qa-stop')
  expect(result.current.state.turns[0].phase).toBe('error')
  await waitFor(() => expect(pendingOf(client)).toBeNull())  // 清單重抓完才清（重抓到的是真的那一列）
  void turnId
})

test('排隊已滿（429）的新提問同樣落庫', async () => {
  // eslint-disable-next-line require-yield -- 429 在串流開始前就拋（後端連 SSE 都沒開）
  streamAsk.mockImplementation(() => (async function* () {
    throw new ApiError(429, '問答排隊人數已滿，請稍後再試')
  })())
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await waitFor(() => expect(result.current.state.turns[0].errorText).toBe('問答排隊人數已滿，請稍後再試'))
  await waitFor(() => expect(stopAsk).toHaveBeenCalledTimes(1))
})

test('補存失敗：fail-open，畫面維持失敗、暫存列清掉', async () => {
  vi.mocked(stopAsk).mockRejectedValueOnce(new Error('stop failed: 503'))
  const g = gated([{ event: 'token', data: '半句' }])
  streamAsk.mockReturnValue(g.gen)
  const { client, wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(stopAsk).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(pendingOf(client)).toBeNull())
  expect(result.current.conversationId).toBeNull()
  expect(result.current.state.turns[0].qaId).toBeNull()
})

test('收到 done 的一輪不補存', async () => {
  const g = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } }])
  streamAsk.mockReturnValue(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  expect(stopAsk).not.toHaveBeenCalled()
})

test('重生失敗不補存：畫面退回舊版，補一個空的停止版會蓋掉它', async () => {
  const g1 = gated([{ event: 'token', data: '舊答案' }, { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } }])
  const g2 = gated([{ event: 'error', data: { detail: 'boom' } }])
  streamAsk.mockReturnValueOnce(g1.gen).mockReturnValueOnce(g2.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g1.release(); await Promise.resolve() })
  await act(async () => { g1.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  const turnId = result.current.state.turns[0].id
  act(() => result.current.regenerate(turnId, 'qa1', 'Q'))
  await act(async () => { g2.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).not.toBe('thinking'))
  expect(stopAsk).not.toHaveBeenCalled()
})

test('重整或關頁（pagehide）時還沒跑完的新提問：以 keepalive 送出停止請求', async () => {
  const g = gated([{ event: 'token', data: '半句' }, { event: 'token', data: '沒送到' }])
  streamAsk.mockReturnValue(g.gen)
  const { wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].answer).toBe('半句'))
  window.dispatchEvent(new Event('pagehide'))
  expect(stopAsk).toHaveBeenCalledWith(expect.objectContaining({ question: 'Q', partial_answer: '半句' }), { keepalive: true })
})

test('既有對話裡的續問不建暫存列', async () => {
  const g1 = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } }])
  const g2 = gated([{ event: 'done', data: { conversation_id: 'c1', qa_id: 'qa2' } }])
  streamAsk.mockReturnValueOnce(g1.gen).mockReturnValueOnce(g2.gen)
  const { client, wrapper } = withQueryClient()
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q1'))
  await act(async () => { g1.release(); await Promise.resolve() })
  await waitFor(() => expect(pendingOf(client)).toBeNull())
  act(() => result.current.submit('Q2'))
  expect(pendingOf(client)).toBeNull()
})

test('新對話跑到一半被切走（開新對話）：照樣落庫，側欄那一列接到停止列；畫面已是新對話，不受影響', async () => {
  const g = gated([{ event: 'token', data: 'A' }, { event: 'token', data: 'B' }])
  streamAsk.mockReturnValue(g.gen)
  const { client, wrapper } = withQueryClient()
  const resolved: unknown[] = []
  client.getQueryCache().subscribe(e => {
    if (e.query.queryKey[0] === 'pendingConversations') resolved.push(...(e.query.state.data as unknown[] ?? []))
  })
  const { result } = renderHook(() => useAskController(), { wrapper })
  act(() => result.current.submit('Q'))
  await act(async () => { g.release(); await Promise.resolve() })
  expect(pendingOf(client)).not.toBeNull()
  act(() => result.current.newConversation())
  await waitFor(() => expect(stopAsk).toHaveBeenCalledWith(expect.objectContaining({ question: 'Q', partial_answer: 'A', conversation_id: null })))
  // 暫存列先改連到停止列（qa_id 就是這一串的 id），清單重抓完才清掉
  await waitFor(() => expect(resolved).toContainEqual(expect.objectContaining({ title: 'Q', conversationId: 'qa-stop' })))
  await waitFor(() => expect(pendingOf(client)).toBeNull())
  expect(result.current.conversationId).toBeNull()
  expect(result.current.state.turns).toHaveLength(0)
})
