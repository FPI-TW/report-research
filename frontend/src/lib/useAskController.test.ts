import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useAskController } from './useAskController'
import * as api from './askApi'
import type { RawSSEEvent } from './readSSE'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

async function* oneToken(): AsyncGenerator<RawSSEEvent> {
  yield { event: 'token', data: '部分' }
  // 掛住：模擬串流尚未結束（否則 for-await 自然收尾會觸發 ask-end 而非停止），
  // 測試在此期間呼叫 stop()。此 promise 永不 resolve；stop() 會 abort 並丟棄殘留。
  await new Promise<void>(() => {})
}

describe('useAskController stop', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('stop reports partial via stopAsk and sets stopped phase', async () => {
    vi.spyOn(api, 'streamAsk').mockReturnValue(oneToken())
    const stopSpy = vi.spyOn(api, 'stopAsk').mockResolvedValue({ qa_id: 'qa-stop' })

    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(result.current.state.turns[0]?.answer).toBe('部分'))

    await act(async () => { await result.current.stop() })

    expect(stopSpy).toHaveBeenCalledOnce()
    expect(stopSpy).toHaveBeenCalledWith(expect.objectContaining({ ext_sources: [] }))
    expect(result.current.state.turns[0].phase).toBe('stopped')
    expect(result.current.state.turns[0].qaId).toBe('qa-stop')
    // 首題在 done 前停止時，qa_id 同時是資料庫 COALESCE(conversation_id, id)
    // 的對話鍵；後續重生必須沿用它，否則會被拆成另一個對話。
    expect(result.current.conversationId).toBe('qa-stop')
  })

  it('stop still sets stopped when stopAsk fails (fail-open)', async () => {
    vi.spyOn(api, 'streamAsk').mockReturnValue(oneToken())
    vi.spyOn(api, 'stopAsk').mockRejectedValue(new Error('net'))

    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(result.current.state.turns[0]?.answer).toBe('部分'))

    await act(async () => { await result.current.stop() })
    expect(result.current.state.turns[0].phase).toBe('stopped')
  })

  it('stop during regenerate forwards regenerate_of (被取代版本的 qaId)', async () => {
    vi.spyOn(api, 'streamAsk')
      .mockReturnValueOnce((async function* () {
        yield { event: 'token', data: '第一版' }
        yield { event: 'done', data: { qa_id: 'qa-v1', conversation_id: 'c1' } }
      })())
      .mockReturnValueOnce(oneToken())
    const stopSpy = vi.spyOn(api, 'stopAsk').mockResolvedValue({ qa_id: 'qa-stop' })

    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(result.current.state.turns[0]?.qaId).toBe('qa-v1'))

    const turnId = result.current.state.turns[0].id
    act(() => { result.current.regenerate(turnId, 'qa-v1', '台積電') })
    await waitFor(() => expect(result.current.state.turns[0]?.answer).toBe('部分'))

    await act(async () => { await result.current.stop() })

    expect(stopSpy).toHaveBeenCalledOnce()
    expect(stopSpy.mock.calls[0][0]).toMatchObject({ regenerate_of: 'qa-v1' })
    expect(result.current.state.turns[0].phase).toBe('stopped')
  })

  it('新請求取代串流時，舊 turn 會結束為 error 而非永久 busy', async () => {
    vi.spyOn(api, 'streamAsk')
      .mockReturnValueOnce(oneToken())
      .mockReturnValueOnce(oneToken())

    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('第一題') })
    await waitFor(() => expect(result.current.state.turns[0]?.answer).toBe('部分'))

    act(() => { result.current.submit('第二題') })

    await waitFor(() => expect(result.current.state.turns[0]?.phase).toBe('error'))
    expect(result.current.state.turns[1]?.phase).not.toBe('error')
  })
})
