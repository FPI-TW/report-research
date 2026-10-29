import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import type { CatalogSort, Market, StanceFilter } from '../../lib/radarSchemas'
import { SORT_LABEL, SORT_OPTIONS, STANCE_DISPLAY, STANCE_OPTIONS } from './catalogState'
import { InstrumentTable } from './InstrumentTable'
import { RadarLoadError, StateBox } from './RadarStates'
import { Segmented, type SegmentedOption } from './Segmented'
import { useRadarInstruments } from './useRadar'
import shared from './shared.module.css'
import styles from './InstrumentPicker.module.css'

const MARKETS: ReadonlyArray<{ code: Market | ''; label: string }> = [
  { code: '', label: '全部' },
  ...MARKET_ORDER.map(code => ({ code, label: marketLabel(code) })),
]

/** 「全部」在分段控制裡用 `all` 表示，進出時與網址的 null（不篩選）互換。 */
const STANCE_SEGMENTS: ReadonlyArray<SegmentedOption<StanceFilter | 'all'>> = STANCE_OPTIONS.map(
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
    ? `找不到「${q.trim()}」，換個名稱或代號再試一次。`
    : '換個市場或立場再試一次。'

  return (
    <div className={styles.wrap}>
      <div className={styles.titleRow}>
        <h1 className={styles.title}>券商共識</h1>
        {/* 載入中或失敗時不顯示數字：那時沒有一個說得出口的數字。 */}
        {total != null && !loadFailed ? (
          <span className={styles.count}><span className={shared.num}>{total}</span> 檔標的</span>
        ) : null}
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <Search size={16} strokeWidth={1.5} className={styles.searchIcon} aria-hidden="true" />
          <input
            ref={inputRef}
            className={styles.input}
            type="search"
            placeholder="標的名稱或代號"
            aria-label="搜尋標的"
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
            <button type="button" className={styles.clear} aria-label="清除搜尋" onClick={clearQuery}>
              <X size={14} strokeWidth={1.8} aria-hidden="true" />
            </button>
          ) : null}
        </div>

        <Segmented<StanceFilter | 'all'>
          label="立場"
          options={STANCE_SEGMENTS}
          value={stance ?? 'all'}
          onChange={v => onStanceChange(v === 'all' ? null : v)}
          itemWidth={60}
        />

        <label className={styles.sort}>
          排序
          <span className={styles.sortPill}>
            {SORT_LABEL[sort]}
            <ChevronDown size={14} strokeWidth={1.8} className={styles.sortIcon} aria-hidden="true" />
            <select
              className={styles.sortSelect}
              aria-label="排序"
              value={sort}
              onChange={e => onSortChange(e.target.value as CatalogSort)}
            >
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </span>
        </label>
      </div>

      <div className={styles.markets} role="group" aria-label="市場">
        {MARKETS.map(m => {
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
          ? `依${SORT_LABEL[sort]}排序，共 ${total} 檔`
            + (stance ? `，僅顯示${STANCE_DISPLAY[stance]}` : '')
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
              <StateBox icon="empty" title="沒有符合的標的" text={emptyText}>
                <button type="button" className={shared.btn} onClick={clearFilters}>清除篩選</button>
              </StateBox>
            ) : (
              <StateBox icon="empty" title="還沒有可彙整的標的" />
            )
          ) : undefined
        }
      />

      {query.isFetchNextPageError ? (
        <div className={styles.more} role="alert">
          <span className={styles.moreError}>載入更多標的失敗</span>
          <button type="button" className={shared.btn} onClick={() => void query.loadMore()}>重試</button>
        </div>
      ) : query.hasMore ? (
        <div className={styles.more}>
          <button
            type="button"
            className={shared.btn}
            disabled={query.isFetchingMore}
            onClick={() => void query.loadMore()}
          >
            {query.isFetchingMore ? '載入中…' : `載入更多（還有 ${query.remaining} 檔）`}
          </button>
        </div>
      ) : null}
    </div>
  )
}
