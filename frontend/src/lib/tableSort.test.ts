import { describe, it, expect } from 'vitest'
import { sortRows, type TableSort } from './tableSort'
import type { ReportRow } from './schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'x', file_name: '', market: null, source: null, summary: null,
    report_date: null, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    ...p,
  }
}
const s = (key: TableSort['key'], dir: TableSort['dir']): TableSort => ({ key, dir })

describe('sortRows', () => {
  it('name 升冪/降冪', () => {
    const rows = [row({ file_name: 'B' }), row({ file_name: 'A' })]
    expect(sortRows(rows, s('name', 'asc')).map(r => r.file_name)).toEqual(['A', 'B'])
    expect(sortRows(rows, s('name', 'desc')).map(r => r.file_name)).toEqual(['B', 'A'])
  })
  it('score 數值排序（降冪）', () => {
    const rows = [row({ best_score: 0.2 }), row({ best_score: 0.9 })]
    expect(sortRows(rows, s('score', 'desc')).map(r => r.best_score)).toEqual([0.9, 0.2])
  })
  it('date 字串排序', () => {
    const rows = [row({ report_date: '2026-01-01' }), row({ report_date: '2026-06-01' })]
    expect(sortRows(rows, s('date', 'desc')).map(r => r.report_date)).toEqual(['2026-06-01', '2026-01-01'])
  })
  it('不變更輸入陣列', () => {
    const rows = [row({ file_name: 'B' }), row({ file_name: 'A' })]
    sortRows(rows, s('name', 'asc'))
    expect(rows.map(r => r.file_name)).toEqual(['B', 'A'])
  })
})
