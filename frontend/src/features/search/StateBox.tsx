import { AlertCircle, Search } from 'lucide-react'
import atoms from './atoms.module.css'
import styles from './StateBox.module.css'

interface Props {
  kind: 'error' | 'empty'
  title: string
  description: string
  action: string
  onAction: () => void
  /** 標題層級：獨立一張是 h2；放在已有 h2 的結果區裡用 h3。 */
  level?: 2 | 3
  className?: string
}

/** 置中的狀態框：44px 灰色圓形圖示、標題、一句說明、動作按鈕。 */
export function StateBox({ kind, title, description, action, onAction, level = 2, className }: Props) {
  const Icon = kind === 'error' ? AlertCircle : Search
  const Heading = level === 3 ? 'h3' : 'h2'
  return (
    <div
      className={`${atoms.tile} ${styles.box}${className ? ` ${className}` : ''}`}
      role={kind === 'error' ? 'alert' : undefined}
    >
      <span className={styles.icon} aria-hidden="true"><Icon size={18} strokeWidth={1.8} /></span>
      <Heading className={styles.title}>{title}</Heading>
      <p className={styles.desc}>{description}</p>
      <div className={styles.actions}>
        <button type="button" className={atoms.btn} onClick={onAction}>{action}</button>
      </div>
    </div>
  )
}
