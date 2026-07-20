import { Skeleton } from '../../components/primitives/Skeleton'
import styles from './RadarSkeleton.module.css'

/** 各區塊版型骨架（非中央 spinner）。 */
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
      <div className={styles.thesis}>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={styles.card}>
            <Skeleton width={48} height={12} radius={4} />
            <Skeleton width={100} height={20} radius={4} style={{ marginTop: 12 }} />
            <Skeleton width="80%" height={12} radius={4} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>
      <div className={styles.events}>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className={styles.card}>
            <Skeleton width={160} height={12} radius={4} />
            <Skeleton width="90%" height={16} radius={4} style={{ marginTop: 10 }} />
            <Skeleton width="70%" height={36} radius={6} style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>
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
