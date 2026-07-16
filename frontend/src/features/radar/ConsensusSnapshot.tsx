import type { RatingConsensus, RatingNorm, Window } from '../../lib/radarSchemas'
import { RATING_DISPLAY, WINDOW_LABEL } from './radarFormat'
import styles from './ConsensusSnapshot.module.css'

const ORDER: RatingNorm[] = ['buy', 'overweight', 'neutral', 'underweight', 'sell']

interface Props {
  rating?: RatingConsensus | null
  window: Window
}

function netLabel(net: number): string {
  if (net > 0) return `淨上調 ${net} 家`
  if (net < 0) return `淨下調 ${Math.abs(net)} 家`
  return '持平'
}

/** 共識定盤：中位立場 + 五級分佈（含中位指針）+ 評等淨變動。 */
export function ConsensusSnapshot({ rating, window }: Props) {
  if (!rating || rating.total_rated === 0) {
    return <div className={styles.empty}>此窗期尚無評等分布。</div>
  }

  const counts = new Map(rating.distribution.map(d => [d.rating, d.count]))
  const total = rating.total_rated
  const median = rating.median_rating ?? 'neutral'

  // 指針落在中位級距的水平中心
  let acc = 0
  let needlePct = 50
  for (const r of ORDER) {
    const w = ((counts.get(r) ?? 0) / total) * 100
    if (r === median) {
      needlePct = acc + w / 2
      break
    }
    acc += w
  }

  const net = rating.upgrades - rating.downgrades
  const winLabel = WINDOW_LABEL[window] ?? window

  return (
    <div className={styles.tape}>
      <div className={styles.stance}>
        <div className={styles.kicker}>中位立場</div>
        <div className={styles.word}>{RATING_DISPLAY[median]}</div>
        <div className={styles.sub}>
          {total} 家已評等 · <b>{rating.bullish}</b> 偏多 / <b>{rating.neutral}</b> 中立
          {' / '}<b>{rating.bearish}</b> 偏空
        </div>

        <div className={styles.track}>
          <div className={styles.dist} role="img" aria-label={`評等分布，共 ${total} 家`}>
            {ORDER.map(r => {
              const c = counts.get(r) ?? 0
              if (!c) return null
              return <span key={r} className={styles[r]} style={{ width: `${(c / total) * 100}%` }} />
            })}
          </div>
          <div className={styles.needle} style={{ left: `${needlePct}%` }}>
            <span className={styles.pill}>中位</span>
            <span className={styles.stem} />
          </div>
        </div>
        <div className={styles.axis}><span>偏多</span><span>中立</span><span>偏空</span></div>

        <div className={styles.legend}>
          {ORDER.map(r => {
            const c = counts.get(r) ?? 0
            if (!c) return null
            return (
              <span key={r}>
                <i className={styles[r]} />{RATING_DISPLAY[r]} {c}
              </span>
            )
          })}
        </div>
      </div>

      <div className={styles.movement}>
        <div className={styles.kicker}>評等淨變動 · {winLabel}</div>
        <div className={styles.mvRow}>
          <span className={`${styles.mvNum} ${styles.up}`}>{rating.upgrades}</span>
          <span className={styles.mvLabel}>家上調評等</span>
        </div>
        <div className={styles.mvRow}>
          <span className={`${styles.mvNum} ${styles.down}`}>{rating.downgrades}</span>
          <span className={styles.mvLabel}>家下調評等</span>
        </div>
        <div className={styles.mvRow}>
          <span className={`${styles.mvNum} ${styles.flat}`}>{rating.unchanged}</span>
          <span className={styles.mvLabel}>家維持不變</span>
        </div>
        <p className={styles.mvNet}>評等動能：<b>{netLabel(net)}</b></p>
      </div>
    </div>
  )
}
