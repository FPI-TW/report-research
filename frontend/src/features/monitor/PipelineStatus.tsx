import styles from './MonitorPage.module.css'
import type { Pipelines } from './progressSchema'

/**
 * 匯出而非私有：頁首的「n/m 條管線執行中」要用同一份清單算，否則加一列就得記得
 * 去改另一個檔案裡的硬編分母（原本寫死 `/4`）。
 */
export const PIPELINE_ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', name: 'Web 服務' },
  { key: 'ingest', name: '報告導入' },
  { key: 'tag', name: '語意標註' },
  { key: 'summaries', name: '摘要生成' },
  { key: 'signals', name: '雷達訊號' },
]

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
