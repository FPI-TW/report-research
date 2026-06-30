import { describe, expect, test, vi, afterEach } from 'vitest'
import { conversationSummarySchema, historyItemSchema } from './schemas'
import { getConversations, deleteConversation } from './api'

afterEach(() => vi.restoreAllMocks())

describe('schemas', () => {
  test('conversationSummary 解析', () => {
    expect(() =>
      conversationSummarySchema.parse({ conversation_id: 'c1', title: 'Q', last_at: '2026-01-01', turn_count: 2 }),
    ).not.toThrow()
  })
  test('historyItem 容忍 null 選用欄', () => {
    const parsed = historyItemSchema.parse({
      id: 'q1', question: 'Q', answer: null, created_at: null, feedback: null,
      sources: [], ext_sources: [], is_offtopic: null, thinking_ms: null, reports: null,
    })
    expect(parsed.sources).toEqual([])
  })
})

describe('api', () => {
  test('getConversations 打對 endpoint 並回陣列', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => [{ conversation_id: 'c1', title: 'Q', last_at: null, turn_count: 1 }],
    })
    const r = await getConversations()
    expect(r).toHaveLength(1)
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/conversations')
  })

  test('deleteConversation：DELETE 成功', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ok: true }) })
    await expect(deleteConversation('c1')).resolves.toEqual({ ok: true })
  })

  test('deleteConversation：404 退回 POST /delete', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) })
    globalThis.fetch = fetchMock
    await expect(deleteConversation('c1')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
    expect(fetchMock.mock.calls[1][0]).toContain('/delete')
    expect(fetchMock.mock.calls[1][1].method).toBe('POST')
  })
})
