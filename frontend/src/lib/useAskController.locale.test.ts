import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useAskController } from './useAskController'
import { setLocale } from './useLocale'
import * as api from './askApi'
import type { RawSSEEvent } from './readSSE'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

async function* askDone(): AsyncGenerator<RawSSEEvent> {
  yield { event: 'done', data: { qa_id: 'qa1', conversation_id: 'c1' } }
}

describe('useAskController locale threading (M10b)', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); setLocale('zh-Hant') })
  afterEach(() => { setLocale('zh-Hant'); localStorage.clear() })

  it('預設把 locale=zh-Hant 帶進 /api/ask', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ locale: 'zh-Hant' })
  })

  it('切換 en 後 /api/ask 帶 locale=en', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setLocale('en')  // 於 renderHook 前設好初值
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('TSMC outlook') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ locale: 'en' })
  })
})
