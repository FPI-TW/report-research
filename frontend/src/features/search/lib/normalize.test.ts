import { describe, it, expect } from 'vitest'
import { normalizeItem, normalizeResult } from './normalize'

describe('normalize', () => {
  it('normalizeItem 無 rank/score', () => {
    const r = normalizeItem({
      report_id: 'r',
      file_name: 'f',
      market: 'TW',
      source: null,
      summary: null,
      report_date: null,
      report_type: null,
      instrument_types: null,
      relates_stock: null,
      relates_futures: null,
      stock_targets: null,
      futures_targets: null,
    })
    expect(r.rank).toBeUndefined()
    expect(r.report_id).toBe('r')
  })

  it('normalizeResult 帶 rank/bestScore/matchCount/passages', () => {
    const r = normalizeResult({
      rank: 3,
      best_score: 0.7,
      match_count: 2,
      passages: [{ score: 0.5, chunk_index: 1, content: 'c' }],
      report_id: 'r',
      file_name: 'f',
      market: 'TW',
      source: null,
      summary: null,
      report_date: null,
      report_type: null,
      instrument_types: null,
      relates_stock: null,
      relates_futures: null,
      stock_targets: null,
      futures_targets: null,
    })
    expect(r.rank).toBe(3)
    expect(r.bestScore).toBe(0.7)
    expect(r.matchCount).toBe(2)
    expect(r.passages?.length).toBe(1)
  })
})
