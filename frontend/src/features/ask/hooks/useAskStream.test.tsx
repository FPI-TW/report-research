import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// 可控的 streamAsk mock：每次呼叫從佇列取一個 async generator factory。
// 用 vi.hoisted 宣告 genQueue —— vi.mock 會提升到 import 之上，factory 不能引用
// 一般外層 const（會 ReferenceError），須經 hoisted 共享。
const { genQueue } = vi.hoisted(() => ({ genQueue: [] as Array<() => AsyncGenerator<unknown>> }))
vi.mock('../lib/sse', () => ({
  streamAsk: () => {
    const make = genQueue.shift()
    if (!make) throw new Error('no gen queued')
    return make()
  },
}))

import { useAskStream } from './useAskStream'
import type { AskEvent } from '../lib/sseEvents'

function gen(events: AskEvent[], opts: { hang?: boolean } = {}): () => AsyncGenerator<AskEvent> {
  return async function* () {
    for (const e of events) yield e
    if (opts.hang) await new Promise(() => {}) // 永不結束（模擬仍在飛行）
  }
}

beforeEach(() => { genQueue.length = 0 })
afterEach(() => vi.clearAllMocks())

describe('useAskStream', () => {
  test('send 累積 token 並於 done 取回 conversation_id', async () => {
    genQueue.push(gen([
      { event: 'sources', data: [] },
      { event: 'token', data: '你' },
      { event: 'token', data: '好' },
      { event: 'done', data: { conversation_id: 'c1', qa_id: 'q1', thinking_ms: 800 } },
    ]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('done'))
    expect(result.current.turns[0].answer).toBe('你好')
    expect(result.current.turns[0].qaId).toBe('q1')
    expect(result.current.conversationId).toBe('c1')
  })

  test('單一大 token 也正確', async () => {
    genQueue.push(gen([{ event: 'token', data: '整段答案' }, { event: 'done', data: {} }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('done'))
    expect(result.current.turns[0].answer).toBe('整段答案')
  })

  test('error 事件 → errorMsg', async () => {
    genQueue.push(gen([{ event: 'error', data: { detail: 'x' } }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('error'))
    expect(result.current.turns[0].errorMsg).toContain('問答服務發生錯誤')
  })

  test('空串流（無 token 無 notice）→ 沒有取得回答', async () => {
    genQueue.push(gen([{ event: 'sources', data: [] }, { event: 'done', data: {} }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.errorMsg).toContain('沒有取得回答'))
  })

  test('latest-wins：新 send 後舊串流的後續事件不寫入', async () => {
    // 第一個串流 hang（吐一個 token 後不結束）；第二個正常完成
    genQueue.push(gen([{ event: 'token', data: '舊' }], { hang: true }))
    genQueue.push(gen([{ event: 'token', data: '新' }, { event: 'done', data: { conversation_id: 'c2' } }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('q1'))
    await waitFor(() => expect(result.current.turns[0]?.answer).toBe('舊'))
    act(() => result.current.send('q2'))
    await waitFor(() => expect(result.current.conversationId).toBe('c2'))
    // 應有兩輪；第二輪完成，第一輪保持「舊」（未被污染、未被新內容覆寫）
    expect(result.current.turns).toHaveLength(2)
    expect(result.current.turns[1].answer).toBe('新')
    expect(result.current.turns[0].answer).toBe('舊')
  })
})
