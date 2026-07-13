import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useAskController } from './useAskController'
import * as api from './askApi'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

async function* oneToken(): AsyncGenerator<any> {
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
    expect(result.current.state.turns[0].phase).toBe('stopped')
    expect(result.current.state.turns[0].qaId).toBe('qa-stop')
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
})
