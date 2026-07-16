import { Pressable } from '../../components/primitives/Pressable'
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
            className={`${styles.btn} ${active ? styles.active : ''}`}
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </Pressable>
        )
      })}
    </div>
  )
}
