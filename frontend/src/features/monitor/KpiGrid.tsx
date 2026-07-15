import styles from './MonitorPage.module.css'
import { KpiCard } from './KpiCard'
import { TweenNumber } from '../../components/primitives/TweenNumber'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'

const toFixed1 = (n: number) => n.toFixed(1)

export function KpiGrid({ progress }: { progress: Progress }) {
  const { db, tagging, summary } = progress
  return (
    <div className={styles.kpiGrid}>
      <KpiCard index={0} label="已導入報告" value={<TweenNumber value={db.reports} decimals={0} format={fmtInt} duration={600} />} suffix="篇" sub={`${db.markets.length} 個市場`} />
      <KpiCard index={1} label="總片段 CHUNKS" value={<TweenNumber value={db.chunks} decimals={0} format={fmtInt} duration={600} />} suffix="段" sub="向量片段總數" />
      <KpiCard
        index={2}
        label="標註進度"
        value={tagging ? <TweenNumber value={tagging.pct} decimals={1} format={toFixed1} duration={600} /> : '—'}
        suffix="%"
        sub={tagging ? `已標註 ${fmtInt(tagging.done)} / ${fmtInt(tagging.total)}` : undefined}
      />
      <KpiCard
        index={3}
        label="摘要進度"
        value={<TweenNumber value={summary.pct} decimals={1} format={toFixed1} duration={600} />}
        suffix="%"
        sub={`已生成 ${fmtInt(summary.done)} / ${fmtInt(summary.total)}`}
      />
    </div>
  )
}
