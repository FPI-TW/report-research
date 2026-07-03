import { monthGroups } from '../../lib/grouping'
import { MonthGroup } from './MonthGroup'
import { ResultCard } from './ResultCard'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './CardsView.module.css'

interface Props {
  rows: ReportRow[]
  mode: SearchMode
  terms: string[]
  latestId: string | null
  onOpen: (id: string, fileName: string) => void
}

export function CardsView({ rows, mode, terms, latestId, onOpen }: Props) {
  return (
    <div>
      {monthGroups(rows).map(g => (
        <MonthGroup key={g.key} title={g.title} count={g.count}>
          <div className={styles.grid}>
            {g.items.map(r => (
              <ResultCard
                key={r.report_id}
                row={r}
                mode={mode}
                terms={terms}
                isLatest={r.report_id === latestId}
                onOpen={onOpen}
              />
            ))}
          </div>
        </MonthGroup>
      ))}
    </div>
  )
}
