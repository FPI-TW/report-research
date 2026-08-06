import styles from './MonitorPage.module.css'
import type { Pipelines } from './progressSchema'

/**
 * 匯出而非私有：頁首的「n/m 條管線執行中」要用同一份清單算，否則加一列就得記得
 * 去改另一個檔案裡的硬編分母（原本寫死 `/4`）。
 */
/**
 * 派生資產的顯示名，管線列與覆蓋率卡共用同一份。
 *
 * 先前管線列叫「雷達訊號」而覆蓋率卡叫「觀點訊號（近 30 天）」——同一個東西兩個名字，
 * 讀者沒辦法把「管線在跑」和「那張卡的數字」連起來。改成單一來源後，這種漂移不必靠
 * 測試去比對字串（那種比對只能證明兩邊「現在」一樣，改一邊照樣同時改到），而是結構上
 * 不可能發生。覆蓋率卡標題 = `${DERIVED_LABEL.x}（近 30 天）`。
 */
export const DERIVED_LABEL = { takeaways: '重點摘錄', signals: '觀點訊號' } as const

export const PIPELINE_ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', name: 'Web 服務' },
  { key: 'ingest', name: '報告導入' },
  { key: 'tag', name: '語意標註' },
  { key: 'summaries', name: '摘要生成' },
  { key: 'takeaways', name: DERIVED_LABEL.takeaways },
  { key: 'signals', name: DERIVED_LABEL.signals },
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
