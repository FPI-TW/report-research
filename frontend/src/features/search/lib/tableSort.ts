import type { Row } from './normalize'
import { mLabel, tLabel } from '../components/meta'

export type TableSortKey = 'name' | 'market' | 'type' | 'date' | 'source' | 'score' | 'match'
export interface TableSort {
  key: TableSortKey | null
  dir: 'asc' | 'desc'
}

function value(r: Row, key: TableSortKey): string | number {
  switch (key) {
    case 'name':
      return (r.file_name || '').toLowerCase()
    case 'market':
      return mLabel(r.market ?? '')
    case 'type':
      return tLabel(r.report_type ?? '')
    case 'date':
      return r.report_date || ''
    case 'source':
      return r.source || ''
    case 'score':
      return r.bestScore || 0
    case 'match':
      return r.matchCount || 0
    default:
      return ''
  }
}

export function sortedRows(rows: Row[], sort: TableSort): Row[] {
  if (!sort.key) return [...rows]
  const key = sort.key
  const dir = sort.dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const va = value(a, key)
    const vb = value(b, key)
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
    return String(va).localeCompare(String(vb), 'zh-Hant') * dir
  })
}

export function nextTableSort(cur: TableSort, key: TableSortKey): TableSort {
  if (cur.key === key) return { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
  return { key, dir: 'asc' }
}
