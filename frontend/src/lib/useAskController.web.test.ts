import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useAskController } from './useAskController'
import { setWebSearch } from './useWebSearch'
import * as api from './askApi'
import type { RawSSEEvent } from './readSSE'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

async function* askDone(): AsyncGenerator<RawSSEEvent> {
  yield { event: 'done', data: { qa_id: 'qa1', conversation_id: 'c1' } }
}

// 開關是 module 級 store，而請求體在 runStream 內組出來——漏掉 deps 陣列裡的 web
// 會讓「切換後第一題仍用舊值」，症狀是使用者開了網搜卻拿到沒查網路的答案。
describe('useAskController web-search threading (M11)', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); setWebSearch(false) })
  afterEach(() => { setWebSearch(false); localStorage.clear() })

  it('預設把 web=false 帶進 /api/ask', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: false })
  })

  it('開啟後 /api/ask 帶 web=true', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setWebSearch(true)
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電最新消息') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })

  it('渲染後才切換也要生效（runStream 需把 web 收進 deps）', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { setWebSearch(true) })
    act(() => { result.current.submit('台積電最新消息') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })

  it('重新生成也沿用當下的開關', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setWebSearch(true)
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.regenerate('t1', 'qa1', '台積電') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })
})
