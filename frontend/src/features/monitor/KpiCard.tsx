import type { ReactNode } from 'react'
import styles from './MonitorPage.module.css'

export function KpiCard({ label, value, suffix, sub }: {
  label: string
  value: ReactNode
  suffix?: string
  sub?: string
}) {
  return (
    <div className={`${styles.card} ${styles.kpiCard}`}>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiValRow}>
        <span className={styles.kpiVal}>{value}</span>
        {suffix ? <span className={styles.kpiSuffix}>{suffix}</span> : null}
      </div>
      {sub ? <div className={styles.kpiSub}>{sub}</div> : null}
    </div>
  )
}
