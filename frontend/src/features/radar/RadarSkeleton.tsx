import { Skeleton } from '../../components/primitives/Skeleton'
import styles from './RadarSkeleton.module.css'

/**
 * 各區塊版型骨架（非中央 spinner）。
 *
 * 它是 `q.isLoading && !data` 期間唯一的內容，資料回來後整段被換掉——所以它必須**鏡射**
 * 真實版型，而不只是「看起來像在載入」。舊版缺了整塊券商共識面板與四個章節標題、KPI 用
 * 三張分離卡片對一整片面板、四向觀點永遠兩欄（真物在 >1080px 是四欄），合計比真實內容
 * 矮 500px 以上，載入完成當下整頁往下跳、捲動位置一起失準。
 */
export function RadarSkeleton() {
  return (
    <div aria-busy="true" data-testid="radar-skeleton">
      <div className={styles.kpi}>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className={styles.card}>
            <Skeleton width={72} height={12} radius={4} />
            <Skeleton width="70%" height={26} radius={6} style={{ marginTop: 12 }} />
            <Skeleton width="50%" height={12} radius={4} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>

      <div className={styles.sectionTitle}><Skeleton width={96} height={19} radius={4} /></div>
      <div className={`${styles.card} ${styles.consensus}`}>
        <div className={styles.consensusMain}>
          <Skeleton width={72} height={12} radius={4} />
          <Skeleton width={110} height={30} radius={6} style={{ marginTop: 10 }} />
          <Skeleton width="42%" height={13} radius={4} style={{ marginTop: 12 }} />
          <Skeleton height={15} radius={999} style={{ marginTop: 20 }} />
          <Skeleton width="60%" height={12} radius={4} style={{ marginTop: 28 }} />
        </div>
        <div className={styles.consensusSide}>
          <Skeleton width={104} height={12} radius={4} />
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} width="78%" height={26} radius={4} style={{ marginTop: 14 }} />
          ))}
        </div>
      </div>

      <div className={styles.sectionTitle}><Skeleton width={80} height={19} radius={4} /></div>
      <div className={styles.thesis}>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={styles.card}>
            <Skeleton width={48} height={12} radius={4} />
            <Skeleton width={100} height={20} radius={4} style={{ marginTop: 12 }} />
            <Skeleton width="80%" height={12} radius={4} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>

      <div className={styles.sectionTitle}><Skeleton width={112} height={19} radius={4} /></div>
      <div className={styles.events}>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className={styles.card}>
            <Skeleton width={160} height={12} radius={4} />
            <Skeleton width="90%" height={16} radius={4} style={{ marginTop: 10 }} />
            <Skeleton width="70%" height={36} radius={6} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>

      <div className={styles.sectionTitle}><Skeleton width={128} height={19} radius={4} /></div>
      <div className={styles.card}>
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className={styles.row}>
            <Skeleton width="18%" height={14} radius={4} />
            <Skeleton width="12%" height={14} radius={4} />
            <Skeleton width="14%" height={14} radius={4} />
            <Skeleton width="20%" height={14} radius={4} />
          </div>
        ))}
      </div>
    </div>
  )
}
