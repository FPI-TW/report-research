import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor, act } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { featuresResponseSchema, setFeatures, useFeature, useFeatures } from './useFeatures'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

function respond(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  setFeatures(null)
})

describe('featuresResponseSchema', () => {
  it('只收 {features: {key: bool}}；不認得的鍵照收', () => {
    expect(featuresResponseSchema.parse({ features: { 'ask.web_search': false, 'brand.new': true } }).features)
      .toEqual({ 'ask.web_search': false, 'brand.new': true })
    expect(() => featuresResponseSchema.parse({ features: { 'ask.web_search': 'yes' } })).toThrow()
  })
})

describe('useFeatures／useFeature', () => {
  it('抓到之後寫進 store，只讀 store 的 useFeature 跟著更新', async () => {
    const fetchSpy = respond(200, { features: { 'ask.web_search': true, 'qa.agentic': false } })
    const reader = renderHook(() => [useFeature('ask.web_search'), useFeature('qa.agentic'), useFeature('nope')])
    expect(reader.result.current).toEqual([false, false, false])  // 還沒抓到＝全部關
    renderHook(() => useFeatures(), { wrapper })
    await waitFor(() => expect(reader.result.current).toEqual([true, false, false]))
    expect(fetchSpy).toHaveBeenCalledWith('/api/features', expect.objectContaining({ cache: 'no-store' }))
  })

  it('抓失敗時 store 不變（還沒抓到就維持全部關）', async () => {
    respond(503, { detail: 'x' })
    const { result } = renderHook(() => useFeatures(), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(renderHook(() => useFeature('ask.web_search')).result.current).toBe(false)
  })

  it('setFeatures 通知所有訂閱者', () => {
    const a = renderHook(() => useFeature('qa.agentic'))
    const b = renderHook(() => useFeature('qa.agentic'))
    act(() => setFeatures({ 'qa.agentic': true }))
    expect([a.result.current, b.result.current]).toEqual([true, true])
  })
})
