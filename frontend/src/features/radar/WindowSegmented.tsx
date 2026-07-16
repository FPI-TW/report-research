import { motion } from 'motion/react'
import { Pressable } from '../../components/primitives/Pressable'
import { springThumb } from '../../lib/motionTokens'
import type { Window } from '../../lib/radarSchemas'
import { WINDOW_OPTIONS } from './radarFormat'
import styles from './WindowSegmented.module.css'

interface Props {
  value: Window
  onChange: (w: Window) => void
}

export function WindowSegmented({ value, onChange }: Props) {
  return (
    <div className={styles.seg} role="radiogroup" aria-label="窗期">
      {WINDOW_OPTIONS.map(opt => {
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
            {active && <motion.span layoutId="window-thumb" className={styles.thumb} transition={springThumb} aria-hidden="true" />}
            <span className={styles.label}>{opt.label}</span>
          </Pressable>
        )
      })}
    </div>
  )
}
