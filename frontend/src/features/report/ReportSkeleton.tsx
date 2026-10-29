import styles from './ReportSkeleton.module.css'

/** 灰塊＋白色掃光。版面對齊實際內容，載入完成時不跳動。 */
function Sk({ w, h, r }: { w: number | string; h: number; r?: number }) {
  return <span className={styles.sk} style={{ width: w, height: h, borderRadius: r }} />
}

/** 雙欄版型骨架（非中央 spinner）：研報面板＋PDF 畫布。 */
export function ReportSkeleton() {
  return (
    <div className={styles.page} aria-busy="true" aria-label="載入中" data-testid="report-skeleton">
      <div className={styles.main}>
        <div className={styles.intel}>
          <div className={styles.hd}>
            <Sk w={64} h={12} />
            <Sk w="46%" h={12} />
            <Sk w="96%" h={20} />
            <Sk w="70%" h={20} />
            <span className={styles.acts}>
              <span className={styles.grow}><Sk w="100%" h={36} r={10} /></span>
              <Sk w={96} h={36} r={10} />
            </span>
          </div>
          <div className={styles.sec}>
            <Sk w="22%" h={12} />
            <Sk w="100%" h={62} r={12} />
          </div>
          <div className={styles.sec}>
            <Sk w="16%" h={12} />
            <Sk w="100%" h={12} />
            <Sk w="92%" h={12} />
            <Sk w="68%" h={12} />
          </div>
        </div>
        <div className={styles.doc}>
          <span className={styles.paper} />
        </div>
      </div>
    </div>
  )
}
