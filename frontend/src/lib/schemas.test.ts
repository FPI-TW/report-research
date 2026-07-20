import { describe, it, expect } from 'vitest'
import { reportListItemSchema, reportResultSchema, searchResponseSchema, reportFullSchema } from './schemas'

const baseItem = {
  report_id: 'r1', file_hash: 'a'.repeat(64), file_name: 'a.pdf', market: 'TW', source: '元大', summary: null,
  report_date: '2026-06-25', report_type: null, instrument_types: ['股票'],
  relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null,
}

describe('schemas', () => {
  it('parses a browse list item', () => {
    expect(reportListItemSchema.parse(baseItem).report_id).toBe('r1')
  })
  // file_hash 是閱讀頁 /report/:hash 的鍵，缺了會靜默壞掉整個檢索→閱讀動線
  it('requires file_hash on a list item', () => {
    const { file_hash: _omitted, ...withoutHash } = baseItem
    expect(() => reportListItemSchema.parse(withoutHash)).toThrow()
  })
  it('parses a search result (superset with passages)', () => {
    const r = reportResultSchema.parse({
      ...baseItem, rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 2, content: '片段' }],
    })
    expect(r.best_score).toBe(0.83)
    expect(r.passages[0].content).toBe('片段')
  })
  it('parses a search response envelope', () => {
    const s = searchResponseSchema.parse({
      query: 'AI', market: null, total: 1,
      results: [{ ...baseItem, rank: 1, best_score: 0.5, match_count: 1,
        passages: [{ score: 0.5, chunk_index: 0, content: 'x' }] }],
    })
    expect(s.total).toBe(1)
  })
  it('parses a report full (has_file bool)', () => {
    const f = reportFullSchema.parse({
      report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大',
      summary: null, report_date: null, report_type: null, has_file: true,
    })
    expect(f.has_file).toBe(true)
  })
})
