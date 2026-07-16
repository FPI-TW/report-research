import type { CSSProperties, ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { revealTransition, revealVariants } from '../../lib/motionTokens'

type RevealTag = 'div' | 'li' | 'section' | 'article'

interface RevealProps {
  /** stagger 序號；delay 經 min(index, cap) 封頂。 */
  index?: number
  as?: RevealTag
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/** 進場淡入＋上浮（原 .tf-reveal 的 Motion 版）；reduced-motion 由 useReducedMotion() 直接呈現終態。 */
export function Reveal({ index = 0, as = 'div', className, style, children }: RevealProps) {
  const reduced = useReducedMotion()
  const Comp = motion[as] as typeof motion.div
  return (
    <Comp
      className={className}
      style={style}
      variants={revealVariants}
      initial="hidden"
      animate="visible"
      transition={revealTransition(reduced, index)}
    >
      {children}
    </Comp>
  )
}
