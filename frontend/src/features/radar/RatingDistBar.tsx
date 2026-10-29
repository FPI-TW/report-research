import type { RatingPart } from './consensusData'
import styles from './RatingDistBar.module.css'

/**
 * 五級評等分布條（紅漲綠跌，深淺分強弱）。各段寬度依家數按比例分配，段間 2px。
 *
 * 純視覺：家數一律另有文字（清單列的分布文字、詳情卡的圖例），所以條本身對輔助技術隱藏。
 */
export function RatingDistBar({ parts, height = 6 }: { parts: readonly RatingPart[]; height?: number }) {
  if (!parts.length) return null
  return (
    <span className={styles.bar} style={{ height }} aria-hidden="true">
      {parts.map(p => (
        <span key={p.rating} className={styles[p.rating]} style={{ flexGrow: p.count }} />
      ))}
    </span>
  )
}
