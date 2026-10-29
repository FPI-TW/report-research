import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useStats } from '../../lib/useStats'
import { useSearchResults } from '../../lib/useSearchResults'
import {
  parseParams, buildParams, modeOf, hasAnyFilter, browseAll, type SearchState,
} from '../../lib/searchFilters'
import { normalizeSort } from '../../lib/sortForMode'
import { queryTerms } from '../../lib/terms'
import { latestId } from '../../lib/isLatest'
import { MARKET_ORDER } from '../../lib/meta'
import type { ReportRow } from '../../lib/schemas'
import type { TableSort, TableSortKey } from '../../lib/tableSort'
import { SearchBar } from './SearchBar'
import { MarketChipBar } from './MarketChipBar'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { ActiveChips } from './ActiveChips'
import { LibraryHome } from './LibraryHome'
import { HomeSkeleton, ListSkeleton } from './LibrarySkeleton'
import { ResultsSection } from './ResultsSection'
import { ReportPreview } from './ReportPreview'
import { StateBox } from './StateBox'
import styles from './SearchPage.module.css'

/** 按 Enter 搜尋後骨架至少顯示這麼久（交接包：0.65 秒），快取命中時也看得出「有在找」。 */
export const SEARCH_FLASH_MS = 650

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  // URL 為狀態真相；初次載入亦將 sort 過 normalizeSort，避免書籤 /library?sort=relevance（無 q）
  // 讓 browse 態殘留不合法排序（顯示與後端雖已各自回退，仍以狀態一致為準）。
  const state = useMemo(() => {
    const s = parseParams(params.toString())
    return { ...s, sort: normalizeSort(modeOf(s), s.sort) }
  }, [params])
  const mode = modeOf(state)
  const [tableSort, setTableSort] = useState<TableSort>({ key: 'date', dir: 'desc' })
  const [preview, setPreview] = useState<ReportRow | null>(null)

  const stats = useStats()
  const results = useSearchResults(state)
  const terms = useMemo(() => queryTerms(state.q), [state.q])
  const latest = useMemo(() => latestId(results.rows), [results.rows])

  // 骨架閃現：Enter 送出與「重試」都先顯示骨架，至少 SEARCH_FLASH_MS
  const [flashing, setFlashing] = useState(false)
  const flashTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(flashTimer.current), [])
  function flash() {
    window.clearTimeout(flashTimer.current)
    setFlashing(true)
    flashTimer.current = window.setTimeout(() => setFlashing(false), SEARCH_FLASH_MS)
  }

  // 空查詢、無篩選、未按「查看全部」→ 首頁 bento
  const showHome = mode === 'browse' && !hasAnyFilter(state) && !state.all

  function applyState(next: SearchState) {
    setParams(buildParams({ ...next, sort: normalizeSort(modeOf(next), next.sort) }))
  }
  const update = (patch: Partial<SearchState>) => applyState({ ...state, ...patch })

  function onSubmit(q: string) {
    if (q) flash()
    update({ q })
  }
  function retry() {
    flash()
    results.refetch()
  }

  /** 瀏覽時的市場計數來自全語料（/api/stats，與查詢無關）。 */
  const corpusCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const it of stats.data?.markets ?? []) if (it.market) m[it.market] = it.count
    if (stats.data) m.ALL = stats.data.total_reports
    return m
  }, [stats.data])

  /** 搜尋時的市場計數來自命中集合（/api/search 分面）。 */
  const hitCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const f of results.facets) m[f.market] = f.count
    m.ALL = results.total
    return m
  }, [results.facets, results.total])

  // 膠囊計數三條規則，每一步都不說謊：
  //   搜尋且未選市場 → 分面涵蓋全部命中，顯示真實命中數，並隱藏零命中的市場；
  //   搜尋且已選市場 → 分面只含該市場，其他市場的命中數無從得知，全列出但不顯示計數；
  //   瀏覽 → 全語料庫計數。
  const searchAllMarkets = mode === 'search' && state.market === 'ALL'
  const chipCounts = mode === 'search' ? (searchAllMarkets && !results.isLoading ? hitCounts : undefined) : corpusCounts
  const chipCodes = searchAllMarkets && !results.isLoading
    ? MARKET_ORDER.filter(c => (hitCounts[c] ?? 0) > 0)
    : undefined

  const instrumentOptions = (stats.data?.instrument_types ?? []).map(t => t.type)
  const reportTypeOptions = (stats.data?.report_types ?? []).map(t => t.type)

  function onTableSort(key: TableSortKey) {
    setTableSort(s => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }

  const busy = flashing || results.isLoading || (results.isError && results.isFetching)

  function body() {
    if (busy) return showHome ? <HomeSkeleton /> : <ListSkeleton />
    if (results.isError) {
      return (
        <StateBox
          kind="error"
          className={styles.stateTop}
          title="研報載入失敗"
          description="連線逾時，請稍後再試。"
          action="重試"
          onAction={retry}
        />
      )
    }
    if (showHome && results.rows.length > 0) {
      return (
        <LibraryHome
          rows={results.rows}
          latestId={latest}
          totalReports={stats.data?.total_reports ?? results.total}
          onPreview={setPreview}
          onSeeAll={() => update({ all: true })}
        />
      )
    }
    return (
      <ResultsSection
        mode={mode}
        rows={results.rows}
        total={results.total}
        terms={terms}
        view={state.view}
        onView={v => update({ view: v })}
        tableSort={tableSort}
        onTableSort={onTableSort}
        hasMore={results.hasMore}
        remaining={results.remaining}
        loadingMore={results.isFetchingMore}
        onLoadMore={results.loadMore}
        onPreview={setPreview}
        onClear={() => applyState(browseAll(state))}
        filters={(
          <MoreFiltersPopover
            state={state}
            instrumentOptions={instrumentOptions}
            reportTypeOptions={reportTypeOptions}
            onApply={update}
          />
        )}
      />
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.pad}>
        <SearchBar initial={state.q} onSubmit={onSubmit} />
        <div className={styles.filters}>
          <MarketChipBar
            value={state.market}
            onChange={m => update({ market: m })}
            counts={chipCounts}
            codes={chipCodes}
          />
        </div>
        <ActiveChips state={state} onPatch={update} />
        {body()}
      </div>
      <ReportPreview row={preview} onClose={() => setPreview(null)} />
    </div>
  )
}
