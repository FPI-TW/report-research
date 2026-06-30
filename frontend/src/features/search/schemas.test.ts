import { reportsSchema, searchSchema, statsSchema } from './schemas'

test('statsSchema 解析最小回應', () => {
  const out = statsSchema.parse({
    total_reports: 10, total_chunks: 100,
    markets: [{ market: 'TW', count: 5 }],
    instrument_types: [{ type: '個股', count: 3 }],
    report_types: [{ type: '法說會', count: 2 }],
    username: 'u',
  })
  expect(out.markets[0].market).toBe('TW')
})

test('reportsSchema 接受 null 欄位（nullish）', () => {
  const out = reportsSchema.parse({
    total: 1, offset: 0,
    items: [{
      report_id: 'r1', file_name: 'f', market: null, source: null, summary: null,
      report_date: null, report_type: null, instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    }],
  })
  expect(out.items[0].report_id).toBe('r1')
})

test('searchSchema 含 passages 與 rank/best_score', () => {
  const out = searchSchema.parse({
    query: 'q', market: null, total: 1,
    results: [{
      rank: 1, report_id: 'r1', file_name: 'f', market: 'TW', source: null, summary: null,
      report_date: null, report_type: null, instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
      best_score: 0.9, match_count: 2, passages: [{ score: 0.8, chunk_index: 0, content: 'x' }],
    }],
  })
  expect(out.results[0].passages[0].chunk_index).toBe(0)
})
