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
}

export function CardsView({ rows, mode, terms, latestId }: Props) {
  return (
    <div className={styles.wrap}>
      {monthGroups(rows).map(g => (
        <MonthGroup key={g.key} title={g.title} count={g.count}>
          <div className={styles.grid}>
            {g.items.map((r, i) => (
              <ResultCard
                key={r.report_id}
                row={r}
                mode={mode}
                terms={terms}
                isLatest={r.report_id === latestId}
                index={i}
              />
            ))}
          </div>
        </MonthGroup>
      ))}
    </div>
  )
}
