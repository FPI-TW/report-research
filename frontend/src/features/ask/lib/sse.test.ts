import { describe, expect, test, vi } from 'vitest'
import { parseFrame, streamAsk } from './sse'
import * as apiModule from '../../../lib/api'

describe('parseFrame', () => {
  test('解析 token 事件（data 為 JSON 字串）', () => {
    expect(parseFrame('event: token\ndata: "台積電"')).toEqual({ event: 'token', data: '台積電' })
  })
  test('解析 sources 事件（data 為陣列）', () => {
    const f = 'event: sources\ndata: [{"n":1,"report_id":"r1","file_name":"a.pdf","market":"TW","report_date":null,"is_latest":true}]'
    expect(parseFrame(f)).toEqual({
      event: 'sources',
      data: [{ n: 1, report_id: 'r1', file_name: 'a.pdf', market: 'TW', report_date: null, is_latest: true }],
    })
  })
  test('解析 done 事件物件', () => {
    const f = 'event: done\ndata: {"cited":["r1"],"qa_id":"q1","conversation_id":"c1","thinking_ms":1200,"offer_report":false}'
    expect(parseFrame(f)?.event).toBe('done')
  })
  test('多行 data 串接後再 JSON.parse', () => {
    expect(parseFrame('event: token\ndata: "ab"')).toEqual({ event: 'token', data: 'ab' })
  })
  test('無 data 行回 null', () => {
    expect(parseFrame('event: ping')).toBeNull()
  })
  test('壞 JSON 回 null', () => {
    expect(parseFrame('event: token\ndata: {不是json')).toBeNull()
  })
})

/** 把字串陣列做成可控的 ReadableStream（每個 chunk 一段，模擬跨 chunk 切幀）。 */
function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(ctrl) {
      if (i < chunks.length) ctrl.enqueue(enc.encode(chunks[i++]))
      else ctrl.close()
    },
  })
}

async function collect(gen: AsyncGenerator<unknown>): Promise<unknown[]> {
  const out: unknown[] = []
  for await (const e of gen) out.push(e)
  return out
}

describe('streamAsk', () => {
  test('yield happy 事件序列，含跨 chunk 切幀', async () => {
    const body = streamFrom([
      'event: sources\ndata: []\n\n',
      'event: token\ndata: "你好"\n', // 幀被切在中間
      '\nevent: done\ndata: {"conversation_id":"c1"}\n\n',
    ])
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    const events = await collect(streamAsk({ question: 'q' }, new AbortController().signal))
    expect(events).toEqual([
      { event: 'sources', data: [] },
      { event: 'token', data: '你好' },
      { event: 'done', data: { conversation_id: 'c1' } },
    ])
  })

  test('單一大 token（無增量）也能 yield', async () => {
    const body = streamFrom(['event: token\ndata: "整段答案"\n\nevent: done\ndata: {}\n\n'])
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    const events = await collect(streamAsk({ question: 'q' }, new AbortController().signal))
    expect(events[0]).toEqual({ event: 'token', data: '整段答案' })
  })

  test('401 觸發導向登入並拋 ApiError', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, body: null })
    const redirectSpy = vi.spyOn(apiModule, 'redirectToLogin').mockImplementation(() => {})
    await expect(collect(streamAsk({ question: 'q' }, new AbortController().signal))).rejects.toMatchObject({ status: 401 })
    expect(redirectSpy).toHaveBeenCalled()
    redirectSpy.mockRestore()
  })

  test('非 OK 回應拋錯', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, body: null })
    await expect(collect(streamAsk({ question: 'q' }, new AbortController().signal))).rejects.toThrow()
  })
})
