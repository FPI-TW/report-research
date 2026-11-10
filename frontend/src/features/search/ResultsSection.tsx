import type { ReactNode } from 'react'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode, ViewMode } from '../../lib/searchFilters'
import type { TableSort, TableSortKey } from '../../lib/tableSort'
import { LoadMore } from './LoadMore'
import { ResultRow } from './ResultRow'
import { StateBox } from './StateBox'
import { TableView } from './TableView'
import { ViewSwitch } from './ViewSwitch'
import atoms from './atoms.module.css'
import styles from './ResultsSection.module.css'
import { t } from '@/i18n'

interface Props {
  mode: SearchMode
  rows: ReportRow[]
  total: number
  terms: string[]
  view: ViewMode
  onView: (v: ViewMode) => void
  tableSort: TableSort
  onTableSort: (key: TableSortKey) => void
  hasMore: boolean
  remaining: number
  loadingMore: boolean
  onLoadMore: () => void
  onPreview: (row: ReportRow) => void
  onClear: () => void
  /** 進階篩選鈕（更多篩選）：無結果時也留著，放寬條件要用到它。 */
  filters?: ReactNode
}

/** 結果區：「搜尋結果／最新入庫」、篇數、排序說明（無結果時不顯示），下面是結果卡片列。 */
export function ResultsSection(p: Props) {
  const empty = p.total === 0
  return (
    <section className={styles.section} aria-labelledby="library-results">
      <div className={styles.head}>
        <h2 id="library-results" className={styles.title}>{p.mode === 'search' ? t('search.resultsSection.searchResults') : t('search.resultsSection.recentlyIngested')}</h2>
        <span className={`${atoms.num} ${styles.count}`}>{t('search.resultsSection.reports', { total: p.total.toLocaleString() })}</span>
        <span className={styles.tools}>
          {!empty && <span className={styles.sort}>{p.mode === 'search' ? t('search.resultsSection.sortedByRelevance') : t('search.resultsSection.sortedByDate')}</span>}
          {p.filters}
          {!empty && <ViewSwitch view={p.view} onChange={p.onView} />}
        </span>
      </div>

      {empty ? (
        <StateBox
          kind="empty"
          level={3}
          title={t('search.resultsSection.noMatchingReports')}
          description={t('search.resultsSection.tryAnotherKeywordOrWiden')}
          action={t('search.resultsSection.clear')}
          onAction={p.onClear}
        />
      ) : p.view === 'table' ? (
        <TableView rows={p.rows} mode={p.mode} sort={p.tableSort} onSort={p.onTableSort} />
      ) : (
        <div className={`${atoms.tile} ${styles.list}`}>
          {p.rows.map((r, i) => (
            <ResultRow
              key={r.report_id}
              row={r}
              isTop={p.mode === 'search' && i === 0}
              terms={p.terms}
              index={i}
              onPreview={p.onPreview}
            />
          ))}
        </div>
      )}

      {!empty && p.hasMore && (
        <LoadMore remaining={p.remaining} loading={p.loadingMore} onClick={p.onLoadMore} />
      )}
    </section>
  )
}
