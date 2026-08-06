import { Skeleton } from '../../components/primitives/Skeleton'
import styles from './RadarSkeleton.module.css'

/**
 * 各區塊版型骨架（非中央 spinner）。
 *
 * 它是 `q.isLoading && !data` 期間唯一的內容，資料回來後整段被換掉——所以它必須**鏡射**
 * 真實版型，而不只是「看起來像在載入」。骨架與真物的欄數、卡片邊界、章節標題數量一旦
 * 對不上，載入完成當下整頁就會往上或往下跳，捲動位置一起失準。
 *
 * 對應真物：CoverageStrip（薄卡）→ 市場共識摘要（左面板：評等窄列＋兩張點圖；
 * 右欄：切換鈕＋已選券商面板）→ 四向觀點 → 近期關鍵變化 → 各券商最新觀點。
 */
export function RadarSkeleton() {
  return (
    <div aria-busy="true" data-testid="radar-skeleton">
      <div className={`${styles.card} ${styles.coverage}`}>
        <Skeleton width={220} height={14} radius={4} />
        <Skeleton width={112} height={14} radius={4} />
      </div>

      <div className={styles.sectionTitle}><Skeleton width={112} height={19} radius={4} /></div>
      <div className={styles.summary}>
        <div className={`${styles.card} ${styles.summaryMain}`}>
          <div className={styles.strip}>
            <Skeleton width={64} height={12} radius={4} />
            <Skeleton width={52} height={20} radius={4} />
            <Skeleton width={86} height={13} radius={4} />
            <Skeleton width={180} height={13} radius={4} />
          </div>
          {Array.from({ length: 2 }, (_, i) => (
            <div key={i} className={styles.plot}>
              <Skeleton width={120} height={15} radius={4} />
              <Skeleton width={200} height={12} radius={4} style={{ marginTop: 8 }} />
              {Array.from({ length: 4 }, (_, r) => (
                <Skeleton key={r} height={14} radius={4} style={{ marginTop: 20 }} />
              ))}
              <Skeleton width="60%" height={12} radius={4} style={{ marginTop: 22 }} />
            </div>
          ))}
        </div>
        <div className={styles.summarySide}>
          <Skeleton height={44} radius={14} />
          <div className={`${styles.card} ${styles.detail}`}>
            <Skeleton width={64} height={12} radius={4} />
            <Skeleton width={110} height={22} radius={6} style={{ marginTop: 12 }} />
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} width="72%" height={21} radius={4} style={{ marginTop: 20 }} />
            ))}
            <Skeleton height={44} radius={10} style={{ marginTop: 20 }} />
          </div>
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
