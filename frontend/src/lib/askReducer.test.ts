import { describe, expect, it, test } from 'vitest'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'

function submit(): AskState {
  return askReducer(initialAskState, { type: 'submit', id: 't1', question: 'Q', startedAt: 1000 })
}
const ev = (event: unknown) => ({ type: 'ask-event' as const, id: 't1', event: event as never })

function seeded(): AskState {
  const s = askReducer(initialAskState, { type: 'submit', id: 't1', question: 'q', startedAt: 0 })
  return askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'token', data: '答案' } })
}

test('submit 推入一筆 thinking turn', () => {
  const s = submit()
  expect(s.turns).toHaveLength(1)
  expect(s.turns[0]).toMatchObject({ id: 't1', question: 'Q', phase: 'thinking', answer: '' })
})

test('正常 RAG：sources→retrieved→reading→generating→token→done', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f', market: 'TW', report_date: '2026-06-20', is_latest: true }] }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'retrieved', count: 8 } }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'reading' } }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'generating', thinking_ms: 4200 } }))
  s = askReducer(s, ev({ event: 'token', data: '答' }))
  s = askReducer(s, ev({ event: 'token', data: '案' }))
  s = askReducer(s, ev({ event: 'done', data: { cited: ['r1'], qa_id: 'qa1', conversation_id: 'c1', thinking_ms: 4200, offer_report: true, report_title: 'Q 深度研報' } }))
  const t = s.turns[0]
  expect(t.answer).toBe('答案')
  expect(t.phase).toBe('done')
  expect(t.qaId).toBe('qa1')
  expect(t.retrievedCount).toBe(8)
  expect(t.thinkingMs).toBe(4200)
  expect(t.offerReport).toBe(true)
  expect(t.report.status).toBe('offered')
  expect(t.sources).toHaveLength(1)
})

test('離題：notice→done 保持 notice、qaId 為 null、不 offer', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'notice', data: '無法回答此問題' }))
  s = askReducer(s, ev({ event: 'done', data: { conversation_id: 'c1' } }))
  const t = s.turns[0]
  expect(t.phase).toBe('notice')
  expect(t.isOfftopic).toBe(true)
  expect(t.noticeText).toBe('無法回答此問題')
  expect(t.qaId).toBeNull()
  expect(t.offerReport).toBe(false)
})

test('ask-end 無 token 且非 notice → error', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'sources', data: [] }))
  s = askReducer(s, { type: 'ask-end', id: 't1' })
  expect(s.turns[0].phase).toBe('error')
  expect(s.turns[0].errorText).toBe('查詢逾時或失敗')
})

test('ask-end 有部分 token 但缺 terminal 事件 → error', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'token', data: '半句回答' }))
  s = askReducer(s, { type: 'ask-end', id: 't1' })
  expect(s.turns[0].phase).toBe('error')
  expect(s.turns[0].answer).toBe('半句回答')
  expect(s.turns[0].errorText).toBe('查詢逾時或失敗')
})

test('ask error 事件會保留 partial answer 並標成 error', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'token', data: '半句回答' }))
  s = askReducer(s, ev({ event: 'error', data: { detail: '問答服務發生錯誤' } }))
  expect(s.turns[0].phase).toBe('error')
  expect(s.turns[0].answer).toBe('半句回答')
  expect(s.turns[0].errorText).toBe('問答服務發生錯誤')
})

test('searching_web 設 webUsed', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'status', data: { stage: 'searching_web' } }))
  expect(s.turns[0].webUsed).toBe(true)
})

test('研報：start→status→done', () => {
  let s = submit()
  s = askReducer(s, { type: 'report-start', id: 't1' })
  expect(s.turns[0].report.status).toBe('generating')
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'status', data: { stage: 'writing' } } })
  expect(s.turns[0].report).toMatchObject({ pct: 50, stageText: '撰寫研報中…' })
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'done', data: { report_id: 'rp1', title: 'T', download_url: '/api/report-doc/rp1/pdf' } } })
  expect(s.turns[0].report).toMatchObject({ status: 'done', pct: 100, downloadUrl: '/api/report-doc/rp1/pdf', title: 'T' })
})

test('研報 error 事件 → error 態', () => {
  let s = submit()
  s = askReducer(s, { type: 'report-start', id: 't1' })
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'error', data: { detail: '找不到足夠資料生成研報' } } })
  expect(s.turns[0].report).toMatchObject({ status: 'error', errorText: '找不到足夠資料生成研報' })
})

test('feedback / reset / load', () => {
  let s = submit()
  s = askReducer(s, { type: 'feedback', id: 't1', value: 'like' })
  expect(s.turns[0].feedback).toBe('like')
  s = askReducer(s, { type: 'reset' })
  expect(s.turns).toEqual([])
  const turn = turnFromHistory({ id: 'qa9', question: 'H', answer: 'A', created_at: '2026-06-20T00:00:00Z', feedback: 'dislike', sources: [], ext_sources: [], is_offtopic: false, thinking_ms: 1500, reports: [{ report_id: 'rp', title: 'RT', download_url: '/api/report-doc/rp/pdf', created_at: null }], stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false })
  s = askReducer(s, { type: 'load', turns: [turn] })
  expect(s.turns[0]).toMatchObject({ id: 'qa9', phase: 'done', qaId: 'qa9', feedback: 'dislike' })
  expect(s.turns[0].report).toMatchObject({ status: 'done', downloadUrl: '/api/report-doc/rp/pdf' })
})

test('turnFromHistory 離題轉 notice、qaId null', () => {
  const t = turnFromHistory({ id: 'qaX', question: 'H', answer: '無法回答此問題', created_at: null, feedback: null, sources: [], ext_sources: [], is_offtopic: true, thinking_ms: null, reports: [], stages: [], followups: [], root_qa_id: null, version_count: 1, stopped: false })
  expect(t.phase).toBe('notice')
  expect(t.noticeText).toBe('無法回答此問題')
  expect(t.qaId).toBeNull()
})

