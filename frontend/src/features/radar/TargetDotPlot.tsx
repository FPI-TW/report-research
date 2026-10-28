import { useId, useMemo, type CSSProperties } from 'react'
import type { BrokerSummary, TargetGroup } from '../../lib/radarSchemas'
import { targetDots } from './consensusData'
import { fmtValue } from './radarFormat'
import shared from './shared.module.css'
import styles from './TargetDotPlot.module.css'

const LEVEL_PX = 18
const DOT_PX = 14
/** 上方留給「中位」標籤的高度。 */
const LABEL_PX = 18

interface Props {
  brokers: readonly BrokerSummary[]
  group: TargetGroup
  /** 目前展開歷程的券商；它的點換成金色。 */
  activeKey?: string | null
  /** 點一下點：展開該券商的歷程並捲到那一列。 */
  onSelect: (brokerKey: string) => void
}

/** 各券商目標價點圖：中位數金色虛線、滑過或聚焦顯示券商與價位。 */
export function TargetDotPlot({ brokers, group, activeKey, onSelect }: Props) {
  const id = useId()
  const dots = useMemo(() => targetDots(brokers, group), [brokers, group])
  if (!dots.length) return null

  const lo = Math.min(group.low, dots[0].value)
  const hi = Math.max(group.high, dots[dots.length - 1].value)
  const medianX = hi > lo ? ((group.median - lo) / (hi - lo)) * 100 : 50
  const maxLevel = Math.max(...dots.map(d => d.level))
  // 軸線的位置隨疊放層數往下推，最高那一層的點才不會蓋到上方的「中位」標籤。
  const axisY = Math.max(54, LABEL_PX + 4 + DOT_PX / 2 + maxLevel * LEVEL_PX)
  const labelAlign = medianX < 12 ? 'start' : medianX > 88 ? 'end' : 'center'

  return (
    <section className={`${shared.tile} ${styles.tile}`} aria-labelledby={id}>
      <div className={styles.head}>
        <h2 id={id} className={styles.title}>各券商目標價</h2>
        <span className={styles.ccy}>{group.currency}</span>
      </div>
      <div
        className={styles.plot}
        role="group"
        aria-label={`各券商目標價分布，中位數 ${fmtValue(group.median)}`}
        style={{ height: axisY + 38, '--axis-y': `${axisY}px` } as CSSProperties}
      >
        <span className={styles.axis} aria-hidden="true" />
        <span className={styles.medianLine} style={{ left: `${medianX}%` }} aria-hidden="true" />
        <span
          className={`${styles.medianLabel} ${shared.num}`}
          data-align={labelAlign}
          style={{ left: `${medianX}%` }}
          aria-hidden="true"
        >
          中位 {fmtValue(group.median)}
        </span>
        {dots.map(d => (
          <button
            key={d.key}
            type="button"
            className={styles.dot}
            data-on={d.key === activeKey || undefined}
            data-edge={d.x < 10 ? 'start' : d.x > 90 ? 'end' : undefined}
            style={{ left: `${d.x}%`, top: axisY - DOT_PX / 2 - d.level * LEVEL_PX }}
            aria-label={`${d.broker}，目標價 ${fmtValue(d.value)}`}
            onClick={() => onSelect(d.key)}
          >
            <span className={styles.tip} aria-hidden="true">
              {d.broker}<span className={`${styles.tipValue} ${shared.num}`}>{fmtValue(d.value)}</span>
            </span>
          </button>
        ))}
        <span className={`${styles.end} ${styles.endStart} ${shared.num}`} aria-hidden="true">{fmtValue(lo)}</span>
        <span className={`${styles.end} ${styles.endEnd} ${shared.num}`} aria-hidden="true">{fmtValue(hi)}</span>
      </div>
    </section>
  )
}
