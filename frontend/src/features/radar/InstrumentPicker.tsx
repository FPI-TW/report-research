import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { useEnabledMarkets } from '../../lib/marketsConfig'
import { marketLabel } from '../../lib/meta'
import type { CatalogSort, Market, StanceFilter } from '../../lib/radarSchemas'
import { SORT_LABEL, SORT_OPTIONS, STANCE_DISPLAY, STANCE_OPTIONS } from './catalogState'
import { InstrumentTable } from './InstrumentTable'
import { RadarLoadError, StateBox } from './RadarStates'
import { Segmented, type SegmentedOption } from './Segmented'
import { useRadarInstruments } from './useRadar'
import shared from './shared.module.css'
import styles from './InstrumentPicker.module.css'
import { t } from '@/i18n'


/** 「全部」在分段控制裡用 `all` 表示，進出時與網址的 null（不篩選）互換。 */
const stanceSegments = (): ReadonlyArray<SegmentedOption<StanceFilter | 'all'>> => STANCE_OPTIONS.map(
  o => ({ value: o.value || 'all', label: o.label }),
)

interface Props {
  market?: Market
  q: string
  sort: CatalogSort
  stance: StanceFilter | null
  onQueryChange: (q: string) => void
  onSortChange: (sort: CatalogSort) => void
  onStanceChange: (stance: StanceFilter | null) => void
  onMarketChange: (market: Market | '') => void
  /** 一次清掉搜尋、市場、立場。必須是單一次網址更新：連續呼叫三支各自的 setter 只有最後一支生效。 */
  onClearFilters: () => void
  hrefFor: (market: Market, code: string) => string
}

