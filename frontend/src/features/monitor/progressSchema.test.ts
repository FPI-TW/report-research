import { expect, test } from 'vitest'
import { progressSchema } from './progressSchema'

const full = {
  ts: '12:00:00',
  db: { reports: 100, chunks: 5000, markets: [{ market: 'TW', count: 60 }] },
  summary: { done: 90, total: 100, remaining: 10, pct: 90 },
  tagging: { done: 80, total: 100, fail: 20, pct: 80 },
  ingest: { ingested: 5, chunks: 300, fail: 0 },
  pipelines: { web: true, ingest: false, tag: true, summaries: false },
  orchestrator: { raw: 'x', timestamp: null, status: 'running', label: '編排器執行中' },
}

test('解析完整 progress', () => {
  const p = progressSchema.parse(full)
  expect(p.db.reports).toBe(100)
  expect(p.db.markets[0].market).toBe('TW')
  expect(p.tagging?.pct).toBe(80)
})

test('tagging/ingest/orchestrator 可為 null', () => {
  const p = progressSchema.parse({ ...full, tagging: null, ingest: null, orchestrator: null })
  expect(p.tagging).toBeNull()
  expect(p.ingest).toBeNull()
  expect(p.orchestrator).toBeNull()
})

test('markets.market 可為 null', () => {
  const p = progressSchema.parse({ ...full, db: { reports: 1, chunks: 1, markets: [{ market: null, count: 3 }] } })
  expect(p.db.markets[0].market).toBeNull()
})

test('缺必要欄位 → throw', () => {
  expect(() => progressSchema.parse({ ...full, db: { reports: 1 } })).toThrow()
})

// ── 後端已回、前端未宣告 → zod 靜默剝除（實際發生過的回歸）─────────────
//
// zod 的 object 預設是 strip：未宣告的鍵不會報錯，會被**安靜地丟掉**。後端從 P4
// 起就在 /api/progress 回 takeaway/signal，但 schema 沒宣告，於是資料一路送到前端
// 卻從未進 DOM——沒有錯誤、沒有 console 警告，看起來就像後端沒回這些欄位。
const derived = {
  takeaway: { done: 5, total: 10, remaining: 5, pct: 50, latest: '2026-07-20' },
  signal: { done: 0, total: 10, remaining: 10, pct: 0, latest: null },
  evaluation: {
    qa: { total: 40, checked: 3, degraded: 1, below_min: 1, avg_score: 0.5634, latest: '2026-07-28' },
    report: null,
    min_score: 0.9,
  },
}

test('takeaway/signal/evaluation 不會被 zod 剝除', () => {
  const p = progressSchema.parse({ ...full, ...derived })
  expect(p.takeaway?.latest).toBe('2026-07-20')
  expect(p.signal?.latest).toBeNull()
  expect(p.evaluation?.qa?.degraded).toBe(1)
  expect(p.evaluation?.min_score).toBe(0.9)
})

test('三塊皆為 optional：舊後端不會讓整頁 parse 失敗', () => {
  // 滾動部署期間前端可能先上線。缺鍵就整張監控頁變空白，代價遠大於少一張卡。
  const p = progressSchema.parse(full)
  expect(p.takeaway).toBeUndefined()
  expect(p.evaluation).toBeUndefined()
})

test('evaluation.avg_score 可為 null（全部 degraded 時沒有分數）', () => {
  const p = progressSchema.parse({
    ...full,
    evaluation: { ...derived.evaluation, qa: { ...derived.evaluation.qa, avg_score: null } },
  })
  expect(p.evaluation?.qa?.avg_score).toBeNull()
})
