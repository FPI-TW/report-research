import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'

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
            <span className={styles.big}>{fmtInt(data.done)}</span>
            <span className={styles.pof}>/ {fmtInt(data.total)} 篇</span>
            <span className={styles.ppct}>{data.pct.toFixed(2)}%</span>
          </div>
          <div className={styles.bar}><div className={styles.barFill} style={{ width: `${data.pct}%` }} /></div>
          <div className={styles.prate}>{rateLine}</div>
        </>
      ) : (
        <div className={styles.pidle}>{idleText}</div>
      )}
    </div>
  )
}
