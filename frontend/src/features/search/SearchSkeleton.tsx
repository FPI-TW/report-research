import { Skeleton } from '../../components/primitives/Skeleton'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './SearchSkeleton.module.css'

interface Props {
  view: 'cards' | 'table'
  mode: SearchMode
  cards?: number
  rows?: number
}

/** 檢索結果載入骨架屏，鏡射 ResultCard / TableView / MonthGroup 結構以最小化換入時的版面跳動。 */
export function SearchSkeleton({ view, mode, cards = 6, rows = 10 }: Props) {
  return (
    <div aria-hidden="true" data-testid="search-skeleton">
      {/* 保留 ResultsMeta 那一行的高度，避免結果換入時整塊上移 */}
      <Skeleton className={styles.meta} width={180} height={13} radius={4} />
      {view === 'table'
        ? <TableSkeleton mode={mode} rows={rows} />
        : <CardGridSkeleton mode={mode} cards={cards} />}
    </div>
  )
}

/* 鏡射 CardsView：單一白底容器 + 月份標頭條 + 92px/1fr/200px 接縫列，
   與實際 ResultCard 幾何一致，換入時零跳動。 */
function CardGridSkeleton({ mode, cards }: { mode: SearchMode; cards: number }) {
  return (
    <div className={styles.wrap}>
      <div className={styles.monthHeader}>
        <Skeleton width={116} height={16} radius="var(--tf-radius-pill)" />
      </div>
      {Array.from({ length: cards }, (_, i) => <CardSkeleton key={i} mode={mode} />)}
    </div>
  )
}

function CardSkeleton({ mode }: { mode: SearchMode }) {
  return (
    <div className={styles.card} data-testid="skeleton-card">
      <div className={styles.chipCol}>
        <Skeleton width={64} height={20} radius="var(--tf-radius-pill)" />
      </div>
      <div className={styles.main}>
        <Skeleton className={styles.titleLine} height={15} width="72%" />
        <Skeleton className={styles.tagLine} height={12} width="42%" />
        <Skeleton className={styles.snippetLine} height={12} width={mode === 'search' ? '90%' : '84%'} />
      </div>
      <div className={styles.right}>
        {mode === 'search' && (
          <div className={styles.scoreRow}>
            <Skeleton className={styles.scoreTrack} height={4} radius="var(--tf-radius-pill)" />
            <Skeleton width={30} height={13} radius={4} />
          </div>
        )}
        <Skeleton width={110} height={12} radius={4} />
      </div>
    </div>
  )
}

function TableSkeleton({ mode, rows }: { mode: SearchMode; rows: number }) {
  const cols = mode === 'search' ? 8 : 6
  const idx = Array.from({ length: cols }, (_, i) => i)
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {idx.map(i => (
              <th key={i}><Skeleton height={12} width={i === 0 ? 90 : 48} radius={4} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r} data-testid="skeleton-row">
              {idx.map(c => (
                <td key={c}>
                  <Skeleton height={12} width={c === 0 ? '80%' : c === 5 ? '60%' : 40} radius={4} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
