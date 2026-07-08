import { Skeleton } from '../../components/primitives/Skeleton'
import styles from './MonitorPage.module.css'

/** 監控首載骨架屏（首次資料前顯示；keepPreviousData 讓其後不再閃）。重用 MonitorPage 版面 class 對齊。 */
export function MonitorSkeleton() {
  return (
    <div aria-hidden="true" data-testid="monitor-skeleton">
      <div className={styles.kpiGrid}>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={`${styles.card} ${styles.kpiCard}`}>
            <Skeleton width={72} height={12} radius={4} />
            <Skeleton width={92} height={26} radius={6} style={{ marginTop: 10 }} />
            <Skeleton width={64} height={12} radius={4} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>

      {[0, 1].map(g => (
        <div key={g} className={styles.panelGrid}>
          <PanelSkeleton />
          <PanelSkeleton />
        </div>
      ))}

      <div className={`${styles.card} ${styles.panel} ${styles.marginTop}`}>
        <Skeleton width={80} height={16} radius={4} />
        <div className={styles.mktList}>
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className={styles.mktRow}>
              <Skeleton width={52} height={12} radius={4} />
              <div className={styles.mktBar}><Skeleton height={8} radius="var(--tf-radius-pill)" /></div>
              <Skeleton width={40} height={12} radius={4} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function PanelSkeleton() {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <Skeleton width={96} height={16} radius={4} />
      <Skeleton width={140} height={28} radius={6} style={{ marginTop: 14 }} />
      <Skeleton height={9} radius="var(--tf-radius-pill)" style={{ marginTop: 12 }} />
      <Skeleton width={180} height={12} radius={4} style={{ marginTop: 12 }} />
    </div>
  )
}
