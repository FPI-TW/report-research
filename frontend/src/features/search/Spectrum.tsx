import { motion, useReducedMotion } from 'motion/react'
import type { CSSProperties } from 'react'
import { marketColor, marketLabel } from '../../lib/meta'
import { TF_EASE_OUT, TF_STAGGER, TF_STAGGER_CAP } from '../../lib/motionTokens'
import styles from './Spectrum.module.css'

export interface SpectrumSlice { market: string; count: number }

interface Props {
  slices: SpectrumSlice[]
  /** 無障礙描述前綴，例如「語料庫市場組成」。 */
  label: string
  className?: string
}

/**
 * 市場色譜：段寬嚴格正比於計數（flex-grow = count），是讀數不是控制項。
 * 顏色與清單列的市場色標同一套語言——一個講總體比例，一個講逐列身分。
 */
export function Spectrum({ slices, label, className }: Props) {
  const reduced = useReducedMotion()
  const desc = slices
    .map(s => `${marketLabel(s.market)} ${s.count.toLocaleString()}`)
    .join('、')

  return (
    <div
      className={`${styles.spectrum}${className ? ` ${className}` : ''}`}
      role="img"
      aria-label={`${label}：${desc}`}
    >
      {slices.map((s, i) => (
        <motion.span
          key={s.market}
          className={styles.seg}
          style={{ background: marketColor(s.market), flexGrow: s.count } as CSSProperties}
          initial={reduced ? false : { scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={
            reduced
              ? { duration: 0 }
              : { duration: 0.64, ease: TF_EASE_OUT, delay: Math.min(i, TF_STAGGER_CAP) * TF_STAGGER }
          }
        />
      ))}
    </div>
  )
}
