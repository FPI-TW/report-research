import styles from './MonitorPage.module.css'
import { TweenNumber } from '../../components/primitives/TweenNumber'
import { fmtInt } from './rate'

const toFixed1 = (n: number) => n.toFixed(1)

/** 進度百分比夾在 0–100，防越界資料把進度條/aria 推出合理範圍。 */
function clampPct(pct: number): number {
  return Math.min(100, Math.max(0, pct))
}

export function ProgressPanel({ title, data, rateLine, idleText }: {
  title: string
  data: { done: number; total: number; pct: number } | null
  rateLine: string
  idleText: string
}) {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>{title}</div>
      {data ? (
        <>
          <div className={styles.pmain}>
            <span className={styles.big}><TweenNumber value={data.done} decimals={0} format={fmtInt} duration={600} /></span>
            <span className={styles.pof}>/ {fmtInt(data.total)} 篇</span>
            <span className={styles.ppct}><TweenNumber value={data.pct} decimals={1} format={toFixed1} duration={600} />%</span>
          </div>
          <div
            className={styles.bar}
            role="progressbar"
            aria-label={title}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(clampPct(data.pct))}
          >
            <div className={styles.barFill} style={{ width: `${clampPct(data.pct)}%` }} />
          </div>
          <div className={styles.prate}>{rateLine}</div>
        </>
      ) : (
        <div className={styles.pidle}>{idleText}</div>
      )}
    </div>
  )
}
