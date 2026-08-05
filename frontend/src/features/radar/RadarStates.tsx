import { Callout } from '../../components/primitives/Callout'
import { Pressable } from '../../components/primitives/Pressable'
import styles from './RadarStates.module.css'

/**
 * overview 的三種非主要路徑狀態。
 *
 * 標題用 **h2** 而非 h3：這三個狀態渲染時，RadarOverview 那四個 h2 章節整段都不進 DOM
 * （是三選一的分支），所以文件裡只剩 h1 → h3、中間斷一級，標題大綱出現跳躍。
 * 而全語料只有約 0.68% 的研報有訊號，pending_extraction／window_empty 才是絕大多數標的
 * 的實際畫面——也就是「壞掉的階層」是多數使用者遇到的那一份，主要路徑反而是少數。
 * `.title` 的字級與 margin 已寫死在 RadarStates.module.css，不吃 UA 預設，故純語意調整、零視覺變化。
 */

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
      <h2 className={styles.title}>尚無可用研報</h2>
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
      <h2 className={styles.title}>尚未完成觀點資料整理</h2>
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
      <h2 className={styles.title}>此窗期內無可用訊號</h2>
      <p className={styles.text}>{note || '請嘗試切換至更長窗期或「全部」。'}</p>
    </div>
  )
}
