import type { CSSProperties } from 'react'
import shared from './shared.module.css'
import styles from './RadarSkeleton.module.css'

function Skel({ w, h, style }: { w: number | string; h: number; style?: CSSProperties }) {
  return <span className={shared.skel} style={{ width: w, height: h, ...style }} />
}

/**
 * 詳情載入中的骨架，版面對齊實際內容：三張摘要卡 → 目標價點圖 → 四向觀點 → 各券商最新觀點。
 * 資料回來後整段被換掉，所以各區塊的高度要接近真的，否則載入完成那一刻整頁會跳。
 */
export function RadarSkeleton() {
  return (
    <div aria-busy="true" aria-label="載入中" data-testid="radar-skeleton">
      <div className={styles.cards}>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className={`${shared.tile} ${styles.card}`}>
            <Skel w={72} h={12} />
            <Skel w={96} h={30} style={{ marginTop: 10 }} />
            <Skel w="100%" h={8} style={{ marginTop: 18 }} />
            <Skel w="60%" h={12} style={{ marginTop: 12 }} />
            <Skel w={120} h={12} style={{ marginTop: 26 }} />
          </div>
        ))}
      </div>

      <div className={`${shared.tile} ${styles.plot}`}>
        <Skel w={110} h={15} />
        <Skel w="100%" h={1} style={{ marginTop: 64 }} />
        <div className={styles.ends}><Skel w={36} h={11} /><Skel w={36} h={11} /></div>
      </div>

      <div className={styles.heading}><Skel w={72} h={17} /></div>
      <div className={styles.thesis}>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={`${shared.tile} ${styles.card}`}>
            <Skel w={48} h={14} />
            <Skel w="70%" h={12} style={{ marginTop: 12 }} />
            <Skel w="100%" h={12} style={{ marginTop: 12 }} />
            <Skel w="80%" h={12} style={{ marginTop: 8 }} />
          </div>
        ))}
      </div>

      <div className={styles.heading}><Skel w={110} h={17} /></div>
      <div className={shared.tile}>
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={styles.row}>
            <Skel w="22%" h={14} />
            <Skel w={44} h={22} />
            <Skel w="12%" h={14} />
            <Skel w="24%" h={12} />
          </div>
        ))}
      </div>
    </div>
  )
}
