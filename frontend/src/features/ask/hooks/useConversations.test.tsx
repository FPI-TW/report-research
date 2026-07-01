import { describe, expect, test, vi, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('../api', () => ({
  getConversations: vi.fn().mockResolvedValue([{ conversation_id: 'c1', title: 'Q', last_at: null, turn_count: 1 }]),
  deleteConversation: vi.fn().mockResolvedValue({ ok: true }),
}))

import { useConversations } from './useConversations'
import * as api from '../api'
import { ApiError } from '../../../lib/api'

afterEach(() => vi.clearAllMocks())

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useConversations', () => {
  test('載入清單', async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
  })

  test('remove 呼叫 deleteConversation', async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
    act(() => result.current.remove('c1'))
    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith('c1'))
  })

  // 401 的「導向登入」動作實作於 getJSON（見 lib/api.test.ts），
  // 此處只驗證 hook 自身對 getConversations 拋出的 ApiError 之處理：
  // 落在 isError、conversations 退回空陣列、且因 hook 內建 retry:false 不重試。
  test('getConversations 拋 401 ApiError → isError 為 true、conversations 退回空陣列、不重試', async () => {
    vi.mocked(api.getConversations).mockRejectedValue(new ApiError(401, '未登入'))
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.conversations).toEqual([])
    expect(api.getConversations).toHaveBeenCalledTimes(1)
  })

  test('getConversations 拋非 401 錯誤 → 同樣以 isError 呈現、不崩潰', async () => {
    vi.mocked(api.getConversations).mockRejectedValue(new ApiError(500, 'HTTP 500'))
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.conversations).toEqual([])
  })
})
