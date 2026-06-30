import type { Row } from '../lib/normalize'
import { groupRows } from '../lib/grouping'
import { mLabel } from './meta'
import { ResultCard } from './ResultCard'

interface GroupedListProps {
  rows: Row[]
  group: 'month' | 'market'
  mode: 'browse' | 'search'
  onOpen: (id: string) => void
}

function groupLabel(key: string, group: 'month' | 'market'): string {
  if (key === '') return '未分類'
  if (group === 'market') return mLabel(key)
  // month: 'YYYY-MM' -> '${yyyy} 年 ${mm} 月'
  const parts = key.split('-')
  if (parts.length === 2) {
    const [yyyy, mm] = parts
    return `${yyyy} 年 ${+mm} 月`
  }
  return key
}

export function GroupedList({ rows, group, mode, onOpen }: GroupedListProps) {
  const groups = groupRows(rows, group)

  // Sort: month desc by key, market by group size desc
  const sorted =
    group === 'month'
      ? [...groups].sort((a, b) => String(b.key).localeCompare(String(a.key)))
      : [...groups].sort((a, b) => b.rows.length - a.rows.length)

  return (
    <div data-testid="grouped-list">
      {sorted.map(({ key, rows: groupedRows }) => (
        <section key={key || '__empty__'} data-testid="group-section">
          <div
            data-testid="group-header"
            style={{
              padding: '6px 12px',
              fontWeight: 600,
              fontSize: 13,
              background: '#f8f9fa',
              borderBottom: '1px solid #e9ecef',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span>{groupLabel(key, group)}</span>
            <span style={{ fontWeight: 400, color: '#868e96', fontSize: 12 }}>
              {groupedRows.length}
            </span>
          </div>
          <div>
            {groupedRows.map((row) => (
              <ResultCard key={row.report_id} row={row} mode={mode} onOpen={onOpen} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
