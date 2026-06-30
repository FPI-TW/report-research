import { describe, expect, test } from 'vitest'
import { buildAskBody, emptyTurn, historyToTurn } from './conversation'

describe('buildAskBody', () => {
  test('首輪無 conversation_id', () => {
    expect(buildAskBody('hi', null)).toEqual({ question: 'hi' })
  })
  test('多輪帶 conversation_id', () => {
    expect(buildAskBody('hi', 'c1')).toEqual({ question: 'hi', conversation_id: 'c1' })
  })
})

describe('emptyTurn', () => {
  test('初始為 streaming 空狀態', () => {
    const t = emptyTurn('t1', 'Q')
    expect(t).toMatchObject({ id: 't1', q: 'Q', answer: '', phase: 'streaming', sources: [], qaId: null })
  })
})

describe('historyToTurn', () => {
  test('一般輪映射欄位且 phase=done', () => {
    const t = historyToTurn(
      { id: 'q1', question: 'Q', answer: 'A', feedback: 'like', sources: [], ext_sources: [], is_offtopic: false, thinking_ms: 900 },
      'h0',
    )
    expect(t).toMatchObject({ q: 'Q', answer: 'A', qaId: 'q1', feedback: 'like', thinkingMs: 900, phase: 'done' })
  })
  test('離題輪 phase=notice、notice 取 answer', () => {
    const t = historyToTurn({ id: 'q2', question: 'Q', answer: '無法回答', is_offtopic: true }, 'h1')
    expect(t.phase).toBe('notice')
    expect(t.notice).toBe('無法回答')
  })
})
