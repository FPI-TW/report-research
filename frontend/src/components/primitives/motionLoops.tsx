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
