import type { CSSProperties } from 'react'
import styles from './Skeleton.module.css'

interface SkeletonProps {
  width?: number | string
  height?: number | string
  radius?: number | string
  variant?: 'block' | 'text' | 'circle'
  className?: string
  style?: CSSProperties
}

/** 骨架屏佔位塊（pulse，reduced-motion 下成靜態灰塊）。恆 aria-hidden；aria-busy 由外層負責。 */
export function Skeleton({ width, height, radius, variant = 'block', className, style }: SkeletonProps) {
  const cls = [styles.sk, variant !== 'block' && styles[variant], className].filter(Boolean).join(' ')
  return (
    <span
      aria-hidden="true"
      className={cls}
      style={{ width, height, borderRadius: radius, ...style }}
    />
  )
}
