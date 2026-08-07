import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { Reveal } from '../../components/primitives/Reveal'
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import type { CatalogSort, Market, StanceFilter } from '../../lib/radarSchemas'
import {
  CATALOG_WINDOW_DAYS, SORT_LABEL, SORT_OPTIONS, STANCE_DISPLAY, STANCE_OPTIONS,
} from './catalogState'
import { InstrumentTable } from './InstrumentTable'
import { BUCKET_DISPLAY, BUCKET_MEMBERS, BUCKET_ORDER, fmtDate, RATING_DISPLAY } from './radarFormat'
import { SelectPill } from './SelectPill'
import { useRadarInstruments } from './useRadar'
import styles from './InstrumentPicker.module.css'

const MARKETS: ReadonlyArray<{ code: Market | ''; label: string }> = [
  { code: '', label: '全部' },
  ...MARKET_ORDER.map(code => ({ code, label: marketLabel(code) })),
]

interface Props {
  market?: Market
  q: string
  sort: CatalogSort
  stance: StanceFilter | null
  onQueryChange: (q: string) => void
  onSortChange: (sort: CatalogSort) => void
  onStanceChange: (stance: StanceFilter | null) => void
  onMarketChange: (market: Market | '') => void
  hrefFor: (market: Market, code: string) => string
}

