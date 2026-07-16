import type { CSSProperties, ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { revealTransition, revealVariantsFor, type RevealVariant } from '../../lib/motionTokens'

type RevealTag = 'div' | 'li' | 'section' | 'article'

interface RevealProps {
  /** stagger 序號；delay 經 min(index, cap) 封頂。 */
  index?: number
  /** 進場變體：up（預設）/fade/scale。 */
  variant?: RevealVariant
  as?: RevealTag
  /** 進入視窗才播（一次性）；預設 false＝掛載即播。 */
  inView?: boolean
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/** 進場動效（原 .tf-reveal 的 Motion 版）；reduced-motion 由 useReducedMotion() 直接呈現終態。 */
export function Reveal({ index = 0, variant = 'up', as = 'div', inView = false, className, style, children }: RevealProps) {
  const reduced = useReducedMotion()
  const Comp = motion[as] as typeof motion.div
  const animateProps = inView
    ? { whileInView: 'visible' as const, viewport: { once: true, margin: '0px 0px -10% 0px' } }
    : { animate: 'visible' as const }
  return (
    <Comp
      className={className}
      style={style}
      variants={revealVariantsFor(variant)}
      initial="hidden"
      {...animateProps}
      transition={revealTransition(reduced, index)}
    >
      {children}
    </Comp>
  )
}
