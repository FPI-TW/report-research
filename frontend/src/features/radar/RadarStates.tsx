import { Callout } from '../../components/primitives/Callout'
import { Pressable } from '../../components/primitives/Pressable'
import styles from './RadarStates.module.css'

export function RadarLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <Callout variant="error" action={{ label: '重試', onClick: onRetry }}>
      載入觀點變化時發生問題
    </Callout>
  )
}

export function RadarNotFound({ onBack }: { onBack: () => void }) {
  return (
    <div className={styles.box} role="status">
      <h3 className={styles.title}>尚無可用研報</h3>
      <p className={styles.text}>本標的目前沒有可供比較的券商研報。</p>
      <Pressable className={styles.btn} onClick={onBack}>重新選擇標的</Pressable>
    </div>
  )
}

export function RadarPendingExtraction({
  note,
  onBrowseReports,
}: {
  note?: string
  onBrowseReports?: () => void
}) {
  return (
    <div className={styles.box} role="status">
      <h3 className={styles.title}>尚未完成觀點資料整理</h3>
      <p className={styles.text}>
        {note || '此標的已有研報，但評等、目標價與論點尚未完成擷取。'}
      </p>
      {onBrowseReports ? (
        <Pressable className={styles.btnSecondary} onClick={onBrowseReports}>
          查看相關研報
        </Pressable>
      ) : null}
    </div>
  )
}

export function RadarWindowEmpty({ note }: { note?: string }) {
  return (
    <div className={styles.box} role="status">
      <h3 className={styles.title}>此窗期內無可用訊號</h3>
      <p className={styles.text}>{note || '請嘗試切換至更長窗期或「全部」。'}</p>
    </div>
  )
}
