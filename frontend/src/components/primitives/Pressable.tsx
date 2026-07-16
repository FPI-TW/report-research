import { motion, type HTMLMotionProps } from 'motion/react'

type PressableTag = 'button' | 'a' | 'div' | 'span'

type PressableProps = HTMLMotionProps<'button'> & {
  /** 底層元素；預設 button。連結傳 'a'（配 href），可點卡片/列傳 'div'。 */
  as?: PressableTag
  /** hover 縮放（省略＝不放大；與 lift 互斥）。 */
  hoverScale?: number
  /** 按壓縮放；預設 0.96。 */
  tapScale?: number
  /** 卡片型：hover 上抬 2px、press 微縮 0.98。 */
  lift?: boolean
  // 連結型（as='a'）常用屬性
  href?: string
  target?: string
  rel?: string
  download?: boolean | string
}

/**
 * 全站互動元件：以 Motion whileHover/whileTap 提供一致的懸停/按壓回饋，取代 CSS :active/hover-scale。
 * 透傳 className（沿用各自 CSS Module 樣式）與全部 props。scale/y 為位移類動畫，
 * 全域 MotionConfig reducedMotion="user" 會於減少動態時自動停用，故免逐一加 guard。
 */
export function Pressable({ as = 'button', hoverScale, tapScale = 0.96, lift, children, ...props }: PressableProps) {
  const Comp = motion[as] as typeof motion.button
  const whileHover = lift ? { y: -2 } : hoverScale ? { scale: hoverScale } : undefined
  const whileTap = lift ? { scale: 0.98 } : { scale: tapScale }
  const typeProp = as === 'button' ? { type: 'button' as const } : {}
  return (
    <Comp whileHover={whileHover} whileTap={whileTap} {...typeProp} {...props}>
      {children}
    </Comp>
  )
}
