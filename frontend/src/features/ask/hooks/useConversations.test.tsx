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
})
