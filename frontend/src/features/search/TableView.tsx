import { Link, useNavigate } from 'react-router'
import { marketLabel, marketTint, reportTypeLabel } from '../../lib/meta'
import { sortRows, type TableSort, type TableSortKey } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import { reportHref } from '../report/readingFormat'
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
}

export function TableView({ rows, mode, sort, onSort }: Props) {
  const navigate = useNavigate()
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
            const href = reportHref(r.file_hash, r.passages?.[0]?.chunk_index)
            return (
              // <tr> 不能是連結，故整列仍以 navigate 開啟；報告名稱另包真 <Link>，
              // 讓 cmd+click／中鍵／複製連結在表格檢視同樣拿得回來。
              <tr
                key={r.report_id}
                className={styles.row}
                tabIndex={0}
                onClick={e => {
                  // 名稱 Link 已處理（左鍵時它會 preventDefault）→ 不重複導覽；
                  // 帶輔助鍵時交給瀏覽器開新分頁，整列不得再導一次。
                  if (e.defaultPrevented) return
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
                  navigate(href)
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(href) }
                }}
              >
                <td className={styles.name}>
                  <Link className={styles.nameLink} to={href}>{r.file_name}</Link>
                </td>
                <td>
                  <span className={styles.badge} style={marketTint(r.market ?? '')}>
                    {marketLabel(r.market ?? '')}
                  </span>
                </td>
                <td>{reportTypeLabel(r.report_type ?? '')}</td>
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
