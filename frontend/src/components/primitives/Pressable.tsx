import { motion, type HTMLMotionProps } from 'motion/react'

type PressableProps = HTMLMotionProps<'button'> & {
  /** hover 時的縮放（省略＝不放大）。 */
  hoverScale?: number
  /** 按壓時的縮放；預設 0.96。 */
  tapScale?: number
}

/**
 * 帶按壓/懸停回饋的 motion 按鈕，透傳 className（沿用各自 CSS Module 樣式）與全部 props。
 * scale 為位移類動畫，全域 MotionConfig reducedMotion="user" 會於減少動態時自動停用，故免逐一加 guard。
 */
export function Pressable({ hoverScale, tapScale = 0.96, children, ...props }: PressableProps) {
  return (
    <motion.button
      type="button"
      whileHover={hoverScale ? { scale: hoverScale } : undefined}
      whileTap={{ scale: tapScale }}
      {...props}
    >
      {children}
    </motion.button>
  )
}
