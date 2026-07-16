import { describe, expect, it, test } from 'vitest'
import { parseAskEvent, parseReportEvent, conversationTurnSchema, sourceSchema } from './askSchemas'

test('parseAskEvent 驗證各事件、拒未知/壞形狀', () => {
  expect(parseAskEvent({ event: 'status', data: { stage: 'retrieved', count: 8 } }))
    .toEqual({ event: 'status', data: { stage: 'retrieved', count: 8 } })
  expect(parseAskEvent({ event: 'token', data: '片段' })).toEqual({ event: 'token', data: '片段' })
  expect(parseAskEvent({ event: 'sources', data: [{ n: 1, report_id: 'r', file_name: 'f', market: 'TW', report_date: null, is_latest: false }] }))
    .toMatchObject({ event: 'sources' })
  expect(parseAskEvent({ event: 'error', data: { detail: '問答服務發生錯誤' } }))
    .toEqual({ event: 'error', data: { detail: '問答服務發生錯誤' } })
  // done 路徑差異：允許缺 qa_id/offer_report
  expect(parseAskEvent({ event: 'done', data: { conversation_id: 'c1' } }))
    .toEqual({ event: 'done', data: { conversation_id: 'c1' } })
  expect(parseAskEvent({ event: 'status', data: { stage: 'bogus' } })).toBeNull()
  expect(parseAskEvent({ event: 'unknown', data: 1 })).toBeNull()
})

test('parseReportEvent 驗證 status/done/error', () => {
  expect(parseReportEvent({ event: 'status', data: { stage: 'writing' } })).toMatchObject({ event: 'status' })
  expect(parseReportEvent({ event: 'done', data: { report_id: 'r', title: 't', download_url: '/api/report-doc/r/pdf' } }))
    .toMatchObject({ event: 'done' })
  expect(parseReportEvent({ event: 'error', data: { detail: 'x' } })).toMatchObject({ event: 'error' })
  expect(parseReportEvent({ event: 'done', data: { title: 't' } })).toBeNull() // 缺 report_id
})

test('conversationTurnSchema 容錯缺欄', () => {
  const t = conversationTurnSchema.parse({
    id: 'q1', question: 'Q', answer: 'A', created_at: '2026-06-20T00:00:00Z',
    feedback: null, sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
  })
  expect(t.question).toBe('Q')
})

test('sourceSchema：歷史來源缺 is_latest/report_date 仍可解析（不 throw）', () => {
  const legacy = { n: 1, report_id: 'r1', file_name: '台積電.pdf', market: 'TW' } // 無 is_latest / report_date
  const parsed = sourceSchema.parse(legacy)
  expect(parsed.is_latest).toBe(false)
  expect(parsed.report_date).toBeNull()
})

test('conversationTurnSchema：sources 含歷史缺欄物件仍可解析（不 throw）', () => {
  const legacySource = { n: 1, report_id: 'r1', file_name: '台積電.pdf', market: 'TW' } // 無 is_latest / report_date
  const t = conversationTurnSchema.parse({
    id: 'q1', question: 'Q', answer: 'A', created_at: '2026-06-20T00:00:00Z',
    feedback: null, sources: [legacySource], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
  })
  expect(t.sources).toHaveLength(1)
  expect(t.sources[0].is_latest).toBe(false)
  expect(t.sources[0].report_date).toBeNull()
})

describe('askSchemas M5', () => {
  it('status 事件接受 evaluating stage', () => {
    expect(parseAskEvent({ event: 'status', data: { stage: 'evaluating' } }))
      .toEqual({ event: 'status', data: { stage: 'evaluating' } })
  })

  it('既有 stage 值不受影響', () => {
    for (const stage of ['understanding', 'retrieved', 'reading', 'searching_web', 'generating']) {
      expect(parseAskEvent({ event: 'status', data: { stage } })).toEqual({ event: 'status', data: { stage } })
    }
    expect(parseAskEvent({ event: 'status', data: { stage: 'bogus' } })).toBeNull()
  })

  it('conversationTurn 歷史 stages 含 evaluating 不被 catch 清空', () => {
    const t = conversationTurnSchema.parse({
      id: 'x', question: 'q', answer: 'a',
      stages: ['understanding', 'evaluating', 'retrieved', 'reading', 'generating'],
    })
    expect(t.stages).toEqual(['understanding', 'evaluating', 'retrieved', 'reading', 'generating'])
  })
})

describe('askSchemas M3', () => {
  it('parses followups event', () => {
    const ev = parseAskEvent({ event: 'followups', data: ['問一', '問二'] })
    expect(ev).toEqual({ event: 'followups', data: ['問一', '問二'] })
  })

  it('rejects non-string-array followups', () => {
    expect(parseAskEvent({ event: 'followups', data: [1, 2] })).toBeNull()
  })

  it('conversationTurn defaults new fields', () => {
    const t = conversationTurnSchema.parse({
      id: 'x', question: 'q', answer: 'a',
    })
    expect(t.stages).toEqual([])
    expect(t.followups).toEqual([])
    expect(t.version_count).toBe(1)
    expect(t.stopped).toBe(false)
    expect(t.root_qa_id).toBeNull()
  })
})
