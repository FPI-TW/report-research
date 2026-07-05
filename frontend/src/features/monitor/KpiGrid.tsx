import styles from './MonitorPage.module.css'
import { KpiCard } from './KpiCard'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'

export function KpiGrid({ progress }: { progress: Progress }) {
  const { db, tagging, summary } = progress
  return (
    <div className={styles.kpiGrid}>
      <KpiCard label="已導入報告" value={fmtInt(db.reports)} sub={`${db.markets.length} 個市場`} />
      <KpiCard label="總片段 CHUNKS" value={fmtInt(db.chunks)} sub="向量片段總數" />
      <KpiCard
        label="標註進度"
        value={tagging ? tagging.pct.toFixed(2) : '—'}
        suffix="%"
        sub={tagging ? `已標註 ${fmtInt(tagging.done)} / ${fmtInt(tagging.total)}` : undefined}
      />
      <KpiCard
        label="摘要進度"
        value={summary.pct.toFixed(2)}
        suffix="%"
        sub={`已生成 ${fmtInt(summary.done)} / ${fmtInt(summary.total)}`}
      />
    </div>
  )
}
