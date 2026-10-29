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
 * 設定「關於」裡的系統狀態列，與帳號分頁各列同一個版型：左邊「系統狀態」，右邊燈號＋結論
 * （正常／部分異常／狀態未知）。不正常時才在標籤下多一行後端給的原因；正常時那句只是重述「正常」。
 * 只顯示後端 `/api/status` 回的粗粒度結論，不連到管理後台、不列服務。
 */
export function SystemStatusRow({ status }: { status: SystemStatus }) {
  const label = STATUS_LABELS[status.status]
  return (
    <div className={styles.row} role="status" aria-label={`系統狀態：${label}`}>
      <div className={styles.main}>
        <span className={styles.label}>系統狀態</span>
        {status.status !== 'ok' && <span className={styles.message}>{status.message}</span>}
      </div>
      <span className={styles.value} data-level={status.status}>
        <StatusDot level={status.status} />
        {label}
      </span>
    </div>
  )
}
