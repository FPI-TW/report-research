import { Icon } from '../../components/primitives/Icon'
import type { Direction } from '../../lib/radarSchemas'
import { directionIconName, directionVerb, fmtPct } from './radarFormat'
import styles from './DirectionTag.module.css'

interface Props {
  direction: Direction
  /** 覆蓋預設動詞（如「上調」vs「上修」） */
  label?: string
  pct?: number | null
  kind?: 'rating' | 'number'
  className?: string
}

/** 方向標籤：圖示 + 文字（+ 可選百分比）。升降不可只靠顏色。 */
export function DirectionTag({ direction, label, pct, kind = 'number', className }: Props) {
  if (direction === 'none' && !label) return <span className={styles.none}>—</span>
  const icon = directionIconName(direction)
  const text = direction === 'incomparable'
    ? directionVerb(direction, kind)
    : (label ?? directionVerb(direction, kind))
  const pctText = direction === 'incomparable' ? '' : (pct != null ? fmtPct(pct) : '')
  const tone = direction === 'up' || direction === 'down' || direction === 'flat' || direction === 'incomparable'
    ? styles[direction]
    : styles.none
  return (
    <span className={`${styles.tag} ${tone} ${className ?? ''}`}>
      {icon ? <Icon name={icon} size={13} className={styles.icon} /> : null}
      {text ? <span className={styles.label}>{text}</span> : null}
      {pctText ? <span className={styles.pct}>{pctText}</span> : null}
    </span>
  )
}
