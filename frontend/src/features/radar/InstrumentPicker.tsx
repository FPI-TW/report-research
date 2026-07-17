import { useEffect, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { InstrumentCard } from './InstrumentCard'
import { fmtDate } from './radarFormat'
import { useRadarInstruments } from './useRadar'
import styles from './InstrumentPicker.module.css'

const MARKETS = [
  { code: '', label: '全部' },
  { code: 'TW', label: '台股' },
  { code: 'US', label: '美股' },
  { code: 'HK', label: '港股' },
  { code: 'CN', label: '陸股' },
]

interface Props {
  market?: string
  onSelect: (market: string, code: string) => void
  onMarketChange: (market: string) => void
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
          <h1 className={styles.title}>廷豐觀點</h1>
          <p className={styles.lede}>券商觀點一眼掌握</p>
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
          onChange={e => setQ(e.target.value)}
          aria-label="搜尋標的"
        />
      </label>

      <div className={styles.filterrow}>
        <div className={styles.markets} role="group" aria-label="市場">
          {MARKETS.map(m => (
            <button
              key={m.code || 'all'}
              type="button"
              className={`${styles.chip} ${(market || '') === m.code ? styles.chipActive : ''}`}
              onClick={() => onMarketChange(m.code)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {total != null ? <span className={styles.count}>顯示 {total} 檔</span> : null}
      </div>

      {query.isLoading ? (
        <div className={styles.grid} aria-busy="true" data-testid="picker-skeleton">
          {Array.from({ length: 6 }, (_, i) => <div key={i} className={styles.skel} />)}
        </div>
      ) : query.isError ? (
        <div className={styles.error} role="alert">
          載入標的清單失敗。
          <button type="button" className={styles.retry} onClick={() => query.refetch()}>重試</button>
        </div>
      ) : !items.length ? (
        <div className={styles.empty}>尚無可展示訊號的標的。請先完成訊號擷取，或調整搜尋條件。</div>
      ) : (
        <div className={styles.grid}>
          {items.map(item => (
            <InstrumentCard
              key={`${item.market}:${item.instrument_code}`}
              item={item}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}
