import { useState, useEffect } from 'react'
import type { KeyboardEvent } from 'react'
import type { Row } from '../lib/normalize'
import { sortedRows, nextTableSort } from '../lib/tableSort'
import type { TableSort, TableSortKey } from '../lib/tableSort'
import { mLabel, mColor, tLabel, fmtDate } from './meta'
import { targetsSummary } from './targets'

interface TableViewProps {
  rows: Row[]
  mode: 'browse' | 'search'
  onOpen: (id: string) => void
}

interface ColDef {
  key: TableSortKey | null
  label: string
  sortable: boolean
}

const BASE_COLS: ColDef[] = [
  { key: 'name', label: '報告名稱', sortable: true },
  { key: 'market', label: '市場', sortable: true },
  { key: 'type', label: '類型', sortable: true },
  { key: 'date', label: '日期', sortable: true },
  { key: 'source', label: '來源', sortable: true },
  { key: null, label: '標的', sortable: false },
]

const SEARCH_COLS: ColDef[] = [
  ...BASE_COLS,
  { key: 'score', label: '相關度', sortable: true },
  { key: 'match', label: '命中', sortable: true },
]

export function TableView({ rows, mode, onOpen }: TableViewProps) {
  const [sort, setSort] = useState<TableSort>({ key: null, dir: 'asc' })

  // rows 參照變更時重置排序（對齊 vanilla 行為）
  useEffect(() => {
    setSort({ key: null, dir: 'asc' })
  }, [rows])

  const cols = mode === 'search' ? SEARCH_COLS : BASE_COLS
  const sorted = sortedRows(rows, sort)

  function handleThClick(col: ColDef) {
    if (!col.sortable || col.key == null) return
    setSort((s) => nextTableSort(s, col.key as TableSortKey))
  }

  function handleThKeyDown(e: KeyboardEvent<HTMLTableCellElement>, col: ColDef) {
    if (!col.sortable || col.key == null) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      setSort((s) => nextTableSort(s, col.key as TableSortKey))
    }
  }

  function handleRowClick(reportId: string) {
    onOpen(reportId)
  }

  function handleRowKeyDown(e: KeyboardEvent<HTMLTableRowElement>, reportId: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onOpen(reportId)
    }
  }

  function ariaSortAttr(col: ColDef): 'ascending' | 'descending' | 'none' {
    if (!col.sortable || col.key == null) return 'none'
    if (sort.key !== col.key) return 'none'
    return sort.dir === 'asc' ? 'ascending' : 'descending'
  }

  function renderCell(row: Row, col: ColDef): string {
    switch (col.key) {
      case 'name':
        return row.file_name || '—'
      case 'market':
        return mLabel(row.market ?? '')
      case 'type':
        return tLabel(row.report_type ?? '') || '—'
      case 'date':
        return fmtDate(row.report_date) ?? '—'
      case 'source':
        return row.source || '—'
      case 'score':
        return row.bestScore != null ? `${Math.round(row.bestScore * 100)}%` : '—'
      case 'match':
        return row.matchCount != null ? String(row.matchCount) : '—'
      case null:
        // 標的欄
        return targetsSummary(row)
      default:
        return '—'
    }
  }

  return (
    <table className="rtable">
      <thead>
        <tr>
          {cols.map((col) => (
            <th
              key={col.label}
              tabIndex={col.sortable ? 0 : undefined}
              aria-sort={ariaSortAttr(col)}
              onClick={() => handleThClick(col)}
              onKeyDown={(e) => handleThKeyDown(e, col)}
              style={col.sortable ? { cursor: 'pointer', userSelect: 'none' } : undefined}
            >
              {col.label}
              {col.sortable && sort.key === col.key && (
                <span aria-hidden="true">{sort.dir === 'asc' ? ' ↑' : ' ↓'}</span>
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={row.report_id}
            data-report-id={row.report_id}
            tabIndex={0}
            onClick={() => handleRowClick(row.report_id)}
            onKeyDown={(e) => handleRowKeyDown(e, row.report_id)}
            style={{ cursor: 'pointer' }}
          >
            {cols.map((col) => {
              const text = renderCell(row, col)
              if (col.key === 'market') {
                const color = mColor(row.market ?? '')
                return (
                  <td key={col.label}>
                    <span
                      style={{
                        background: color,
                        color: '#fff',
                        borderRadius: 4,
                        padding: '1px 6px',
                        fontSize: 12,
                        fontWeight: 600,
                      }}
                    >
                      {text}
                    </span>
                  </td>
                )
              }
              return <td key={col.label}>{text}</td>
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
