import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader } from '@mantine/core'
import { getStats } from './api'
import type { StatsResponse } from './schemas'
import { useSearchParamsState } from './hooks/useSearchParamsState'
import { useSearchResults } from './hooks/useSearchResults'
import { groupViewMode } from './lib/grouping'
import { DEFAULT_FILTERS, type Allowlists } from './lib/filters'
import { SearchBar } from './components/SearchBar'
import { FilterSidebar } from './components/FilterSidebar'
import { ResultsMeta } from './components/ResultsMeta'
import { ResultsView } from './components/ResultsView'
import { LoadMore } from './components/LoadMore'
import { ReportDetailModal } from './components/ReportDetailModal'
import { EmptyState, ErrorState } from './components/states'
import styles from './SearchPage.module.css'

// ── SearchPage ────────────────────────────────────────────────────────────────
// Route-level component. Gates on stats load before rendering the inner shell
// so that child hooks always have a valid allowlist.
export default function SearchPage() {
  const statsQuery = useQuery({ queryKey: ['stats'], queryFn: getStats })

  if (statsQuery.isLoading) {
    return (
      <div className={styles.loadingWrap}>
        <Loader />
      </div>
    )
  }

  if (statsQuery.isError || !statsQuery.data) {
    return <ErrorState onRetry={() => void statsQuery.refetch()} />
  }

  return <SearchPageLoaded stats={statsQuery.data} />
}

// ── SearchPageLoaded ──────────────────────────────────────────────────────────
// Inner component rendered only once stats are available.
// All search hooks live here so they never run before allowlists exist.
function SearchPageLoaded({ stats }: { stats: StatsResponse }) {
  // Memoised allowlists derived from stats — stable identity avoids URL parse churn.
  const allow: Allowlists = useMemo(
    () => ({
      markets: stats.markets.map((m) => m.market),
      instruments: stats.instrument_types.map((i) => i.type),
      types: stats.report_types.map((t) => t.type),
    }),
    [stats],
  )

  const { filters, setFilters } = useSearchParamsState(allow)

  const {
    rows,
    total,
    mode,
    isLoading,
    isBlockingError,
    loadMoreError,
    hasMore,
    fetchNextPage,
    isFetchingNextPage,
    refetch,
  } = useSearchResults(filters)

  // Phase 2a: month grouping only. groupViewMode('month', *) always returns 'grouped'.
  const group = 'month' as const
  const view = groupViewMode(group, filters.market)

  const [modalId, setModalId] = useState<string | null>(null)

  return (
    <div className={styles.page}>
      {/* ── Search bar (spans both columns) ──────────────────────────── */}
      <div className={styles.searchBarWrap}>
        <SearchBar
          value={filters.q}
          onSubmit={(q) => setFilters({ ...filters, q })}
          onClear={() => setFilters({ ...filters, q: '' })}
        />
      </div>

      {/* ── Filter sidebar ────────────────────────────────────────────── */}
      <aside className={styles.sidebar}>
        <FilterSidebar stats={stats} filters={filters} onChange={setFilters} />
      </aside>

      {/* ── Main results area ─────────────────────────────────────────── */}
      <main className={styles.main}>
        <div className={styles.meta}>
          <ResultsMeta mode={mode} q={filters.q} market={filters.market} total={total} />
        </div>

        {isBlockingError ? (
          <ErrorState onRetry={() => void refetch()} />
        ) : isLoading ? (
          <div className={styles.loadingWrap}>
            <Loader />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState onReset={() => setFilters(DEFAULT_FILTERS)} />
        ) : (
          <div className={styles.resultsWrap}>
            <ResultsView
              view={view}
              rows={rows}
              group={group}
              mode={mode}
              onOpen={setModalId}
              onPickMarket={(m) => setFilters({ ...filters, market: m })}
            />
          </div>
        )}

        <LoadMore
          hasMore={hasMore}
          loading={isFetchingNextPage}
          error={loadMoreError}
          onMore={() => void fetchNextPage()}
        />
      </main>

      {/* ── Detail modal ──────────────────────────────────────────────── */}
      <ReportDetailModal reportId={modalId} onClose={() => setModalId(null)} />
    </div>
  )
}
