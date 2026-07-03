import { marketColor, marketLabel } from '../../lib/meta'
import { sortRows, type TableSort, type TableSortKey } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './TableView.module.css'

interface Col { key: TableSortKey | null; label: string }
const BASE_COLS: Col[] = [
  { key: 'name', label: '報告名稱' },
  { key: 'market', label: '市場' },
  { key: 'type', label: '類型' },
  { key: 'date', label: '日期' },
  { key: 'source', label: '來源' },
  { key: null, label: '標的' },
]
const SEARCH_COLS: Col[] = [
  { key: 'score', label: '相關度' },
  { key: 'match', label: '命中' },
]

interface Props {
  rows: ReportRow[]
  mode: SearchMode
  sort: TableSort
  onSort: (key: TableSortKey) => void
  onOpen: (id: string, fileName: string) => void
}

export function TableView({ rows, mode, sort, onSort, onOpen }: Props) {
  const cols = mode === 'search' ? [...BASE_COLS, ...SEARCH_COLS] : BASE_COLS
  const sorted = sortRows(rows, sort)
  const arrow = (key: TableSortKey) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '')
  return (
    <div className={styles.wrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {cols.map(c => (
              <th
                key={c.label}
                className={c.key ? styles.sortable : undefined}
                aria-sort={c.key && sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                onClick={c.key ? () => onSort(c.key as TableSortKey) : undefined}
              >
                {c.label}{c.key ? arrow(c.key) : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(r => {
            const targets = [...(r.stock_targets ?? []), ...(r.futures_targets ?? [])]
            return (
              <tr
                key={r.report_id}
                className={styles.row}
                tabIndex={0}
                onClick={() => onOpen(r.report_id, r.file_name)}
                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(r.report_id, r.file_name) } }}
              >
                <td className={styles.name}>{r.file_name}</td>
                <td>
                  <span className={styles.badge} style={{ background: marketColor(r.market ?? '') }}>
                    {marketLabel(r.market ?? '')}
                  </span>
                </td>
                <td>{r.report_type ?? ''}</td>
                <td className={styles.nowrap}>{(r.report_date ?? '').slice(0, 10)}</td>
                <td className={styles.nowrap}>{r.source ?? ''}</td>
                <td>{targets.join('、')}</td>
                {mode === 'search' && <td className={styles.nowrap}>{Math.round((r.best_score ?? 0) * 100)}%</td>}
                {mode === 'search' && <td className={styles.nowrap}>{r.match_count ?? 0}</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
