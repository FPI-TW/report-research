import type { CSSProperties, ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'

/**
 * 裝飾性無限迴圈的 Motion 版，取代 CSS @keyframes（tf-spin/tf-pulse/tf-indet/tf-caret…）。
 * 全部以 useReducedMotion() 在減少動態時退回靜態（與舊 CSS blanket 行為一致）。
 */

interface SpinProps { children: ReactNode; duration?: number; className?: string; style?: CSSProperties }
/** 旋轉載入：等速無限自轉，包裹一個圖示。 */
export function Spin({ children, duration = 0.8, className, style }: SpinProps) {
  const reduced = useReducedMotion()
  return (
    <motion.span
      className={className}
      style={{ display: 'inline-flex', ...style }}
      animate={reduced ? undefined : { rotate: 360 }}
      transition={reduced ? undefined : { repeat: Infinity, ease: 'linear', duration }}
    >
      {children}
    </motion.span>
  )
}

interface PulseProps { children?: ReactNode; min?: number; duration?: number; className?: string; style?: CSSProperties }
/** 沉靜呼吸：opacity 1→min→1 無限；用於 liveDot、skeleton、串流本文示意。 */
export function Pulse({ children, min = 0.4, duration = 1.05, className, style }: PulseProps) {
  const reduced = useReducedMotion()
  return (
    <motion.span
      aria-hidden={children ? undefined : true}
      className={className}
      style={{ display: 'inline-flex', ...style }}
      animate={reduced ? undefined : { opacity: [1, min, 1] }}
      transition={reduced ? undefined : { repeat: Infinity, ease: 'easeInOut', duration }}
    >
      {children}
    </motion.span>
  )
}

interface SweepProps { className?: string; barClassName?: string; duration?: number }
/** 不定量掃光：軌道內一條金色漸層條無限橫掃（translateX，相對自身寬度）。 */
export function Sweep({ className, barClassName, duration = 1.4 }: SweepProps) {
  const reduced = useReducedMotion()
  return (
    <span className={className} aria-hidden="true">
      <motion.span
        className={barClassName}
        animate={reduced ? undefined : { x: ['-100%', '300%'] }}
        transition={reduced ? undefined : { repeat: Infinity, ease: 'easeInOut', duration }}
      />
    </span>
  )
}

interface CaretProps { className?: string; style?: CSSProperties }
/** 打字游標：細直條的沉靜呼吸（opacity），取代 ::after 硬閃。 */
export function Caret({ className, style }: CaretProps) {
  const reduced = useReducedMotion()
  return (
    <motion.span
      aria-hidden="true"
      className={className}
      style={{ display: 'inline-block', ...style }}
      animate={reduced ? undefined : { opacity: [1, 0.2, 1] }}
      transition={reduced ? undefined : { repeat: Infinity, ease: 'easeInOut', duration: 1.05 }}
    />
  )
}
