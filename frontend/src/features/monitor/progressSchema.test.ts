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
