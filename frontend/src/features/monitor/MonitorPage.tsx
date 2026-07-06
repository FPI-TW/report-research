import styles from './MonitorPage.module.css'
import { useProgress } from './useProgress'
import { useClock } from './useClock'
import { useRates } from './useRates'
import { rateText, ingestRateText, fmtInt } from './rate'
import { KpiGrid } from './KpiGrid'
import { ProgressPanel } from './ProgressPanel'
import { IngestPanel } from './IngestPanel'
import { PipelineStatus } from './PipelineStatus'
import { MarketDistribution } from './MarketDistribution'
import { MonitorSkeleton } from './MonitorSkeleton'

export default function MonitorPage() {
  const q = useProgress()
  const clock = useClock()
  const rates = useRates(q.data, q.dataUpdatedAt)
  const p = q.data
  const live = !q.isError

  const alive = p ? [p.pipelines.web, p.pipelines.ingest, p.pipelines.tag, p.pipelines.summaries].filter(Boolean).length : 0

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.inner}>
          <div className={styles.header}>
            <div>
              <h2 className={styles.title}>研報導入監控</h2>
              <div className={styles.sub}>
                {p ? `${fmtInt(p.db.reports)} 篇已導入 · ${alive}/4 條管線執行中` : '連線中…'}
              </div>
            </div>
            <div className={styles.headRight}>
              <span className={`${styles.live} ${live ? '' : styles.stale}`}>
                <span className={`${styles.liveDot} ${live ? '' : styles.staleDot}`} />
                {live ? 'LIVE' : '重連中'}
              </span>
              <span className={styles.clock}>{clock}</span>
            </div>
          </div>

          {p ? (
            <>
              <KpiGrid progress={p} />
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="語意標註"
                  data={p.tagging}
                  rateLine={rateText(p.tagging?.fail ?? 0, rates.tpm, '標註')}
                  idleText="目前無執行中的標註"
                />
                <IngestPanel progress={p} rateLine={ingestRateText(rates.rpm, rates.cps)} />
              </div>
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="摘要生成"
                  data={p.summary}
                  rateLine={rateText(p.summary.remaining, rates.spm, '摘要')}
                  idleText="—"
                />
                <PipelineStatus pipelines={p.pipelines} />
              </div>
              <MarketDistribution markets={p.db.markets} />
              <div className={styles.footer}>資料每 5 秒自動更新 · 廷豐智能研報導入管線</div>
            </>
          ) : q.isError ? (
            <div className={styles.footer}>連線失敗，重試中…</div>
          ) : (
            <MonitorSkeleton />
          )}
        </div>
      </div>
    </div>
  )
}
