import type { CSSProperties, ReactNode } from 'react'

interface RevealProps {
  /** stagger 序號；經 CSS min(--tf-i, --tf-stagger-cap) 封頂。 */
  index?: number
  /** 進場變體：'up'＝淡入上浮（預設）、'fade'＝純淡入、'scale'＝淡入微縮放。 */
  variant?: 'up' | 'fade' | 'scale'
  as?: 'div' | 'li' | 'section' | 'article'
  className?: string
  style?: CSSProperties
  children: ReactNode
}

const REVEAL_CLASS = { up: 'tf-reveal', fade: 'tf-reveal-fade', scale: 'tf-reveal-scale' } as const

/** 進場淡入（tf-up 家族）的語法糖；亦可直接對元素加 class="tf-reveal"/"tf-reveal-fade"/"tf-reveal-scale" 與 style={{'--tf-i': i}}。 */
export function Reveal({ index = 0, variant = 'up', as: Tag = 'div', className, style, children }: RevealProps) {
  return (
    <Tag
      className={`${REVEAL_CLASS[variant]} ${className ?? ''}`.trim()}
      style={{ ['--tf-i' as string]: index, ...style }}
    >
      {children}
    </Tag>
  )
}
