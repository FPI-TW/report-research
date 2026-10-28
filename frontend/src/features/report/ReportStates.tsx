import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { AlertCircle, FileX } from 'lucide-react'
import styles from './ReportStates.module.css'

interface StateBoxProps {
  icon: ReactNode
  title: string
  desc?: string
  /** 整頁狀態用 h1（頁面沒有別的標題），畫布內的狀態用 h2 */
  level?: 1 | 2
  /** 白底圓形圖示：放在灰色 PDF 畫布上時用，灰底圓會糊在背景裡 */
  onCanvas?: boolean
  role?: 'status' | 'alert'
  children?: ReactNode
}

/**
 * 載入、空白、錯誤共用的狀態框（交接包「載入、空白、錯誤狀態」）：
 * 置中，44px 灰色圓形圖示、標題 16px/600、一句說明、動作按鈕。
 * 動作按鈕用本檔 CSS Module 的 `.btn`／`.btnPrimary`（PdfPane、PdfViewer 直接引用）。
 */
export function StateBox({ icon, title, desc, level = 2, onCanvas, role, children }: StateBoxProps) {
  const H = level === 1 ? 'h1' : 'h2'
  return (
    <div className={styles.box} role={role}>
      <span className={onCanvas ? `${styles.ic} ${styles.icOnCanvas}` : styles.ic} aria-hidden="true">{icon}</span>
      <H className={styles.title}>{title}</H>
      {desc && <p className={styles.desc}>{desc}</p>}
      {children && <div className={styles.acts}>{children}</div>}
    </div>
  )
}

/** 查無此研報（含非 64-hex 的 hash，前端先擋一次不打 API）。 */
export function ReportNotFound() {
  return (
    <div className={styles.page}>
      <StateBox
        level={1}
        role="status"
        icon={<FileX size={18} strokeWidth={1.8} />}
        title="找不到這篇研報"
        desc="研報可能已下架，或網址有誤。"
      >
        <Link className={`${styles.btn} ${styles.btnPrimary}`} to="/library">回到研報庫</Link>
      </StateBox>
    </div>
  )
}

export function ReportLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className={styles.page}>
      <StateBox
        level={1}
        role="alert"
        icon={<AlertCircle size={18} strokeWidth={1.8} />}
        title="研報載入失敗"
        desc="連線逾時，請稍後再試。"
      >
        <button type="button" className={styles.btn} onClick={onRetry}>重試</button>
      </StateBox>
    </div>
  )
}
