import { describe, it, expect } from 'vitest'
import { monthGroups } from './grouping'
import type { ReportRow } from './schemas'

function row(id: string, date: string | null): ReportRow {
  return {
    report_id: id, file_hash: id.repeat(64).slice(0, 64), file_name: id + '.pdf',
    market: 'TW', source: null, summary: null,
    report_date: date, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
  }
}

describe('monthGroups', () => {
  it('依 YYYY-MM 分組、標頭 YYYY 年 M 月、count、組內順序保留', () => {
    const g = monthGroups([row('a', '2026-06-25'), row('b', '2026-06-02'), row('c', '2026-05-30')])
    expect(g.map(x => x.title)).toEqual(['2026 年 6 月', '2026 年 5 月'])
    expect(g[0].count).toBe(2)
    expect(g[0].items.map(i => i.report_id)).toEqual(['a', 'b'])
  })
  it('無/不合法日期→「未標日期」組置底', () => {
    const g = monthGroups([row('a', null), row('b', '2026-06-01'), row('c', 'bad')])
    expect(g.map(x => x.title)).toEqual(['2026 年 6 月', '未標日期'])
    expect(g[1].items.map(i => i.report_id)).toEqual(['a', 'c'])
  })
})
