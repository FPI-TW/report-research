import { afterEach, expect, test, vi } from 'vitest'
import { getConversation, deleteConversation, sendFeedback } from './askApi'

afterEach(() => vi.unstubAllGlobals())

test('getConversation 解析歷史陣列', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify([
    { id: 'q1', question: 'Q', answer: 'A', created_at: '2026-06-20T00:00:00Z', feedback: null, sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [] },
  ]), { status: 200 })))
  const turns = await getConversation('c1')
  expect(turns).toHaveLength(1)
  expect(turns[0].question).toBe('Q')
})

test('deleteConversation：405 時 fallback POST /delete', async () => {
  const calls: Array<{ url: string; method?: string }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method })
    if (init?.method === 'DELETE') return new Response('', { status: 405 })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }))
  await deleteConversation('c1')
  expect(calls[0]).toMatchObject({ url: '/api/conversations/c1', method: 'DELETE' })
  expect(calls[1]).toMatchObject({ url: '/api/conversations/c1/delete', method: 'POST' })
})

test('sendFeedback POST 到 /api/feedback', async () => {
  const spy = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
  vi.stubGlobal('fetch', spy)
  await sendFeedback('qa1', 'like')
  const call = spy.mock.calls[0] as unknown as [string, RequestInit]
  expect(JSON.parse(call[1].body as string)).toEqual({ qa_id: 'qa1', value: 'like' })
})