export function InstrumentPicker({
  market, q, sort, stance,
  onQueryChange, onSortChange, onStanceChange, onMarketChange, hrefFor,
}: Props) {
  // 輸入框自帶草稿值，只在 debounce／Enter 後才推回網址：每敲一個字就改網址會讓
  // 網址列與 react-query 的 key 一起抖動。刻意**不做**網址→草稿的反向同步，
  // 那需要一個 effect setState，而它的失效方式是「使用者打字打到一半被蓋掉」。
  const [draft, setDraft] = useState(q)
  const composing = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

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
  // `facets` 走 `?? {}`：schema 宣告成 optional（滾動部署時舊後端還沒回這個欄位），
  // 而測試會直接 mock 掉 radarApi，連 zod 都不經過。
  const facets = useMemo(() => query.data?.facets ?? {}, [query.data])
  const latest = query.data?.latest_report_date

  // 「全部」那顆的數字是各市場相加，**不是 `total`**：帶立場篩選時 `total` 已被市場條件
  // 濾過（後端在 Python 裡先算 facets 再套 market），兩者不相等。
  const facetTotal = useMemo(
    () => Object.values(facets).reduce((s, n) => s + n, 0),
    [facets],
  )
  const hasFacets = Object.keys(facets).length > 0

  const hasFilter = Boolean(q || market || stance)
  const showSkeleton = query.isLoading && !query.data
  const loadFailed = query.isError && !query.data

  function clearFilters() {
    setDraft('')
    commit('')
    onMarketChange('')
    onStanceChange(null)
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.mast}>
        <div>
          <h1 className={styles.title}>券商觀點</h1>
          <p className={styles.lede}>彙整同一標的的各券商評等、目標價與論點，追蹤一段期間內的變化。</p>
        </div>
        <div className={styles.statrail}>
          <div className={styles.stat}>
            <b>{total != null ? total : '—'}</b>
            <span>檔標的</span>
          </div>
          <div className={styles.stat}>
            {/* 整個篩選結果的最新日期（後端 `latest_report_date`），不是第一列的日期
                ——後者只有在依「最新研報」排序時才碰巧相等。 */}
            <b>{latest ? fmtDate(latest) : '—'}</b>
            <span>最新更新</span>
          </div>
        </div>
      </div>

      <label className={styles.search}>
        <Icon name="search" size={18} className={styles.searchIcon} />
        <input
          className={styles.input}
          type="search"
          placeholder="搜尋代碼或名稱…"
          value={draft}
          maxLength={64}
          onChange={e => {
            setDraft(e.target.value)
            // 組字中不排程：注音／拼音的中間狀態送出去只會查到亂碼。
            if (!composing.current) schedule(e.target.value)
          }}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={e => {
            composing.current = false
            schedule(e.currentTarget.value)
          }}
          onKeyDown={e => {
            // React 19 的 `e.isComposing` 恆為 undefined，必須讀 nativeEvent。
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              commit(e.currentTarget.value)
            }
          }}
          aria-label="搜尋標的"
        />
        <kbd className={styles.kbd} aria-hidden="true">Enter</kbd>
      </label>

      <div className={styles.filterrow}>
        <div className={styles.markets} role="group" aria-label="市場">
          {MARKETS.map(m => {
            const active = (market || '') === m.code
            const n = m.code === '' ? (hasFacets ? facetTotal : null) : facets[m.code] ?? null
            return (
              <Pressable
                key={m.code || 'all'}
                type="button"
                className={`${styles.chip} ${active ? styles.chipActive : ''}`}
                aria-pressed={active}
                // 顯式 aria-label 蓋掉內容：把筆數串進可及名稱會讓「台股」變成「台股 12」，
                // 而那個名稱是使用者與測試共同的定位依據。
                aria-label={m.label}
                onClick={() => onMarketChange(m.code)}
              >
                {m.label}
                {/* 沒有筆數就只印市場名稱——不印 0。facets 未含某個市場代表「這個篩選下
                    沒有標的」，而 0 與「還沒算出來」在畫面上長得一模一樣。 */}
                {n != null ? <span className={styles.chipCount} aria-hidden="true">{n}</span> : null}
              </Pressable>
            )
          })}
        </div>

        <div className={styles.controls}>
          <SelectPill
            kicker="排序"
            value={sort}
            options={SORT_OPTIONS}
            onChange={onSortChange}
          />
          <SelectPill
            kicker="篩選"
            value={stance ?? ''}
            options={STANCE_OPTIONS}
            onChange={v => onStanceChange(v || null)}
          />
          {total != null ? (
            <span className={styles.count}>
              {/* total 是符合篩選的全部筆數，items 受 useRadar 的 limit 截斷且無分頁：
                  兩者不等時要如實顯示，否則標籤會宣稱格內有它沒有的列。 */}
              顯示 {items.length < total ? `${items.length} / ${total}` : total} 檔
            </span>
          ) : null}
        </div>
      </div>

      {/* 篩選與排序後的結果變化對讀屏是無聲的（表格內容整批換掉，但焦點沒動）。 */}
      <p className={styles.srOnly} role="status">
        {total != null
          ? `依${SORT_LABEL[sort]}排序，共 ${total} 檔`
            + (stance ? `，僅顯示${STANCE_DISPLAY[stance]}` : '')
          : ''}
      </p>

      <Reveal variant="fade">
        <InstrumentTable
          items={items}
          sort={sort}
          hrefFor={hrefFor}
          loading={showSkeleton}
          fallback={
            loadFailed ? (
              <span role="alert">
                載入標的清單失敗。
                <Pressable type="button" className={styles.retry} onClick={() => query.refetch()}>
                  重試
                </Pressable>
              </span>
            ) : !showSkeleton && !items.length ? (
              <span role="status">
                {hasFilter
                  ? '目前的搜尋與篩選條件下沒有標的。'
                  : '尚無可展示訊號的標的。請先完成訊號擷取，或調整搜尋條件。'}
                {hasFilter ? (
                  <Pressable type="button" className={styles.retry} onClick={clearFilters}>
                    清除篩選
                  </Pressable>
                ) : null}
              </span>
            ) : undefined
          }
        />
      </Reveal>

      {query.isFetchNextPageError ? (
        <div className={styles.error} role="alert">
          載入更多標的失敗，已保留目前清單。
          <Pressable
            type="button"
            className={styles.retry}
            onClick={() => void query.loadMore()}
          >
            重試載入更多
          </Pressable>
        </div>
      ) : query.hasMore ? (
        <div className={styles.loadMoreWrap}>
          <Pressable
            type="button"
            className={styles.loadMore}
            disabled={query.isFetchingMore}
            onClick={() => void query.loadMore()}
          >
            {query.isFetchingMore
              ? '載入中…'
              : `載入更多（尚有 ${query.remaining} 檔）`}
          </Pressable>
        </div>
      ) : null}

      <div className={styles.legend}>
        <span className={styles.legendGroup}>
          <span className={styles.legendKicker}>評等分布</span>
          {BUCKET_ORDER.map(b => (
            <span key={b} className={styles.legendItem}>
              <i className={`${styles.swatch} ${styles[b]}`} aria-hidden="true" />
              {BUCKET_DISPLAY[b]}
              <span className={styles.legendNote}>
                （{BUCKET_MEMBERS[b].map(r => RATING_DISPLAY[r]).join('／')}）
              </span>
            </span>
          ))}
        </span>
        {/* 三桶加總不等於「全部」不是 bug：尚未擷取到評等的標的不屬於任何一桶。
            不說出來的話，讀者只會把差額當成算錯。 */}
        <span className={styles.legendNote}>
          共識與近期變化皆以近 {CATALOG_WINDOW_DAYS} 天為窗期；尚未擷取到評等的標的不歸入任何立場，僅在「全部」出現。
        </span>
      </div>
    </div>
  )
}
