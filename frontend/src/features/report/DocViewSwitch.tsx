import { motion } from 'motion/react'
import { Pressable } from '../../components/primitives/Pressable'
import { springThumb } from '../../lib/motionTokens'
import styles from './DocViewSwitch.module.css'

export type DocView = 'pdf' | 'text'

const OPTIONS: { value: DocView; label: string }[] = [
  { value: 'pdf', label: '原文' },
  { value: 'text', label: '文字' },
]

interface Props {
  value: DocView
  onChange: (v: DocView) => void
}

/** [原文][文字] 分段控制；滑動 thumb 走 layoutId（layoutId 需全站唯一）。 */
export function DocViewSwitch({ value, onChange }: Props) {
  return (
    <div className={styles.seg} role="radiogroup" aria-label="文件檢視">
      {OPTIONS.map(opt => {
        const active = opt.value === value
        return (
          <Pressable
            key={opt.value}
            role="radio"
            aria-checked={active}
            hoverScale={1}
            className={`${styles.btn} ${active ? styles.active : ''}`}
            onClick={() => onChange(opt.value)}
          >
            {active && (
              <motion.span
                layoutId="doc-view-thumb"
                className={styles.thumb}
                transition={springThumb}
                aria-hidden="true"
              />
            )}
            <span className={styles.label}>{opt.label}</span>
          </Pressable>
        )
      })}
    </div>
  )
}
