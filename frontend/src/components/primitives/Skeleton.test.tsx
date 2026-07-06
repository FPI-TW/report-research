import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Skeleton } from './Skeleton'

describe('Skeleton', () => {
  it('渲染 aria-hidden 佔位塊並套用尺寸', () => {
    const { container } = render(<Skeleton width={120} height={26} />)
    const el = container.querySelector('span')!
    expect(el.getAttribute('aria-hidden')).toBe('true')
    expect(el.style.width).toBe('120px')
    expect(el.style.height).toBe('26px')
  })

  it('variant=block 不產生 undefined class', () => {
    const { container } = render(<Skeleton variant="block" />)
    expect(container.querySelector('span')!.className).not.toContain('undefined')
  })
})