export function InstrumentPicker({
  market, q, sort, stance,
  onQueryChange, onSortChange, onStanceChange, onMarketChange, onClearFilters, hrefFor,
}: Props) {
  // 輸入框自帶草稿值，只在 debounce／Enter 後才推回網址：每敲一個字就改網址會讓網址列與
  // 查詢鍵一起抖動。刻意不做網址→草稿的反向同步：那需要一個 effect setState，失效方式是
  // 「打字打到一半被蓋掉」。
  const [draft, setDraft] = useState(q)
  // 市場選單＝profile 啟用的子集（顯示順序、名稱都來自 profile）；名稱在 render 時才取，覆寫抓到後跟著換。
  const enabled = useEnabledMarkets()
  const markets: ReadonlyArray<{ code: Market | ''; label: string }> = [
    { code: '', label: t('radar.instrumentPicker.all') },
    ...(enabled as readonly Market[]).map(code => ({ code, label: marketLabel(code) })),
  ]
  const composing = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const inputRef = useRef<HTMLInputElement>(null)

  const commit = useCallback((value: string) => {
    clearTimeout(timer.current)
    onQueryChange(value)
  }, [onQueryChange])

  const schedule = useCallback((value: string) => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => onQueryChange(value), 250)
  }, [onQueryChange])

  useEffect(() => () => clearTimeout(timer.current), [])

  const query = useRadarInstruments({ market, q, sort, stance: stance ?? undefined })
  const items = query.data?.items ?? []
  const total = query.data?.total
  const showSkeleton = query.isLoading && !query.data
  const loadFailed = query.isError && !query.data
  const hasFilter = Boolean(q || market || stance)

  function clearQuery() {
    setDraft('')
    commit('')
    inputRef.current?.focus()
  }

  function clearFilters() {
    clearTimeout(timer.current)
    setDraft('')
    onClearFilters()
  }

  const emptyText = q.trim()
    ? t('radar.instrumentPicker.noMatchForTryAnother', { trim: q.trim() })
    : t('radar.instrumentPicker.tryAnotherMarketOrStance')

  return (
    <div className={styles.wrap}>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>{t('radar.instrumentPicker.consensus')}</h1>
        {/* 載入中或失敗時不顯示數字：那時沒有一個說得出口的數字。 */}
        {total != null && !loadFailed ? (
          <span className={styles.count}><span className={shared.num}>{total}</span>{' '}{t('radar.instrumentPicker.instruments')}</span>
        ) : null}
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <Search size={16} strokeWidth={1.5} className={styles.searchIcon} aria-hidden="true" />
          <input
            ref={inputRef}
            className={styles.input}
            type="search"
            placeholder={t('radar.instrumentPicker.instrumentNameOrTicker')}
            aria-label={t('radar.instrumentPicker.searchInstruments')}
            value={draft}
            maxLength={64}
            onChange={e => {
              setDraft(e.target.value)
              // 組字中不排程：注音、拼音的中間狀態送出去只會查到亂碼。
              if (!composing.current) schedule(e.target.value)
            }}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={e => {
              composing.current = false
              schedule(e.currentTarget.value)
            }}
            onKeyDown={e => {
              // React 19 的 `e.isComposing` 恆為 undefined，必須讀 nativeEvent。
              if (e.nativeEvent.isComposing) return
              if (e.key === 'Enter') {
                e.preventDefault()
                commit(e.currentTarget.value)
              } else if (e.key === 'Escape' && draft) {
                e.preventDefault()
                clearQuery()
              }
            }}
          />
          {draft ? (
            <button type="button" className={styles.clear} aria-label={t('radar.instrumentPicker.clearSearch')} onClick={clearQuery}>
              <X size={14} strokeWidth={1.8} aria-hidden="true" />
            </button>
          ) : null}
        </div>

        <Segmented<StanceFilter | 'all'>
          label={t('radar.instrumentPicker.stance')}
          options={stanceSegments()}
          value={stance ?? 'all'}
          onChange={v => onStanceChange(v === 'all' ? null : v)}
          itemWidth={60}
        />

        <label className={styles.sort}>
          {t('radar.instrumentPicker.sort')}
          <span className={styles.sortPill}>
            {SORT_LABEL[sort]}
            <ChevronDown size={14} strokeWidth={1.8} className={styles.sortIcon} aria-hidden="true" />
            <select
              className={styles.sortSelect}
              aria-label={t('radar.instrumentPicker.sort')}
              value={sort}
              onChange={e => onSortChange(e.target.value as CatalogSort)}
            >
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </span>
        </label>
      </div>

      <div className={styles.markets} role="group" aria-label={t('radar.instrumentPicker.market')}>
        {markets.map(m => {
          const active = (market || '') === m.code
          return (
            <button
              key={m.code || 'all'}
              type="button"
              className={styles.chip}
              data-on={active || undefined}
              aria-pressed={active}
              onClick={() => onMarketChange(m.code)}
            >
              {m.label}
            </button>
          )
        })}
      </div>

      {/* 篩選、排序後表格整批換掉但焦點沒動，對讀屏是無聲的。 */}
      <p className={shared.srOnly} role="status">
        {total != null
          ? t('radar.instrumentPicker.sortedByInstruments', { SORT_LABEL: SORT_LABEL[sort], total })
            + (stance ? t('radar.instrumentPicker.showingOnly', { STANCE_DISPLAY: STANCE_DISPLAY[stance] }) : '')
          : ''}
      </p>

      <InstrumentTable
        items={items}
        sort={sort}
        hrefFor={hrefFor}
        loading={showSkeleton}
        fallback={
          loadFailed ? (
            <RadarLoadError error={query.error} onRetry={() => void query.refetch()} />
          ) : !showSkeleton && !items.length ? (
            hasFilter ? (
              <StateBox icon="empty" title={t('radar.instrumentPicker.noMatchingInstruments')} text={emptyText}>
                <button type="button" className={shared.btn} onClick={clearFilters}>{t('radar.instrumentPicker.clearFilters')}</button>
              </StateBox>
            ) : (
              <StateBox icon="empty" title={t('radar.instrumentPicker.noInstrumentsToCompileYet')} />
            )
          ) : undefined
        }
      />

      {query.isFetchNextPageError ? (
        <div className={styles.more} role="alert">
          <span className={styles.moreError}>{t('radar.instrumentPicker.couldnTLoadMoreInstruments')}</span>
          <button type="button" className={shared.btn} onClick={() => void query.loadMore()}>{t('radar.instrumentPicker.retry')}</button>
        </div>
      ) : query.hasMore ? (
        <div className={styles.more}>
          <button
            type="button"
            className={shared.btn}
            disabled={query.isFetchingMore}
            onClick={() => void query.loadMore()}
          >
            {query.isFetchingMore ? t('radar.instrumentPicker.loading') : t('radar.instrumentPicker.loadMoreRemaining', { remaining: query.remaining })}
          </button>
        </div>
      ) : null}
    </div>
  )
}
