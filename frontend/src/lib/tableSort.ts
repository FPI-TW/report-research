import { displayTitle } from './displayTitle'
import { marketLabel } from './meta'
import type { ReportRow } from './schemas'

export type TableSortKey = 'name' | 'market' | 'type' | 'date' | 'source' | 'score' | 'match'
export interface TableSort { key: TableSortKey; dir: 'asc' | 'desc' }

function valueOf(r: ReportRow, key: TableSortKey): string | number {
  switch (key) {
    // 依畫面上看到的字排序：表頭「報告名稱」顯示的是 displayTitle，
    // 排序若用 file_name 會出現「看起來沒排序」的清單。
    case 'name': return displayTitle(r, '')
    case 'market': return marketLabel(r.market ?? '')
    case 'type': return r.report_type ?? ''
    case 'date': return r.report_date ?? ''
    case 'source': return r.source ?? ''
    case 'score': return r.best_score ?? 0
    case 'match': return r.match_count ?? 0
  }
}

/** 依欄鍵排序（不變更輸入）。數值欄（score/match）用數值比較，其餘用中文 locale 字串比較。 */
export function sortRows(rows: ReportRow[], sort: TableSort): ReportRow[] {
  const out = [...rows]
  out.sort((a, b) => {
    const va = valueOf(a, sort.key)
    const vb = valueOf(b, sort.key)
    const c = typeof va === 'number' && typeof vb === 'number'
      ? va - vb
      : String(va).localeCompare(String(vb), 'zh-Hant')
    return sort.dir === 'asc' ? c : -c
  })
  return out
}
