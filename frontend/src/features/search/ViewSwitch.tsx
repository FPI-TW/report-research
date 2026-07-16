import { motion } from 'motion/react'
import { Pressable } from '../../components/primitives/Pressable'
import { springThumb } from '../../lib/motionTokens'
import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

const OPTIONS: { v: ViewMode; label: string; aria: string }[] = [
  { v: 'cards', label: '≣', aria: '列表檢視' },
  { v: 'table', label: '▦', aria: '表格檢視' },
]

/** 檢視切換：位於工具列的分段控制（cards＝高密度列表、table＝表格）。active thumb 以 layoutId 於兩鍵間滑移。 */
export function ViewSwitch({ view, onChange }: Props) {
  return (
    <div className={styles.wrap} role="group" aria-label="檢視切換">
      {OPTIONS.map(o => {
        const active = view === o.v
        return (
          <Pressable
            key={o.v}
            aria-label={o.aria}
            aria-pressed={active}
            hoverScale={1}
            className={`${styles.btn} ${active ? styles.active : ''}`}
            onClick={() => onChange(o.v)}
          >
            {active && <motion.span layoutId="view-thumb" className={styles.thumb} transition={springThumb} aria-hidden="true" />}
            <span className={styles.label}>{o.label}</span>
          </Pressable>
        )
      })}
    </div>
  )
}
