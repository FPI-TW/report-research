import { Link } from 'react-router'
import { Callout } from '../../components/primitives/Callout'
import styles from './ReportStates.module.css'

/** 查無此研報（含非 64-hex 的 hash，前端先擋一次不打 API）。 */
export function ReportNotFound() {
  return (
    <div className={styles.page}>
      <div className={styles.box} role="status">
        <h2 className={styles.title}>找不到這份研報</h2>
        <p className={styles.text}>連結可能已失效，或這份研報不在語料庫中。</p>
        <Link className={styles.btn} to="/search">回到檢索</Link>
      </div>
    </div>
  )
}

export function ReportLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className={styles.page}>
      <div className={styles.boxPlain}>
        <Callout variant="error" action={{ label: '重試', onClick: onRetry }}>
          載入研報時發生問題
        </Callout>
      </div>
    </div>
  )
}
