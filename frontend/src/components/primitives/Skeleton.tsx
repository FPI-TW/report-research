import type { CSSProperties } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import styles from './Skeleton.module.css'

interface SkeletonProps {
  width?: number | string
  height?: number | string
  radius?: number | string
  variant?: 'block' | 'text' | 'circle'
  className?: string
  style?: CSSProperties
}

/** 骨架屏佔位塊（Motion opacity pulse，reduced-motion 下成靜態灰塊）。恆 aria-hidden；aria-busy 由外層負責。 */
export function Skeleton({ width, height, radius, variant = 'block', className, style }: SkeletonProps) {
  const reduced = useReducedMotion()
  const cls = [styles.sk, variant !== 'block' && styles[variant], className].filter(Boolean).join(' ')
  return (
    <motion.span
      aria-hidden="true"
      className={cls}
      style={{ width, height, borderRadius: radius, ...style }}
      animate={reduced ? undefined : { opacity: [1, 0.35, 1] }}
      transition={reduced ? undefined : { repeat: Infinity, ease: 'easeInOut', duration: 1.4 }}
    />
  )
}