test('report-cancel：generating→offered；done 不被還原', () => {
  let s = submit()
  s = askReducer(s, { type: 'report-start', id: 't1' })
  s = askReducer(s, { type: 'report-cancel', id: 't1' })
  expect(s.turns[0].report.status).toBe('offered')

  let s2 = submit()
  s2 = askReducer(s2, { type: 'report-start', id: 't1' })
  s2 = askReducer(s2, { type: 'report-event', id: 't1', event: { event: 'done', data: { report_id: 'r', title: 'T', download_url: '/api/report-doc/r/pdf' } } })
  s2 = askReducer(s2, { type: 'report-cancel', id: 't1' })
  expect(s2.turns[0].report.status).toBe('done')
})

describe('askReducer M3', () => {
  it('ask-stop sets stopped phase and keeps answer + qaId', () => {
    const s = askReducer(seeded(), { type: 'ask-stop', id: 't1', qaId: 'qa-stop' })
    const t = s.turns[0]
    expect(t.phase).toBe('stopped')
    expect(t.answer).toBe('答案')
    expect(t.qaId).toBe('qa-stop')
  })

  it('ask-end does not overwrite stopped', () => {
    let s = askReducer(seeded(), { type: 'ask-stop', id: 't1', qaId: 'x' })
    s = askReducer(s, { type: 'ask-end', id: 't1' })
    expect(s.turns[0].phase).toBe('stopped')
  })

  it('followups sets chips', () => {
    const s = askReducer(seeded(), { type: 'followups', id: 't1', data: ['追問一', '追問二'] })
    expect(s.turns[0].followups).toEqual(['追問一', '追問二'])
  })

  it('regenerate-start snapshots prior version and resets live', () => {
    let s = seeded()
    s = askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'done', data: { conversation_id: 'c', qa_id: 'qa1' } } })
    s = askReducer(s, { type: 'regenerate-start', id: 't1' })
    const t = s.turns[0]
    expect(t.priorVersions.length).toBe(1)
    expect(t.priorVersions[0].answer).toBe('答案')
    expect(t.answer).toBe('')
    expect(t.phase).toBe('thinking')
    expect(t.versionIndex).toBe(1)
  })

  it('set-version switches displayed index', () => {
    let s = seeded()
    s = askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'done', data: { conversation_id: 'c', qa_id: 'qa1' } } })
    s = askReducer(s, { type: 'regenerate-start', id: 't1' })
    s = askReducer(s, { type: 'set-version', id: 't1', index: 0 })
    expect(s.turns[0].versionIndex).toBe(0)
  })

  it('truncate-after removes turns following the given id', () => {
    let s = askReducer(initialAskState, { type: 'submit', id: 't1', question: 'q1', startedAt: 0 })
    s = askReducer(s, { type: 'submit', id: 't2', question: 'q2', startedAt: 0 })
    s = askReducer(s, { type: 'truncate-after', id: 't1' })
    expect(s.turns.map(t => t.id)).toEqual(['t1'])
  })

  it('submit-edit resets turn to new question thinking state', () => {
    let s = seeded()
    s = askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'done', data: { conversation_id: 'c', qa_id: 'qa1' } } })
    s = askReducer(s, { type: 'submit-edit', id: 't1', question: '新問題' })
    expect(s.turns[0].question).toBe('新問題')
    expect(s.turns[0].phase).toBe('thinking')
    expect(s.turns[0].answer).toBe('')
  })

  it('turnFromHistory 重載多版本 turn：versionIndex 對齊最新版（避免 pager 標籤/內容錯位）', () => {
    const t = turnFromHistory({
      id: 'qa9', question: 'H', answer: '最新答案', created_at: null, feedback: null,
      sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
      stages: [], followups: [], root_qa_id: 'qa1', version_count: 3, stopped: false,
    })
    expect(t.versionIndex).toBe(2)
    expect(t.versionCount).toBe(3)
    expect(t.priorVersions).toEqual([])
  })

  it('load-versions fills priorVersions from all-but-last', () => {
    const versions = [
      { qa_id: 'v1', answer: '答一', sources: [], ext_sources: [], thinking_ms: 100, stages: [], feedback: null, created_at: null },
      { qa_id: 'v2', answer: '答二', sources: [], ext_sources: [], thinking_ms: 120, stages: [], feedback: 'like' as const, created_at: null },
    ]
    let s = seeded()
    s = askReducer(s, { type: 'load-versions', id: 't1', versions })
    expect(s.turns[0].priorVersions.length).toBe(1)
    expect(s.turns[0].priorVersions[0].qaId).toBe('v1')
    expect(s.turns[0].versionCount).toBe(2)
    expect(s.turns[0].versionIndex).toBe(1)
  })

  it('done 對齊 versionIndex 至最新版：伺服器權威 version_count 晚到時不讓 isLive 誤 false', () => {
    // 重載多版本後直接重生：regenerate-start 樂觀設 versionIndex=1/versionCount=2，
    // done 帶回伺服器 version_count=4 → versionIndex 應對齊 3（=versionCount-1），使 isLive 為真。
    let s = seeded()
    s = askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'done', data: { conversation_id: 'c', qa_id: 'qa1' } } })
    s = askReducer(s, { type: 'regenerate-start', id: 't1' })
    s = askReducer(s, { type: 'ask-event', id: 't1', event: { event: 'done', data: { conversation_id: 'c', qa_id: 'qa2', version_count: 4 } } })
    expect(s.turns[0].versionCount).toBe(4)
    expect(s.turns[0].versionIndex).toBe(3)
  })
})
