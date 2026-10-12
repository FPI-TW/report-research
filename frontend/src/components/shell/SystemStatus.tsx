import { STATUS_LABELS, type SystemStatus, type SystemStatusLevel } from '../../lib/useSystemStatus'
import styles from './SystemStatus.module.css'

const DOT_CLASS: Record<SystemStatusLevel, string> = {
  ok: styles.ok,
  degraded: styles.degraded,
  unknown: styles.unknown,
}

/** 狀態小燈（純裝飾，文字在旁邊或 title 裡）。 */
export function StatusDot({ level, className }: { level: SystemStatusLevel; className?: string }) {
  return <span className={`${styles.dot} ${DOT_CLASS[level]} ${className ?? ''}`} aria-hidden="true" />
}

/**
 * 帳號選單裡的一列系統狀態：燈號＋「正常／部分異常／狀態未知」＋後端給的一句說明。
 * 只顯示後端 `/api/status` 回的粗粒度結論，不連到管理後台、不列服務。
 */
export function SystemStatusRow({ status }: { status: SystemStatus }) {
  return (
    <div className={styles.row} role="status" aria-label={`系統狀態：${STATUS_LABELS[status.status]}`}>
      <StatusDot level={status.status} />
      <div className={styles.text}>
        <span className={styles.label}>系統狀態：{STATUS_LABELS[status.status]}</span>
        {status.status !== 'ok' && <span className={styles.message}>{status.message}</span>}
      </div>
    </div>
  )
}
