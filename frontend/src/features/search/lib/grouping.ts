import type { Row } from './normalize'

export type GroupView = 'grouped' | 'index' | 'drill'

/**
 * Determines which rendering mode to use for grouped view.
 * Aligns to web/static/app/state.js groupViewMode.
 */
export function groupViewMode(group: 'month' | 'market', market: string): GroupView {
  if (group !== 'market') return 'grouped'
  return market === '全部' ? 'index' : 'drill'
}

/**
 * Returns the grouping key for a row given the group dimension.
 * month → YYYY-MM prefix of report_date (empty string if null/undefined)
 * market → market value (empty string if null/undefined)
 */
export function groupKey(row: Row, group: 'month' | 'market'): string {
  if (group === 'month') {
    return row.report_date ? row.report_date.slice(0, 7) : ''
  }
  return row.market ?? ''
}

/**
 * Groups rows by the given dimension, preserving first-seen key order.
 * Returns { key, rows }[] — no label field (labels computed in components via meta).
 */
export function groupRows(
  rows: Row[],
  group: 'month' | 'market',
): { key: string; rows: Row[] }[] {
  const map = new Map<string, Row[]>()
  for (const row of rows) {
    const k = groupKey(row, group)
    if (!map.has(k)) map.set(k, [])
    map.get(k)!.push(row)
  }
  return Array.from(map.entries()).map(([key, rows]) => ({ key, rows }))
}
