import type { RatingConsensus, RatingNorm, Window } from '../../lib/radarSchemas'
import { RATING_BUCKET, RATING_DISPLAY, WINDOW_LABEL } from './radarFormat'
import styles from './ConsensusSnapshot.module.css'

const ORDER: RatingNorm[] = ['buy', 'overweight', 'neutral', 'underweight', 'sell']

/** 語意桶 → 立場詞著色。與 InstrumentCard 的 .bull/.neu/.bear 同一組，同一筆資料不該兩套色。 */
const WORD_CLASS = { bull: 'wBull', neu: 'wNeu', bear: 'wBear' } as const

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

  // 標線對應固定五級量表，而不是動態分布條的寬度：
  // 否則「加碼」會因樣本分布被畫到例如 75%，與下方語意座標脫節。
  const medianIndex = ORDER.indexOf(median)
  const needlePct = medianIndex >= 0
    ? ((medianIndex + 0.5) / ORDER.length) * 100
    : 50

  const net = rating.upgrades - rating.downgrades
  const winLabel = WINDOW_LABEL[window] ?? window
  // 淨變動的方向色沿用正上方三列 .mvNum 的同一組 class，不再寫死綠色。
  const netTone = net > 0 ? styles.up : net < 0 ? styles.down : styles.flat

  return (
    <div className={styles.tape}>
      <div className={styles.stance}>
        <div className={styles.kicker}>中位立場</div>
        <div className={`${styles.word} ${styles[WORD_CLASS[RATING_BUCKET[median]]]}`}>
          {RATING_DISPLAY[median]}
        </div>
        {/* 只給總數：偏多／中立／偏空的三桶聚合是下方 .legend 五級明細的嚴格子集
            （bullish = buy + overweight，見 radarFormat.RATING_BUCKET），
            兩者在垂直方向不到 60px 內把同一組數字講了兩次。 */}
        <div className={styles.sub}>
          共 <b>{total}</b> 家已評等
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
        {/* 只標兩端。中間那個「中立」會宣稱「分布條的 50% ＝ 中立」，但條的段寬是
            count/total 的比例、指針走的卻是固定五級量表，兩個座標系不同（見 needlePct 註解）。 */}
        <div className={styles.axis}><span>偏多</span><span>偏空</span></div>

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
        <p className={styles.mvNet}>評等動能：<b className={netTone}>{netLabel(net)}</b></p>
      </div>
    </div>
  )
}
