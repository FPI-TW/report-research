import type { ReactNode } from 'react'
import { AlertCircle, BarChart3, Clock, SearchX } from 'lucide-react'
import { ApiError } from '../../lib/api'
import shared from './shared.module.css'
import styles from './RadarStates.module.css'

const ICONS = {
  error: AlertCircle,
  empty: BarChart3,
  pending: Clock,
  notFound: SearchX,
} as const

interface StateBoxProps {
  icon: keyof typeof ICONS
  title: string
  text?: string
  children?: ReactNode
  /** 錯誤用 alert（立即播報），其餘用 status。 */
  role?: 'alert' | 'status'
}

/**
 * 交接包「載入、空白、錯誤狀態」的狀態框：置中、44px 灰色圓形圖示、標題 16px/600、
 * 一句說明、動作按鈕。
 *
 * 標題用 h2：這些狀態出現時頁面其他章節不在 DOM 裡，h1 之下直接接 h2 才不會跳級。
 */
export function StateBox({ icon, title, text, children, role = 'status' }: StateBoxProps) {
  const Icon = ICONS[icon]
  return (
    <div className={styles.box} role={role}>
      <span className={styles.icon} aria-hidden="true"><Icon size={18} strokeWidth={1.8} /></span>
      <h2 className={styles.title}>{title}</h2>
      {text ? <p className={styles.text}>{text}</p> : null}
      {children ? <div className={styles.actions}>{children}</div> : null}
    </div>
  )
}

/** HTTP 錯誤不是逾時，不說成逾時；連不上（fetch 本身失敗）才是。 */
function errorText(error: unknown): string {
  return error instanceof ApiError ? '請稍後再試。' : '連線逾時，請稍後再試。'
}

export function RadarLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <StateBox icon="error" role="alert" title="券商共識載入失敗" text={errorText(error)}>
      <button type="button" className={shared.btn} onClick={onRetry}>重試</button>
    </StateBox>
  )
}

export function RadarNotFound({ onBack }: { onBack: () => void }) {
  return (
    <StateBox icon="notFound" title="找不到這檔標的" text="目前沒有提及這檔標的的券商研報。">
      <button type="button" className={shared.btn} onClick={onBack}>回到券商共識</button>
    </StateBox>
  )
}

/** 「相關研報」在頁首已經有一顆，這裡不再重複放。 */
export function RadarPendingExtraction({ note }: { note?: string }) {
  return (
    <StateBox
      icon="pending"
      title="觀點資料整理中"
      text={note || '這檔標的已有研報，評等、目標價與觀點尚未擷取完成。'}
    />
  )
}

export function RadarWindowEmpty({
  note, onShowAll,
}: { note?: string; onShowAll?: () => void }) {
  return (
    <StateBox icon="empty" title="這段期間沒有可用資料" text={note || undefined}>
      {onShowAll ? (
        <button type="button" className={shared.btn} onClick={onShowAll}>看全部期間</button>
      ) : null}
    </StateBox>
  )
}
