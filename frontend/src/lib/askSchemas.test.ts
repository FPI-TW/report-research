import { afterEach, describe, expect, it, test, vi } from 'vitest'
import { parseAskEvent, parseReportEvent, conversationTurnSchema, sourceSchema } from './askSchemas'

afterEach(() => vi.restoreAllMocks())

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

test('sourceSchema：title 必須留在解析結果（zod 預設 strip 未宣告鍵）', () => {
  const parsed = sourceSchema.parse({
    n: 1, report_id: 'r1', file_name: '6247269925_260728_gs_umt.pdf', market: 'TW',
    title: '低軌衛星業務擴展，維持買入',
  })
  expect(parsed.title).toBe('低軌衛星業務擴展，維持買入')
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

test('parseAskEvent／parseReportEvent 認得 queued（未宣告就會被靜默丟棄）', () => {
  // 本專案踩過：後端送了 section_draft 好幾個里程碑，parser 沒有對應 case 一律回 null，
  // 症狀只是「進度條停在 50% 不動」。新事件一律連同 parser 一起加，並用測試釘住。
  expect(parseAskEvent({ event: 'queued', data: { scope: 'ask', position: 2, capacity: 3 } }))
    .toEqual({ event: 'queued', data: { scope: 'ask', position: 2, capacity: 3 } })
  expect(parseReportEvent({ event: 'queued', data: { scope: 'report', position: 1, capacity: 1 } }))
    .toMatchObject({ event: 'queued' })
})

describe('done.answer：簡體→繁體的畫面校正', () => {
  // 同一顆地雷（zod strip）：後端只在轉換真的改動了內容時才帶這個欄位，schema
  // 沒宣告的話它會被安靜丟掉，畫面就永遠停在簡體那份、而 qa_log 是繁體。
  // 反轉實驗：把 askDoneData 的 answer 那一行刪掉，第一題必紅。
  it('done 帶得出校正後的答案', () => {
    const r = parseAskEvent({
      event: 'done',
      data: { conversation_id: 'c1', answer: '群聯電子營收創同期新高' },
    })
    expect((r as { data: { answer?: string } }).data.answer).toBe('群聯電子營收創同期新高')
  })

  it('沒帶 answer 仍是合法的 done（平時就是這樣）', () => {
    const r = parseAskEvent({ event: 'done', data: { conversation_id: 'c1' } })
    expect(r).not.toBeNull()
    expect((r as { data: { answer?: string } }).data.answer).toBeUndefined()
  })
})

describe('notice_kind：離題與時效婉拒必須分得開', () => {
  // zod 物件預設是 strip——未宣告的鍵不報錯、直接安靜丟掉。本專案踩過兩次
  // （/api/progress 的 takeaway/signal 從 P4 就在回，schema 沒宣告於是從未進 DOM）。
  // 這組的反轉實驗：把 askDoneData 的 notice_kind 那一行刪掉，下面第一題必紅。
  it('done 帶得出 notice_kind', () => {
    const r = parseAskEvent({
      event: 'done',
      data: { conversation_id: 'c1', notice_kind: 'time_sensitive' },
    })
    expect(r).not.toBeNull()
    expect((r as { data: { notice_kind?: string | null } }).data.notice_kind).toBe('time_sensitive')
  })

  it('未知的 kind 只讓該欄位退成 null，不讓整個 done 被丟棄', () => {
    // 滾動部署期間後端可能先送出前端還不認得的值；整包 parse 失敗會讓 done
    // 事件被靜默丟棄，畫面就永遠停在串流中。
    const r = parseAskEvent({
      event: 'done',
      data: { conversation_id: 'c1', notice_kind: 'something_new' },
    })
    expect(r).not.toBeNull()
    expect((r as { data: { notice_kind?: string | null } }).data.notice_kind).toBeNull()
  })

  it('歷史重播的 turn 也帶得出 notice_kind', () => {
    const t = conversationTurnSchema.parse({
      id: 'q1', question: 'Q', answer: 'A', created_at: null, feedback: null,
      sources: [], ext_sources: [], is_offtopic: true, notice_kind: 'off_topic',
      thinking_ms: null, reports: [], stages: [], followups: [],
      root_qa_id: null, version_count: 1, stopped: false,
    })
    expect(t.notice_kind).toBe('off_topic')
  })
})

describe('丟棄事件不再靜默（rejectEvent）', () => {
  // 回傳值刻意不變（仍是 null）——這組測的只是「有沒有留下痕跡」。沒有痕跡時，schema
  // 與後端 payload 漂移的唯一症狀是畫面少了東西，沒有任何線索指向 parser。
  it('schema 不合時警告並帶出 zod 的錯誤', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseAskEvent({ event: 'status', data: { stage: 'bogus' } })).toBeNull()
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0][0]).toBe('[sse] 丟棄事件')
    expect(warn.mock.calls[0][1]).toBe('status')
    expect(warn.mock.calls[0][2]).toBeTruthy() // zod error，不是 undefined
  })

  it('token/notice 的非字串 payload 也會警告', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseAskEvent({ event: 'token', data: 42 })).toBeNull()
    expect(parseAskEvent({ event: 'notice', data: null })).toBeNull()
    expect(parseReportEvent({ event: 'token', data: {} })).toBeNull()
    expect(warn).toHaveBeenCalledTimes(3)
  })

  it('未宣告的 event 種類會警告', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseAskEvent({ event: 'unknown', data: 1 })).toBeNull()
    expect(warn).toHaveBeenCalledWith('[sse] 丟棄事件', 'unknown', '未宣告的事件種類')
  })

  it('成功解析的事件不留噪音（每個 token 都會經過這條路）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseAskEvent({ event: 'token', data: '片段' })).not.toBeNull()
    expect(parseReportEvent({ event: 'status', data: { stage: 'writing' } })).not.toBeNull()
    expect(warn).not.toHaveBeenCalled()
  })

  it('document_revision 刻意忽略：回 null 但不警告', () => {
    // 「刻意忽略」與「忘了宣告」在回傳值上完全一樣（都是 null），差別只有這個警告。
    // 前者是 M7 的純加法事件（前端要的是 done 帶的 download_url），必須是明確的 case。
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseReportEvent({
      event: 'document_revision', data: { revision_id: 'rev-1', revision: 1 },
    })).toBeNull()
    expect(warn).not.toHaveBeenCalled()
  })
})

test('queued 欄位全 optional：後端只送部分欄位仍解析得出來', () => {
  // 滾動部署期間後端可能還是舊版（只有 scope），或已是新版（多了欄位）。
  // 整包 parse 失敗會讓事件回到「被靜默丟棄」，正是這裡要避免的事。
  expect(parseAskEvent({ event: 'queued', data: {} })).toEqual({ event: 'queued', data: {} })
  expect(parseAskEvent({ event: 'queued', data: { scope: 'ask' } })).toMatchObject({ event: 'queued' })
  // 型別錯了才該拒收
  expect(parseAskEvent({ event: 'queued', data: { position: '很多' } })).toBeNull()
})
