import { groupViewMode, groupKey, groupRows } from './grouping'
import type { Row } from './normalize'

// ── groupViewMode ──────────────────────────────────────────────
test('groupViewMode: market + 全部 → index', () => {
  expect(groupViewMode('market', '全部')).toBe('index')
})
test('groupViewMode: market + TW → drill', () => {
  expect(groupViewMode('market', 'TW')).toBe('drill')
})
test('groupViewMode: month + 全部 → grouped', () => {
  expect(groupViewMode('month', '全部')).toBe('grouped')
})
test('groupViewMode: month + TW → grouped', () => {
  expect(groupViewMode('month', 'TW')).toBe('grouped')
})

// ── groupKey ───────────────────────────────────────────────────
test('groupKey: month returns YYYY-MM slice', () => {
  const r = { report_id: '1', file_name: 'f', report_date: '2025-03-15' } as Row
  expect(groupKey(r, 'month')).toBe('2025-03')
})
test('groupKey: month with null report_date returns empty string', () => {
  const r = { report_id: '1', file_name: 'f', report_date: null } as Row
  expect(groupKey(r, 'month')).toBe('')
})
test('groupKey: market returns market value', () => {
  const r = { report_id: '1', file_name: 'f', market: 'TW' } as Row
  expect(groupKey(r, 'market')).toBe('TW')
})
test('groupKey: market with null market returns empty string', () => {
  const r = { report_id: '1', file_name: 'f', market: null } as Row
  expect(groupKey(r, 'market')).toBe('')
})

// ── groupRows ──────────────────────────────────────────────────
test('groupRows: groups by market, preserves first-seen key order', () => {
  const rows = [
    { report_id: '1', file_name: 'a', market: 'TW' },
    { report_id: '2', file_name: 'b', market: 'US' },
    { report_id: '3', file_name: 'c', market: 'TW' },
  ] as Row[]
  const g = groupRows(rows, 'market')
  expect(g.map((x) => x.key)).toEqual(['TW', 'US'])
  expect(g[0].rows.length).toBe(2)
  expect(g[1].rows.length).toBe(1)
})
test('groupRows: groups by month, preserves first-seen key order', () => {
  const rows = [
    { report_id: '1', file_name: 'a', report_date: '2025-03-10' },
    { report_id: '2', file_name: 'b', report_date: '2025-01-05' },
    { report_id: '3', file_name: 'c', report_date: '2025-03-22' },
  ] as Row[]
  const g = groupRows(rows, 'month')
  expect(g.map((x) => x.key)).toEqual(['2025-03', '2025-01'])
  expect(g[0].rows.length).toBe(2)
})
test('groupRows: no label field on result entries', () => {
  const rows = [{ report_id: '1', file_name: 'a', market: 'TW' }] as Row[]
  const g = groupRows(rows, 'market')
  expect('label' in g[0]).toBe(false)
})
test('groupRows: empty input returns empty array', () => {
  expect(groupRows([], 'market')).toEqual([])
})
