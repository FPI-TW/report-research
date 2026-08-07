import styles from './MonitorPage.module.css'
import type { Pipelines } from './progressSchema'
import { PIPELINE_ROWS } from './pipelineMeta'

export function PipelineStatus({ pipelines }: { pipelines: Pipelines }) {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>處理管線</div>
      <div className={styles.pipeList}>
        {PIPELINE_ROWS.map(r => {
          const on = pipelines[r.key]
          return (
            <div key={r.key} className={styles.pipeRow}>
              <span className={`${styles.pipeDot} ${on ? styles.pipeOn : styles.pipeOff}`} />
              <span className={styles.pipeName}>{r.name}</span>
              <span className={`${styles.pipeBadge} ${on ? styles.badgeOn : styles.badgeOff}`}>{on ? '執行中' : '已停止'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
