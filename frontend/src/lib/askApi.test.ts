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

test('deleteConversation：ok=true 正常結束', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })))
  await expect(deleteConversation('c1')).resolves.toBeUndefined()
})

// 以下四條是同一個缺陷的四種形狀：舊版**從不讀回應、從不 reject**，於是
// useMutation 的 onSuccess 照樣觸發 ⇒ 快取失效、重取後對話還在，而
// ConversationList 的 onSuccess 還會 navigate('/ask')——使用者被送離當前對話，
// 且沒有任何錯誤訊息。下一步當然是再按一次刪除。
test('deleteConversation：ok=false 必須 throw（後端「一列都沒刪到」回的是 200）', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: false }), { status: 200 })))
  await expect(deleteConversation('c1')).rejects.toThrow(/找不到該對話串/)
})

test('deleteConversation：500 必須 throw', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('boom', { status: 500 })))
  await expect(deleteConversation('c1')).rejects.toThrow(/HTTP 500/)
})

test('deleteConversation：fallback 之後的失敗也要 throw', async () => {
  // 405 → 走 POST fallback → fallback 自己回 ok=false。舊版連 fallback 的回應
  // 都不看，所以這種「兩次都失敗」看起來跟成功一模一樣。
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => (
    init?.method === 'DELETE'
      ? new Response('', { status: 405 })
      : new Response(JSON.stringify({ ok: false }), { status: 200 })
  )))
  await expect(deleteConversation('c1')).rejects.toThrow(/找不到該對話串/)
})

test('deleteConversation：空主體視為成功，不誘導使用者重按', async () => {
  // DELETE 成功但回應被代理改寫（或無主體）時，報「失敗」會讓使用者再按一次刪除，
  // 而東西其實已經沒了。這個方向的錯誤成本比較低，所以刻意偏向「當成成功」。
  // 註：204 依規範必須是 null body，`new Response('', {status:204})` 會被 fetch 的
  // Response 建構子拒絕——那是測試寫法的問題，不是待驗行為。
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })))
  await expect(deleteConversation('c1')).resolves.toBeUndefined()
})

test('deleteConversation：非 JSON 主體同樣視為成功', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('OK', { status: 200 })))
  await expect(deleteConversation('c1')).resolves.toBeUndefined()
})

test('sendFeedback POST 到 /api/feedback', async () => {
  const spy = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
  vi.stubGlobal('fetch', spy)
  await sendFeedback('qa1', 'like')
  const call = spy.mock.calls[0] as unknown as [string, RequestInit]
  expect(JSON.parse(call[1].body as string)).toEqual({ qa_id: 'qa1', value: 'like' })
})
