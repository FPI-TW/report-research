import { expect, test } from 'vitest'
import { parseAskEvent, parseReportEvent, conversationTurnSchema } from './askSchemas'

test('parseAskEvent 驗證各事件、拒未知/壞形狀', () => {
  expect(parseAskEvent({ event: 'status', data: { stage: 'retrieved', count: 8 } }))
    .toEqual({ event: 'status', data: { stage: 'retrieved', count: 8 } })
  expect(parseAskEvent({ event: 'token', data: '片段' })).toEqual({ event: 'token', data: '片段' })
  expect(parseAskEvent({ event: 'sources', data: [{ n: 1, report_id: 'r', file_name: 'f', market: 'TW', report_date: null, is_latest: false }] }))
    .toMatchObject({ event: 'sources' })
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
