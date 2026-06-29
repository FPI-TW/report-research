import { progressSchema } from './schemas'

const sample = {
  ts: '14:00:00',
  db: { reports: 11401, chunks: 360000, markets: [{ market: 'TW', count: 9000 }] },
  summary: { done: 100, total: 200, remaining: 100, pct: 50 },
  tagging: { pct: 99.1, done: 11000, total: 11100, fail: 100 },
  ingest: { ingested: 3, fail: 0 },
  pipelines: { web: true, ingest: false, tag: false, summaries: true },
  orchestrator: { label: '編排器', status: 'running', timestamp: '13:59', raw: '' },
}

test('progressSchema 接受完整 payload', () => {
  expect(progressSchema.parse(sample).db.reports).toBe(11401)
})

test('progressSchema 容許缺 runtime 欄位（tagging/ingest/orchestrator）', () => {
  const minimal = { ts: '14:00:00', db: { reports: 1, chunks: 2, markets: [] }, summary: { done: 0, total: 0, remaining: 0, pct: 0 } }
  expect(progressSchema.parse(minimal).tagging).toBeUndefined()
})

test('progressSchema 接受 null runtime 欄位（idle/全新部署狀態）', () => {
  const idle = {
    ts: '14:00:00',
    db: { reports: 5, chunks: 100, markets: [] },
    summary: { done: 0, total: 0, remaining: 0, pct: 0 },
    tagging: null,
    ingest: null,
    pipelines: null,
    orchestrator: null,
  }
  const result = progressSchema.safeParse(idle)
  expect(result.success).toBe(true)
})

test('progressSchema 接受 orchestrator 含 timestamp:null', () => {
  const payload = {
    ts: '14:00:00',
    db: { reports: 1, chunks: 2, markets: [] },
    summary: { done: 0, total: 0, remaining: 0, pct: 0 },
    orchestrator: { label: '測試', status: 'idle', timestamp: null, raw: '' },
  }
  const result = progressSchema.safeParse(payload)
  expect(result.success).toBe(true)
})
