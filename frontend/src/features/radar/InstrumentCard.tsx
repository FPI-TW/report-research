import type { RadarInstrumentItem, RatingNorm } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtPrice, marketVar, RATING_BUCKET, RATING_DISPLAY } from './radarFormat'
import styles from './InstrumentCard.module.css'

const ORDER: RatingNorm[] = ['buy', 'overweight', 'neutral', 'underweight', 'sell']

interface Props {
  item: RadarInstrumentItem
  onSelect: (market: string, code: string) => void
}

export function InstrumentCard({ item, onSelect }: Props) {
  const c = item.consensus
  const stance = c?.stance
  const bucket = stance ? RATING_BUCKET[stance.rating] : 'neu'
  const net = stance?.net_rating ?? 0
  const netDir = net > 0 ? 'up' : net < 0 ? 'down' : 'flat'
  const netLabel = net > 0 ? `淨上調 ${net}` : net < 0 ? `淨下調 ${Math.abs(net)}` : '持穩'
  const counts = stance ? new Map(stance.distribution.map(d => [d.rating, d.count])) : null
  const total = stance?.total_rated ?? 0

  return (
    <button type="button" className={styles.card} onClick={() => onSelect(item.market, item.instrument_code)}>
      <div className={styles.head}>
        <div className={styles.id}>
          <span className={styles.name}>{item.instrument_name || item.instrument_code}</span>
          <span className={styles.code}>{item.instrument_code}</span>
        </div>
        <span className={styles.badge} style={marketVar(item.market)}>
          {item.market_display || item.market}
        </span>
      </div>

      {stance && counts ? (
        <>
          <div className={styles.stanceLine}>
            <span className={`${styles.stanceWord} ${styles[bucket]}`}>
              {RATING_DISPLAY[stance.rating]}
            </span>
            <DirectionTag direction={netDir} label={netLabel} />
          </div>
          <div className={styles.dist} role="img" aria-label={`評等分布，共 ${total} 家`}>
            {ORDER.map(r => {
              const n = counts.get(r) ?? 0
              if (!n || !total) return null
              return <span key={r} className={styles[r]} style={{ width: `${(n / total) * 100}%` }} />
            })}
          </div>
          {c?.target ? (
            <div className={styles.tp}>
              <span className={styles.tpLabel}>目標價中位數</span>
              <span className={styles.tpVal}>
                {fmtPrice(c.target.median, c.target.currency)}
                <DirectionTag direction={c.target.revision_direction} pct={c.target.revision_pct} />
              </span>
            </div>
          ) : null}
        </>
      ) : (
        <div className={styles.pending}>資料擷取中，尚無共識預覽</div>
      )}

      <div className={styles.foot}>
        <span className={styles.meta}>
          {item.broker_count} 家券商 · {item.report_count} 份研報
          {item.latest_report_date ? ` · 最新 ${fmtDate(item.latest_report_date)}` : ''}
        </span>
        <span className={styles.go} aria-hidden="true">→</span>
      </div>
    </button>
  )
}
