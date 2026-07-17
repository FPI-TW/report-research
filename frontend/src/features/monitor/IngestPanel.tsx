import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'
import { Sweep } from '../../components/primitives/motionLoops'

export function IngestPanel({ progress, rateLine }: { progress: Progress; rateLine: string }) {
  const { ingest, orchestrator, pipelines } = progress
  const active = pipelines.ingest
  const current =
    orchestrator?.label ??
    (ingest ? `本輪已導入 ${fmtInt(ingest.ingested)} 篇 · 失敗 ${fmtInt(ingest.fail)}` : '目前無執行中的導入')
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>報告導入</div>
      <div className={styles.ingCurrent}>{current}</div>
      {active ? (
        <Sweep className={styles.indetWrap} barClassName={styles.indetBar} duration={1.4} />
      ) : (
        <div className={styles.indetWrap}>
          <div className={styles.indetIdle} />
        </div>
      )}
      <div className={styles.prate}>{rateLine}</div>
    </div>
  )
}
