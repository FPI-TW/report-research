import { describe, expect, test } from 'vitest'
import { parseFrame } from './sse'

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
