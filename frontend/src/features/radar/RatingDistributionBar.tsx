import type { RatingBucketCount } from '../../lib/radarSchemas'
import { RATING_DISPLAY } from './radarFormat'
import styles from './RatingDistributionBar.module.css'

const ORDER = ['buy', 'overweight', 'neutral', 'underweight', 'sell'] as const
const SEG: Record<string, string> = {
  buy: styles.buy,
  overweight: styles.overweight,
  neutral: styles.neutral,
  underweight: styles.underweight,
  sell: styles.sell,
}

interface Props {
  distribution: RatingBucketCount[]
  totalRated: number
}

export function RatingDistributionBar({ distribution, totalRated }: Props) {
  const byRating = new Map(distribution.map(d => [d.rating, d.count]))
  const total = totalRated || ORDER.reduce((s, r) => s + (byRating.get(r) ?? 0), 0)

  return (
    <div>
      <div className={styles.bar} role="img" aria-label={`評等分布，共 ${total} 家`}>
        {ORDER.map(r => {
          const count = byRating.get(r) ?? 0
          if (!count || !total) return null
          const pct = (count / total) * 100
          return (
            <div
              key={r}
              className={`${styles.seg} ${SEG[r]}`}
              style={{ width: `${pct}%` }}
              title={`${RATING_DISPLAY[r]} ${count}`}
            />
          )
        })}
      </div>
      <div className={styles.legend}>
        {ORDER.map(r => {
          const count = byRating.get(r) ?? 0
          if (!count) return null
          return (
            <span key={r}>
              <span className={`${styles.dot} ${SEG[r]}`} />
              {RATING_DISPLAY[r]} {count}
            </span>
          )
        })}
      </div>
    </div>
  )
}
