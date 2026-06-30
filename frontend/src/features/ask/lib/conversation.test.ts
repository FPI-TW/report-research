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
  test('historyToTurn 過濾非 http(s) ext_sources，保留 http(s) 來源', () => {
    const t = historyToTurn(
      {
        id: 'q3',
        question: 'Q',
        ext_sources: [
          { url: 'https://ok.com', title: 'OK' },
          { url: 'javascript:alert(1)', title: 'Bad' },
          { url: 'http://also-ok.com', title: 'Also OK' },
          { url: 'data:text/html,<h1>bad</h1>', title: 'Data' },
        ],
      },
      'h2',
    )
    expect(t.extSources).toHaveLength(2)
    expect(t.extSources.map((s) => s.url)).toEqual(['https://ok.com', 'http://also-ok.com'])
  })
})
