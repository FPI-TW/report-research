import { useId } from 'react'
import type { Direction, EpsGroup, RatingConsensus, TargetConsensus, TargetGroup } from '../../lib/radarSchemas'
import { epsFyLabel, fmtSignedPct, fmtValue, RATING_DISPLAY } from './radarFormat'
import { primaryTargetGroup, ratingParts } from './consensusData'
import { RatingDistBar } from './RatingDistBar'
import shared from './shared.module.css'
import styles from './ConsensusCards.module.css'

interface Props {
  rating?: RatingConsensus | null
  target?: TargetConsensus | null
  eps?: EpsGroup | null
}

/** 三張摘要卡：共識評等、目標價中位數、EPS 中位數（交接包「券商共識」詳情）。 */
export function ConsensusCards({ rating, target, eps }: Props) {
  return (
    <div className={styles.grid}>
      <RatingCard rating={rating} />
      <TargetCard target={target} />
      <EpsCard eps={eps} />
    </div>
  )
}

function RatingCard({ rating }: { rating?: RatingConsensus | null }) {
  const id = useId()
  const parts = rating ? ratingParts(rating.distribution) : []
  const median = rating?.median_rating
  return (
    <section className={`${shared.tile} ${styles.card}`} aria-labelledby={id}>
      <h2 id={id} className={styles.cardTitle}>共識評等</h2>
      <div className={styles.bigRow}>
        <span className={styles.big}>{median && parts.length ? RATING_DISPLAY[median] : '—'}</span>
        <span className={styles.unit}>{parts.length ? '中位數' : '尚無評等'}</span>
      </div>
      {parts.length ? (
        <>
          <div className={styles.distBar}><RatingDistBar parts={parts} height={8} /></div>
          <ul className={styles.legend} aria-label="評等分布">
            {parts.map(p => (
              <li key={p.rating}>
                <i className={styles[p.rating]} aria-hidden="true" />
                {p.label}
                <span className={`${styles.legendN} ${shared.num}`}>{p.count}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {rating ? (
        <div className={styles.footWrap}>
          <div className={`${styles.foot} ${styles.footWide}`}>
            <span className={`${shared.up} ${styles.strong} ${shared.num}`}>調升 {rating.upgrades}</span>
            <span className={`${shared.down} ${styles.strong} ${shared.num}`}>調降 {rating.downgrades}</span>
          </div>
        </div>
      ) : null}
    </section>
  )
}

function TargetCard({ target }: { target?: TargetConsensus | null }) {
  const id = useId()
  const group = primaryTargetGroup(target)
  return (
    <section className={`${shared.tile} ${styles.card}`} aria-labelledby={id}>
      <h2 id={id} className={styles.cardTitle}>目標價中位數</h2>
      <div className={styles.bigRow}>
        <span className={`${styles.big} ${styles.bigNum} ${shared.num}`}>{group ? fmtValue(group.median) : '—'}</span>
        <span className={styles.unit}>{group ? group.currency : '尚無目標價'}</span>
      </div>
      {group ? (
        <>
          <TargetRange group={group} />
          {target?.note ? <p className={styles.note}>{target.note}</p> : null}
          <Revision direction={group.revision_direction} pct={group.revision_pct} />
        </>
      ) : null}
    </section>
  )
}

/** 區間條：全距細線、四分位距淡金塊、中位數金色刻度。只有一家時三者疊在正中間。 */
function TargetRange({ group }: { group: TargetGroup }) {
  const span = group.high - group.low
  const pos = (v: number) => (span > 0 ? ((v - group.low) / span) * 100 : 50)
  const q1 = pos(group.q1)
  const q3 = pos(group.q3)
  return (
    <>
      <div
        className={styles.range}
        role="img"
        aria-label={`目標價區間 ${fmtValue(group.low)} 至 ${fmtValue(group.high)}，中位數 ${fmtValue(group.median)}，${group.count} 家券商`}
      >
        <span className={styles.track} />
        <span className={styles.iqr} style={{ left: `${q1}%`, width: `${Math.max(0, q3 - q1)}%` }} />
        <span className={styles.median} style={{ left: `${pos(group.median)}%` }} />
      </div>
      <div className={`${styles.rangeLabels} ${shared.num}`} aria-hidden="true">
        <span>{fmtValue(group.low)}</span>
        <span>{fmtValue(group.high)}</span>
      </div>
    </>
  )
}

function EpsCard({ eps }: { eps?: EpsGroup | null }) {
  const id = useId()
  const basis = eps ? epsFyLabel(eps.fiscal_year, eps.period) : ''
  return (
    <section className={`${shared.tile} ${styles.card}`} aria-labelledby={id}>
      <h2 id={id} className={styles.cardTitle}>
        EPS 中位數
        {basis ? <>{' '}<span className={`${styles.basis} ${shared.num}`}>{basis}</span></> : null}
      </h2>
      <div className={styles.bigRow}>
        <span className={`${styles.big} ${styles.bigNum} ${shared.num}`}>{eps ? fmtValue(eps.median) : '—'}</span>
        <span className={styles.unit}>{eps ? (eps.currency ?? '') : '尚無 EPS 預估'}</span>
      </div>
      {eps ? (
        <>
          <p className={styles.provided}><b className={shared.num}>{eps.count}</b> 家券商提供</p>
          <Revision direction={eps.revision_direction} pct={eps.revision_pct} />
        </>
      ) : null}
    </section>
  )
}

const DIRECTION_TEXT: Partial<Record<Direction, string>> = { up: '上升', down: '下降', flat: '持平' }

/**
 * 卡片底列的修正幅度。後端算的是「每家券商與自己前一份報告相比」的變動中位數，
 * 所以字樣是「較前次」而不是「較期初」。
 */
function Revision({ direction, pct }: { direction: Direction; pct?: number | null }) {
  const moved = (direction === 'up' || direction === 'down') && pct != null
  return (
    <div className={styles.footWrap}>
      <div className={styles.foot}>
        較前次
        {moved ? (
          <>
            <svg className={`${styles.tri} ${shared[direction]}`} width="8" height="8" viewBox="0 0 10 10" aria-hidden="true">
              <path d={direction === 'up' ? 'M5 1 9.5 9h-9z' : 'M5 9 9.5 1h-9z'} fill="currentColor" />
            </svg>
            <span className={shared.srOnly}>{DIRECTION_TEXT[direction]}</span>
            <span className={`${shared[direction]} ${styles.strong} ${shared.num}`}>{fmtSignedPct(pct)}</span>
          </>
        ) : (
          <span>{direction === 'flat' ? '持平' : '—'}</span>
        )}
      </div>
    </div>
  )
}
