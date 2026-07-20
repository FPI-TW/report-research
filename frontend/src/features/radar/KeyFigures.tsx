import type { Coverage, EpsConsensus, TargetConsensus } from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import { currencyPrefix, fmtNum } from './radarFormat'
import styles from './KeyFigures.module.css'

interface Props {
  target?: TargetConsensus | null
  eps?: EpsConsensus | null
  coverage: Coverage
}

function PriceValue({ value, currency }: { value: number | null | undefined, currency: string | null | undefined }) {
  if (value == null) return '—'

  const prefix = currencyPrefix(currency)
  const amount = fmtNum(value, value >= 100 ? 0 : 2)
  return (
    <>
      {prefix ? <span className={styles.currency}>{prefix}</span> : null}
      {prefix ? <wbr /> : null}
      <span className={styles.amount}>{amount}</span>
    </>
  )
}

/** overview 頂部關鍵數字：目標價中位數 / EPS 中位數 / 資料品質。 */
export function KeyFigures({ target, eps, coverage }: Props) {
  const primaryTarget = target?.groups.find(g => g.currency === target.primary_currency)
    ?? target?.groups[0]
  const primaryEps = eps?.primary ?? eps?.groups[0]
  const qualityPct = coverage.brokers_total
    ? Math.round((coverage.brokers_extracted / coverage.brokers_total) * 100)
    : 0

  return (
    <section className={styles.grid} aria-label="關鍵數字">
      <div className={styles.fig}>
        <div className={styles.kicker}>目標價中位數</div>
        {primaryTarget ? (
          <>
            <div className={styles.val}>
              <PriceValue value={primaryTarget.median} currency={primaryTarget.currency} />
            </div>
            <div className={styles.foot}>
              <DirectionTag
                direction={primaryTarget.revision_direction}
                pct={primaryTarget.revision_pct}
              />
            </div>
            <div className={styles.sub}>
              區間 {currencyPrefix(primaryTarget.currency)}{fmtNum(primaryTarget.low)}–{fmtNum(primaryTarget.high)}
              {' · '}{primaryTarget.count} 家可比
            </div>
          </>
        ) : (
          <div className={styles.val}>—</div>
        )}
      </div>

      <div className={styles.fig}>
        <div className={styles.kicker}>
          EPS 中位數{primaryEps?.fiscal_year ? ` · FY${primaryEps.fiscal_year}` : ''}
        </div>
        {primaryEps ? (
          <>
            <div className={styles.val}>
              <PriceValue value={primaryEps.median} currency={primaryEps.currency} />
            </div>
            <div className={styles.foot}>
              <DirectionTag
                direction={primaryEps.revision_direction}
                pct={primaryEps.revision_pct}
              />
            </div>
            <div className={styles.sub}>
              {primaryEps.count} 家可比
              {primaryEps.period ? ` · ${primaryEps.period}` : ''}
              {primaryEps.unit ? ` · ${primaryEps.unit}` : ''}
            </div>
          </>
        ) : (
          <div className={styles.val}>—</div>
        )}
      </div>

      <div className={styles.fig}>
        <div className={styles.kicker}>資料品質</div>
        <div className={styles.val}>
          {coverage.brokers_extracted} / {coverage.brokers_total || '—'} 家
        </div>
        <div className={styles.qbar}>
          <i style={{ width: `${qualityPct}%` }} />
        </div>
        {coverage.state === 'partial' ? (
          <div className={styles.qualityState}>部分資料 · 非完整品質</div>
        ) : null}
        <div className={styles.sub}>
          券商已擷取 · 共識納入 {coverage.brokers_in_consensus} 家
        </div>
      </div>
    </section>
  )
}
