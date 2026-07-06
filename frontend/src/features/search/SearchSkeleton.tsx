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

function CardGridSkeleton({ mode, cards }: { mode: SearchMode; cards: number }) {
  return (
    <section>
      <div className={styles.monthHeader}>
        <Skeleton width={116} height={30} radius="var(--tf-radius-pill)" />
      </div>
      <div className={styles.grid}>
        {Array.from({ length: cards }, (_, i) => <CardSkeleton key={i} mode={mode} />)}
      </div>
    </section>
  )
}

function CardSkeleton({ mode }: { mode: SearchMode }) {
  return (
    <div className={styles.card} data-testid="skeleton-card">
      <div className={styles.top}>
        <Skeleton width={44} height={18} radius="var(--tf-radius-pill)" />
      </div>
      <Skeleton className={styles.titleLine} height={14} width="92%" />
      <Skeleton className={styles.titleLine} height={14} width="64%" />
      <div className={styles.tags}>
        <Skeleton width={52} height={20} radius="var(--tf-radius-pill)" />
        <Skeleton width={40} height={20} radius="var(--tf-radius-pill)" />
        <Skeleton width={64} height={20} radius="var(--tf-radius-pill)" />
      </div>
      {mode === 'search' ? (
        <>
          <Skeleton className={styles.line} height={12} width="100%" />
          <Skeleton className={styles.line} height={12} width="78%" />
          <div className={styles.scoreRow}>
            <Skeleton className={styles.scoreTrack} height={6} radius="var(--tf-radius-pill)" />
            <Skeleton width={52} height={11} radius={4} />
          </div>
        </>
      ) : (
        <Skeleton className={styles.line} height={13} width="85%" />
      )}
      <div className={styles.footer}>
        <Skeleton width={120} height={12} radius={4} />
        <Skeleton width={56} height={12} radius={4} />
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
