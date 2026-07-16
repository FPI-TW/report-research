import type { Coverage, EpsConsensus, RatingConsensus, TargetConsensus, Window } from '../../lib/radarSchemas'
import { TweenNumber } from '../../components/primitives/TweenNumber'
import { DirectionTag } from './DirectionTag'
import { currencyPrefix, fmtNum, fmtPrice, WINDOW_LABEL } from './radarFormat'
import { RatingDistributionBar } from './RatingDistributionBar'
import styles from './ConsensusSnapshot.module.css'

interface Props {
  rating?: RatingConsensus | null
  target?: TargetConsensus | null
  eps?: EpsConsensus | null
  coverage: Coverage
  window: Window
}

export function ConsensusSnapshot({ rating, target, eps, coverage, window }: Props) {
  const primaryTarget = target?.groups.find(g => g.currency === target.primary_currency) ?? target?.groups[0]
  const primaryEps = eps?.primary ?? eps?.groups[0]
  const winLabel = WINDOW_LABEL[window] ?? window

  return (
    <section className={styles.grid} aria-label="共識快照">
      <div className={`${styles.card} ${styles.rating} tf-reveal`} style={{ ['--tf-i' as string]: 0 }}>
        <div className={styles.label}>評等共識</div>
        {rating && rating.total_rated > 0 ? (
          <>
            <div className={styles.buckets}>
              <span>偏多 <b><TweenNumber value={rating.bullish} decimals={0} /></b></span>
              <span>中立 <b><TweenNumber value={rating.neutral} decimals={0} /></b></span>
              <span>偏空 <b><TweenNumber value={rating.bearish} decimals={0} /></b></span>
            </div>
            <RatingDistributionBar distribution={rating.distribution} totalRated={rating.total_rated} />
            <div className={styles.sub}>
              {winLabel}：{rating.upgrades} 家上調、{rating.downgrades} 家下調
              {rating.unchanged ? `、${rating.unchanged} 家持平` : ''}
            </div>
          </>
        ) : (
          <div className={styles.sub}>尚無評等分布</div>
        )}
      </div>

      <div className={`${styles.card} tf-reveal`} style={{ ['--tf-i' as string]: 1 }}>
        <div className={styles.label}>目標價中位數</div>
        {primaryTarget ? (
          <>
            <div className={styles.value}>
              {fmtPrice(primaryTarget.median, primaryTarget.currency)}
            </div>
            <div className={styles.sub}>
              區間 {currencyPrefix(primaryTarget.currency)}{fmtNum(primaryTarget.low)}–{fmtNum(primaryTarget.high)}
              {' · '}{primaryTarget.count} 家可比
            </div>
            <div className={styles.foot}>
              <DirectionTag
                direction={primaryTarget.revision_direction}
                pct={primaryTarget.revision_pct}
              />
            </div>
            {target?.note ? <div className={styles.sub}>{target.note}</div> : null}
          </>
        ) : (
          <div className={styles.value}>—</div>
        )}
      </div>

      <div className={`${styles.card} tf-reveal`} style={{ ['--tf-i' as string]: 2 }}>
        <div className={styles.label}>
          EPS{primaryEps?.fiscal_year ? `（FY${primaryEps.fiscal_year}）` : ''}
        </div>
        {primaryEps ? (
          <>
            <div className={styles.value}>
              {fmtPrice(primaryEps.median, primaryEps.currency)}
            </div>
            <div className={styles.sub}>
              {primaryEps.count} 家可比
              {primaryEps.period ? ` · ${primaryEps.period}` : ''}
              {primaryEps.unit ? ` · ${primaryEps.unit}` : ''}
            </div>
            <div className={styles.foot}>
              <DirectionTag
                direction={primaryEps.revision_direction}
                pct={primaryEps.revision_pct}
              />
            </div>
          </>
        ) : (
          <div className={styles.value}>—</div>
        )}
      </div>

      <div className={`${styles.card} tf-reveal`} style={{ ['--tf-i' as string]: 3 }}>
        <div className={styles.label}>資料品質</div>
        <div className={styles.quality}>
          <strong>
            <TweenNumber value={coverage.brokers_extracted} decimals={0} />/{coverage.brokers_total || '—'} 家
          </strong>
          券商已擷取
          <div className={styles.sub} style={{ marginTop: 6 }}>
            共識納入 {coverage.brokers_in_consensus} 家
            {coverage.note ? ` · ${coverage.note}` : ''}
          </div>
        </div>
      </div>
    </section>
  )
}
