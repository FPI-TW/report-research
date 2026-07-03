import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useStats } from '../../lib/useStats'
import { useSearchResults } from '../../lib/useSearchResults'
import {
  parseParams, buildParams, modeOf, hasAnyFilter,
  clearFilters, browseAll, type SearchState,
} from '../../lib/searchFilters'
import { normalizeSort } from '../../lib/sortForMode'
import { queryTerms } from '../../lib/terms'
import { latestId } from '../../lib/isLatest'
import { resultsMetaText, emptyState } from '../../lib/resultsMeta'
import type { TableSort, TableSortKey } from '../../lib/tableSort'
import { SearchBar } from './SearchBar'
import { MarketChipBar } from './MarketChipBar'
import { SortMenu } from './SortMenu'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { ActiveChips } from './ActiveChips'
import { ResultsMeta } from './ResultsMeta'
import { CardsView } from './CardsView'
import { TableView } from './TableView'
import { EmptyState } from './EmptyState'
import { LoadMore } from './LoadMore'
import { ViewSwitch } from './ViewSwitch'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import styles from './SearchPage.module.css'

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  // URL 為狀態真相；初次載入亦將 sort 過 normalizeSort，避免書籤 /search?sort=relevance（無 q）
  // 讓 browse 態殘留不合法排序（顯示與後端雖已各自回退，仍以狀態一致為準）。
  const state = useMemo(() => {
    const s = parseParams(params.toString())
    return { ...s, sort: normalizeSort(modeOf(s), s.sort) }
  }, [params])
  const mode = modeOf(state)
  const [tableSort, setTableSort] = useState<TableSort>({ key: 'date', dir: 'desc' })
  const [openReport, setOpenReport] = useState<{ id: string; fileName: string } | null>(null)

  const stats = useStats()
  const results = useSearchResults(state)
  const terms = useMemo(() => queryTerms(state.q), [state.q])
  const latest = useMemo(() => latestId(results.rows), [results.rows])

  function applyState(next: SearchState) {
    setParams(buildParams({ ...next, sort: normalizeSort(modeOf(next), next.sort) }))
  }
  const update = (patch: Partial<SearchState>) => applyState({ ...state, ...patch })

  const marketCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const it of stats.data?.markets ?? []) m[it.market] = it.count
    if (stats.data) m.ALL = stats.data.total_reports
    return m
  }, [stats.data])
  const instrumentOptions = (stats.data?.instrument_types ?? []).map(t => t.type)
  const reportTypeOptions = (stats.data?.report_types ?? []).map(t => t.type)

  function onSort(key: TableSortKey) {
    setTableSort(s => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }
  const onOpen = (id: string, fileName: string) => setOpenReport({ id, fileName })

  return (
    <div className={styles.page}>
      <div className={styles.controls}>
        <SearchBar initial={state.q} onSubmit={q => update({ q })} />
        <div className={styles.filterRow}>
          <MarketChipBar value={state.market} onChange={m => update({ market: m })} counts={marketCounts} />
          <div className={styles.toolbar}>
            <SortMenu mode={mode} value={state.sort} onChange={v => update({ sort: v })} />
            <MoreFiltersPopover
              state={state}
              instrumentOptions={instrumentOptions}
              reportTypeOptions={reportTypeOptions}
              onApply={update}
            />
          </div>
        </div>
        <ActiveChips state={state} onPatch={update} />
      </div>

      {results.isError ? (
        <div className={styles.error}>
          載入失敗，請稍後再試。<button type="button" onClick={results.refetch}>重試</button>
        </div>
      ) : results.isLoading ? (
        <div className={styles.loading} aria-busy="true">載入中…</div>
      ) : results.total === 0 ? (
        <EmptyState
          copy={emptyState(state, hasAnyFilter(state))}
          onClear={() => applyState(clearFilters(state))}
          onBrowseAll={() => applyState(browseAll(state))}
        />
      ) : (
        <>
          <ResultsMeta text={resultsMetaText(state, results.total)} />
          {state.view === 'table' ? (
            <TableView rows={results.rows} mode={mode} sort={tableSort} onSort={onSort} onOpen={onOpen} />
          ) : (
            <CardsView rows={results.rows} mode={mode} terms={terms} latestId={latest} onOpen={onOpen} />
          )}
          {results.hasMore && (
            <LoadMore remaining={results.remaining} loading={results.isFetchingMore} onClick={results.loadMore} />
          )}
        </>
      )}

      <ViewSwitch view={state.view} onChange={v => update({ view: v })} />
      <ReportDetailModal
        reportId={openReport?.id ?? null}
        fileName={openReport?.fileName}
        onClose={() => setOpenReport(null)}
      />
    </div>
  )
}
