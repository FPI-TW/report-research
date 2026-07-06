import type { CSSProperties, ReactNode } from 'react'

interface RevealProps {
  /** stagger 序號；經 CSS min(--tf-i, --tf-stagger-cap) 封頂。 */
  index?: number
  as?: 'div' | 'li' | 'section' | 'article'
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/** 進場淡入＋上浮（tf-up）的語法糖；亦可直接對元素加 class="tf-reveal" 與 style={{'--tf-i': i}}。 */
export function Reveal({ index = 0, as: Tag = 'div', className, style, children }: RevealProps) {
  return (
    <Tag
      className={`tf-reveal ${className ?? ''}`.trim()}
      style={{ ['--tf-i' as string]: index, ...style }}
    >
      {children}
    </Tag>
  )
}
