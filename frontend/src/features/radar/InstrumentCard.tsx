import type { Market, RadarInstrumentItem, RatingNorm } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtPrice, marketVar, RATING_BUCKET, RATING_DISPLAY } from './radarFormat'
import styles from './InstrumentCard.module.css'

const ORDER: RatingNorm[] = ['buy', 'overweight', 'neutral', 'underweight', 'sell']

interface Props {
  item: RadarInstrumentItem
  onSelect: (market: Market, code: string) => void
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
          {/* 標籤要帶分布本身。整張卡是一個 <button>，這段會被串進它的可及名稱，
              而卡內沒有任何文字版分布可以補救（ConsensusSnapshot 那邊有 .legend 逐級列出，
              這裡沒有）——只說「共 N 家」等於這條五級分布對螢幕閱讀器使用者完全消失。 */}
          <div
            className={styles.dist}
            role="img"
            aria-label={
              ORDER.filter(r => counts.get(r))
                .map(r => `${RATING_DISPLAY[r]} ${counts.get(r)} 家`)
                .join('、') + `，共 ${total} 家已評等`
            }
          >
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
