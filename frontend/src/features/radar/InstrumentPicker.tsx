import { useEffect, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { Skeleton } from '../../components/primitives/Skeleton'
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

  return (
    <div className={styles.wrap}>
      <h1 className={styles.title}>觀點雷達</h1>
      <p className={styles.sub}>
        選擇標的後，檢視跨券商評等、目標價、EPS 與四維論點共識，以及近期觀點變化。
        本區整理已擷取的研報觀點，非系統預測或投資建議。
      </p>

      <div className={styles.controls}>
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
        <div className={styles.markets} role="group" aria-label="市場">
          {MARKETS.map(m => (
            <Pressable
              key={m.code || 'all'}
              className={`${styles.chip} ${(market || '') === m.code ? styles.chipActive : ''}`}
              onClick={() => onMarketChange(m.code)}
            >
              {m.label}
            </Pressable>
          ))}
        </div>
      </div>

      {query.isLoading ? (
        <div className={styles.list} aria-busy="true" data-testid="picker-skeleton">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className={styles.item} style={{ pointerEvents: 'none' }}>
              <div className={styles.main}>
                <Skeleton width={120} height={16} radius={4} />
                <Skeleton width={180} height={12} radius={4} style={{ marginTop: 8 }} />
              </div>
            </div>
          ))}
        </div>
      ) : query.isError ? (
        <div className={styles.error} role="alert">
          載入標的清單失敗。
          <Pressable className={styles.chip} style={{ marginLeft: 8 }} onClick={() => query.refetch()}>
            重試
          </Pressable>
        </div>
      ) : !query.data?.items.length ? (
        <div className={styles.empty}>尚無可展示訊號的標的。請先完成訊號擷取，或調整搜尋條件。</div>
      ) : (
        <ul className={styles.list}>
          {query.data.items.map(item => (
            <li key={`${item.market}:${item.instrument_code}`}>
              <Pressable
                className={styles.item}
                onClick={() => onSelect(item.market, item.instrument_code)}
              >
                <div className={styles.main}>
                  <div className={styles.name}>
                    <span>{item.instrument_name || item.instrument_code}</span>
                    <span className={styles.code}>{item.instrument_code}</span>
                  </div>
                  <div className={styles.meta}>
                    {item.broker_count} 家券商 · {item.report_count} 份研報
                    {item.latest_report_date ? ` · 最新 ${fmtDate(item.latest_report_date)}` : ''}
                  </div>
                </div>
                <span className={styles.mkt}>{item.market_display || item.market}</span>
                <Icon name="chevronDown" size={16} className={styles.chevron} />
              </Pressable>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
