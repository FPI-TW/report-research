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
import { BrandLogo } from '../../components/BrandLogo'
import { ModeSwitch } from '../../components/shell/ModeSwitch'
import { SearchBar } from './SearchBar'
import { MarketChipBar } from './MarketChipBar'
import { SortMenu } from './SortMenu'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { ActiveChips } from './ActiveChips'
import { ResultsMeta } from './ResultsMeta'
import { CardsView } from './CardsView'
import { TableView } from './TableView'
import { SearchSkeleton } from './SearchSkeleton'
import { EmptyState } from './EmptyState'
import { LoadMore } from './LoadMore'
import { ViewSwitch } from './ViewSwitch'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Reveal } from '../../components/primitives/Reveal'
import { Pressable } from '../../components/primitives/Pressable'
import { GradientBackground } from '../../components/animate-ui/components/backgrounds/gradient'
import styles from './SearchPage.module.css'

/** 首頁 hero 的建議查詢（純起手式，點了即搜） */
const TRY_QUERIES = ['AI 伺服器散熱供應鏈', 'FOMC 降息路徑', '高股息 ETF 配置']

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

  // 空查詢且無任何篩選 → 品牌起始畫面（hero），下方即為最新入庫（瀏覽模式本身依日期排序）
  const showHero = mode === 'browse' && !hasAnyFilter(state)

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
      <div className={styles.inner} data-hero={showHero || undefined}>
        {showHero && (
          <Reveal className={styles.hero}>
            <div className={styles.heroBg} aria-hidden="true">
              <GradientBackground
                className={styles.heroGradient}
                transition={{ duration: 18, ease: 'easeInOut', repeat: Infinity }}
              />
            </div>
            <BrandLogo size={64} />
            <h2 className={styles.heroTitle}>廷豐智能研報</h2>
            <p className={styles.heroSub}>
              {stats.data ? `收錄 ${stats.data.total_reports.toLocaleString()} 篇券商研報 — ` : ''}
              語意檢索 · 智能問答 · 深度研報
            </p>
            <ModeSwitch className={styles.heroSwitch} />
          </Reveal>
        )}

        <div className={styles.controls}>
          <div className={styles.searchRow}>
            <SearchBar initial={state.q} onSubmit={q => update({ q })} size={showHero ? 'lg' : 'md'} />
            {!showHero && (
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
          {showHero && (
            <div className={styles.tryRow}>
              {TRY_QUERIES.map(q => (
                <Pressable key={q} className={styles.tryChip} onClick={() => update({ q })}>{q}</Pressable>
              ))}
            </div>
          )}
          <MarketChipBar value={state.market} onChange={m => update({ market: m })} counts={marketCounts} />
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
        ) : (
          <>
            {showHero ? (
              <div className={styles.sectionHead}>
                <span className={styles.sectionTitle}>最新入庫</span>
                <span className={styles.sectionMeta}>{resultsMetaText(state, results.total)}</span>
              </div>
            ) : (
              <ResultsMeta text={resultsMetaText(state, results.total)} />
            )}
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
      </div>

      <ReportDetailModal
        reportId={openReport?.id ?? null}
        fileName={openReport?.fileName}
        onClose={() => setOpenReport(null)}
      />
    </div>
  )
}
