import { Skeleton } from '../../components/primitives/Skeleton'
import styles from './ReportSkeleton.module.css'

/** 雙欄版型骨架（非中央 spinner）：報頭 + 左欄智慧 + 右欄文件。 */
export function ReportSkeleton() {
  return (
    <div className={styles.page} aria-busy="true" data-testid="report-skeleton">
      <div className={styles.hd}>
        <Skeleton width={90} height={12} radius={4} />
        <Skeleton width="46%" height={26} radius={6} style={{ marginTop: 12 }} />
        <Skeleton width={220} height={14} radius={4} style={{ marginTop: 12 }} />
      </div>
      <div className={styles.main}>
        <div className={styles.intel}>
          {Array.from({ length: 2 }, (_, s) => (
            <div key={s} className={styles.sec}>
              <Skeleton width={64} height={11} radius={4} style={{ marginBottom: 12 }} />
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton
                  key={i}
                  width={i % 3 === 2 ? '72%' : '100%'}
                  height={13}
                  radius={4}
                  style={{ marginBottom: 9 }}
                />
              ))}
            </div>
          ))}
        </div>
        <div className={styles.doc}>
          {/* docBar 現在只剩檔名（[原文][文字] 分段控制已移除），佔位塊要跟著縮成
              一行等寬文字，否則骨架→實體之間會有一次可見的版位跳動。 */}
          <div className={styles.bar}>
            <Skeleton width={220} height={14} radius={4} />
          </div>
          <div className={styles.stage}>
            <Skeleton width="100%" height="100%" radius={4} />
          </div>
        </div>
      </div>
    </div>
  )
}
