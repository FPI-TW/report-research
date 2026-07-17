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
import { MARKET_ORDER } from '../../lib/meta'
import { resultsMetaText, emptyState } from '../../lib/resultsMeta'
import type { TableSort, TableSortKey } from '../../lib/tableSort'
import { SearchBar } from './SearchBar'
import { MarketChipBar } from './MarketChipBar'
import { SortMenu } from './SortMenu'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { ActiveChips } from './ActiveChips'
import { ResultsMeta } from './ResultsMeta'
import { BentoWall, monthOf } from './BentoWall'
import { FeatureTile } from './FeatureTile'
import { HitBar } from './HitBar'
import { ResultRow } from './ResultRow'
import { CardsView } from './CardsView'
import { TableView } from './TableView'
import { SearchSkeleton } from './SearchSkeleton'
import { EmptyState } from './EmptyState'
import { LoadMore } from './LoadMore'
import { ViewSwitch } from './ViewSwitch'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Reveal } from '../../components/primitives/Reveal'
import { Pressable } from '../../components/primitives/Pressable'
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

  // 空查詢、無篩選、未按「查看全部」→ Bento 簡報牆（每日簡報面）
  const showBento = mode === 'browse' && !hasAnyFilter(state) && !state.all

  function applyState(next: SearchState) {
    setParams(buildParams({ ...next, sort: normalizeSort(modeOf(next), next.sort) }))
  }
  const update = (patch: Partial<SearchState>) => applyState({ ...state, ...patch })

  /** 全語料庫的市場計數（/api/stats，與查詢無關）。 */
  const corpusComposition = useMemo(
    () => (stats.data?.markets ?? []).filter(m => m.market),
    [stats.data],
  )
  const corpusCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const it of corpusComposition) m[it.market] = it.count
    if (stats.data) m.ALL = stats.data.total_reports
    return m
  }, [corpusComposition, stats.data])

  /** 命中集合的市場計數（/api/search 分面）。 */
  const hitCounts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const f of results.facets) m[f.market] = f.count
    m.ALL = results.total
    return m
  }, [results.facets, results.total])

  // chips 三條規則，每一步都不說謊：
  //   搜尋且未選市場 → 分面涵蓋全部命中，故顯示真實命中數，並隱藏零命中的市場；
  //   搜尋且已選市場 → 分面只含該市場，其他市場的命中數無從得知（要再跑一次未篩選檢索），
  //                    故全列出但不顯示計數，寧可不說也不編造；
  //   瀏覽 → 全語料庫計數。
  const searchAllMarkets = mode === 'search' && state.market === 'ALL'
  const chipCounts = mode === 'search' ? (searchAllMarkets ? hitCounts : undefined) : corpusCounts
  const chipCodes = searchAllMarkets
    ? MARKET_ORDER.filter(c => (hitCounts[c] ?? 0) > 0)
    : undefined

  const instrumentOptions = (stats.data?.instrument_types ?? []).map(t => t.type)
  const reportTypeOptions = (stats.data?.report_types ?? []).map(t => t.type)

  function onSort(key: TableSortKey) {
    setTableSort(s => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }
  const onOpen = (id: string, fileName: string) => setOpenReport({ id, fileName })

  const [topHit, ...restHits] = results.rows
  const showFeature = mode === 'search' && state.view !== 'table' && !!topHit

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <div className={styles.controls}>
          <div className={styles.searchRow}>
            <SearchBar initial={state.q} onSubmit={q => update({ q })} size={showBento ? 'lg' : 'md'} />
            {!showBento && (
              <div className={styles.toolbar}>
                <SortMenu mode={mode} value={state.sort} onChange={v => update({ sort: v })} />
                <MoreFiltersPopover
                  state={state}
                  instrumentOptions={instrumentOptions}
                  reportTypeOptions={reportTypeOptions}
                  onApply={update}
                />
                <ViewSwitch view={state.view} onChange={v => update({ view: v })} />
              </div>
            )}
          </div>
          <MarketChipBar
            value={state.market}
            onChange={m => update({ market: m })}
            counts={chipCounts}
            codes={chipCodes}
          />
          <ActiveChips state={state} onPatch={update} />
        </div>

        {results.isError ? (
          <Reveal className={styles.error}>
            載入失敗，請稍後再試。<Pressable onClick={results.refetch}>重試</Pressable>
          </Reveal>
        ) : results.isLoading ? (
          <SearchSkeleton view={state.view === 'table' ? 'table' : 'cards'} mode={mode} />
        ) : results.total === 0 ? (
          <Reveal>
            <EmptyState
              copy={emptyState(state, hasAnyFilter(state))}
              onClear={() => applyState(clearFilters(state))}
              onBrowseAll={() => applyState(browseAll(state))}
            />
          </Reveal>
        ) : showBento ? (
          <BentoWall
            rows={results.rows}
            latestId={latest}
            totalReports={stats.data?.total_reports ?? results.total}
            composition={corpusComposition}
            monthLabel={monthOf(results.rows[0]?.report_date ?? null)}
            onOpen={onOpen}
            onSeeAll={() => update({ all: true })}
          />
        ) : (
          <>
            {/* 色譜對查詢有反應：這裡是命中結果的市場組成，不再是全語料庫 */}
            {mode === 'search' && results.facets.length > 0 && (
              <HitBar slices={results.facets} total={results.total} />
            )}

            <div className={styles.resultsHead}>
              <span className={styles.resultsTitle}>{mode === 'search' ? '搜尋結果' : '最新入庫'}</span>
              {/* 篇數歸命中組成條獨有；此處只說排序，避免同一畫面把數字講兩次 */}
              {mode === 'search'
                ? <span className={styles.resultsSort}>依相關度排序</span>
                : <ResultsMeta text={resultsMetaText(state, results.total)} />}
            </div>

            {showFeature && (
              <FeatureTile
                className={styles.featureRow}
                row={topHit}
                mode="search"
                isLatest={topHit.report_id === latest}
                onOpen={onOpen}
              />
            )}

            {state.view === 'table' ? (
              <TableView rows={results.rows} mode={mode} sort={tableSort} onSort={onSort} onOpen={onOpen} />
            ) : mode === 'search' ? (
              <div className={styles.results}>
                {restHits.map((r, i) => (
                  <ResultRow
                    key={r.report_id}
                    row={r}
                    isLatest={r.report_id === latest}
                    terms={terms}
                    index={i}
                    onOpen={onOpen}
                  />
                ))}
              </div>
            ) : (
              // 完整清單（查看全部／已篩選的瀏覽）：時間是主軸，保留月份分組
              <CardsView rows={results.rows} mode={mode} terms={terms} latestId={latest} onOpen={onOpen} />
            )}

            {results.hasMore && (
              <LoadMore remaining={results.remaining} loading={results.isFetchingMore} onClick={results.loadMore} />
            )}
          </>
        )}
      </div>

      <ReportDetailModal
        reportId={openReport?.id ?? null}
        fileName={openReport?.fileName}
        onClose={() => setOpenReport(null)}
      />
    </div>
  )
}
