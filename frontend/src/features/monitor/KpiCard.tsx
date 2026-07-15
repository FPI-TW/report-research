import type { ReactNode } from 'react'
import styles from './MonitorPage.module.css'

export function KpiCard({ label, value, suffix, sub, index = 0 }: {
  label: string
  value: ReactNode
  suffix?: string
  sub?: string
  index?: number
}) {
  return (
    <div className={`${styles.card} ${styles.kpiCard} tf-reveal`} style={{ ['--tf-i' as string]: index }}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiValRow}>
        <span className={styles.kpiVal}>{value}</span>
        {suffix ? <span className={styles.kpiSuffix}>{suffix}</span> : null}
      </div>
      {sub ? <div className={styles.kpiSub}>{sub}</div> : null}
    </div>
  )
}
