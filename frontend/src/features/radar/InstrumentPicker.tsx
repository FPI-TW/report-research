import { useEffect, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { MARKET_ORDER, marketLabel } from '../../lib/meta'
import type { Market } from '../../lib/radarSchemas'
import { InstrumentCard } from './InstrumentCard'
import { fmtDate } from './radarFormat'
import { useRadarInstruments } from './useRadar'
import styles from './InstrumentPicker.module.css'

const MARKETS: ReadonlyArray<{ code: Market | ''; label: string }> = [
  { code: '', label: '全部' },
  ...MARKET_ORDER.map(code => ({ code, label: marketLabel(code) })),
]

interface Props {
  market?: Market
  onSelect: (market: Market, code: string) => void
  onMarketChange: (market: Market | '') => void
}

export function InstrumentPicker({ market, onSelect, onMarketChange }: Props) {
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250)
    return () => clearTimeout(t)
  }, [q])

  const query = useRadarInstruments({ market, q: debounced })
  const items = query.data?.items ?? []
  const total = query.data?.total
  const latest = items[0]?.latest_report_date

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
            <b>{latest ? fmtDate(latest) : '—'}</b>
            <span>最新研報</span>
          </div>
        </div>
      </div>

      <label className={styles.search}>
        <Icon name="search" size={18} className={styles.searchIcon} />
        <input
          className={styles.input}
          type="search"
          placeholder="搜尋代碼或名稱…"
          value={q}
          maxLength={64}
          onChange={e => setQ(e.target.value)}
          aria-label="搜尋標的"
        />
      </label>

      <div className={styles.filterrow}>
        <div className={styles.markets} role="group" aria-label="市場">
          {MARKETS.map(m => (
            <Pressable
              key={m.code || 'all'}
              type="button"
              className={`${styles.chip} ${(market || '') === m.code ? styles.chipActive : ''}`}
              aria-pressed={(market || '') === m.code}
              onClick={() => onMarketChange(m.code)}
            >
              {m.label}
            </Pressable>
          ))}
        </div>
        {total != null ? (
          <span className={styles.count}>
            {/* total 是符合篩選的全部筆數，items 受 useRadar 的 limit 截斷且無分頁：
                兩者不等時要如實顯示，否則標籤會宣稱格內有它沒有的卡片。 */}
            顯示 {items.length < total ? `${items.length} / ${total}` : total} 檔
          </span>
        ) : null}
      </div>

      {query.isLoading ? (
        <div className={styles.grid} aria-busy="true" data-testid="picker-skeleton">
          {Array.from({ length: 6 }, (_, i) => <div key={i} className={styles.skel} />)}
        </div>
      ) : query.isError && !query.data ? (
        <div className={styles.error} role="alert">
          載入標的清單失敗。
          <Pressable type="button" className={styles.retry} onClick={() => query.refetch()}>重試</Pressable>
        </div>
      ) : !items.length ? (
        <div className={styles.empty}>尚無可展示訊號的標的。請先完成訊號擷取，或調整搜尋條件。</div>
      ) : (
        <>
          <div className={styles.grid}>
            {items.map(item => (
              <InstrumentCard
                key={`${item.market}:${item.instrument_code}`}
                item={item}
                onSelect={onSelect}
              />
            ))}
          </div>
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
        </>
      )}
    </div>
  )
}
