import styles from './MonitorPage.module.css'
import { marketColor, marketLabel } from '../../lib/meta'
import { TweenNumber } from '../../components/primitives/TweenNumber'
import { fmtInt } from './rate'
import type { MarketCount } from './progressSchema'

export function MarketDistribution({ markets }: { markets: MarketCount[] }) {
  const rows = [...markets].sort((a, b) => b.count - a.count)
  const total = rows.reduce((s, m) => s + m.count, 0)
  const max = Math.max(1, ...rows.map(m => m.count))
  return (
    <div className={`${styles.card} ${styles.panel} ${styles.marginTop}`}>
      <div className={styles.ptitle}>市場分佈</div>
      <div className={styles.mktList}>
        {rows.length === 0 ? (
          <div className={styles.mktRow}><span className={styles.mktLabel}>—</span></div>
        ) : (
          rows.map(m => {
            const code = m.market ?? '—'
            const label = m.market ? marketLabel(m.market) : '—'
            const color = m.market ? marketColor(m.market) : '#98a2b3'
            const pct = total ? (m.count / total) * 100 : 0
            return (
              <div key={code} className={styles.mktRow}>
                <span className={styles.mktLabel}>{label}</span>
                <div className={styles.mktBar}>
                  <div className={styles.mktFill} style={{ width: `${((m.count / max) * 100).toFixed(1)}%`, background: color }} />
                </div>
                <span className={styles.mktCount}><TweenNumber value={m.count} decimals={0} format={fmtInt} duration={600} /></span>
                <span className={styles.mktPct}>{pct.toFixed(0)}%</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
