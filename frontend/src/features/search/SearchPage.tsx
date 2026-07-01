import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader } from '@mantine/core'
import { getStats } from './api'
import type { StatsResponse } from './schemas'
import { useSearchParamsState } from './hooks/useSearchParamsState'
import { useSearchResults } from './hooks/useSearchResults'
import { groupViewMode } from './lib/grouping'
import { buildTerms } from './lib/terms'
import { DEFAULT_FILTERS, type Allowlists } from './lib/filters'
import { SearchBar } from './components/SearchBar'
import { FilterSidebar } from './components/FilterSidebar'
import { ResultsMeta } from './components/ResultsMeta'
import { ResultsView } from './components/ResultsView'
import { LoadMore } from './components/LoadMore'
import { ReportDetailModal } from './components/ReportDetailModal'
import { ViewSwitch } from './components/ViewSwitch'
import { GroupBySelect } from './components/GroupBySelect'
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

  const { filters, viewState, setFilters, setViewState } = useSearchParamsState(allow)

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

  // Top-level view: 'group' | 'table' — from viewState (URL + localStorage)
  const topView = viewState.view

  // Sub-view for conditional rendering (e.g. hide LoadMore on index)
  const subView =
    topView === 'table' ? 'table' : groupViewMode(viewState.group, filters.market)

  // Highlight terms derived from the search query
  const terms = useMemo(() => buildTerms(filters.q), [filters.q])

  // Full-corpus market counts from /api/stats (not from current page results)
  const markets = stats.markets

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
        ) : (
          <>
            {/* ── View toolbar：常駐顯示，載入中／無結果時也可先切換檢視 ──── */}
            <div className={styles.viewToolbar}>
              <ViewSwitch
                value={topView}
                onChange={(v) => setViewState({ ...viewState, view: v })}
              />
              {topView === 'group' && (
                <GroupBySelect
                  value={viewState.group}
                  onChange={(g) => setViewState({ ...viewState, group: g })}
                />
              )}
            </div>

            {isLoading ? (
              <div className={styles.loadingWrap}>
                <Loader />
              </div>
            ) : rows.length === 0 ? (
              <EmptyState onReset={() => setFilters(DEFAULT_FILTERS)} />
            ) : (
              <div className={styles.resultsWrap}>
                <ResultsView
                  view={topView}
                  group={viewState.group}
                  rows={rows}
                  mode={mode}
                  terms={terms}
                  total={total}
                  markets={markets}
                  onOpen={setModalId}
                  onPickMarket={(m) => setFilters({ ...filters, market: m })}
                  market={filters.market}
                />
              </div>
            )}
          </>
        )}

        {/* LoadMore: hidden in market-index sub-view (no pagination on index) */}
        {subView !== 'index' && (
          <LoadMore
            hasMore={hasMore}
            loading={isFetchingNextPage}
            error={loadMoreError}
            onMore={() => void fetchNextPage()}
            remaining={Math.max(0, total - rows.length)}
          />
        )}
      </main>

      {/* ── Detail modal ──────────────────────────────────────────────── */}
      <ReportDetailModal reportId={modalId} onClose={() => setModalId(null)} />
    </div>
  )
}
