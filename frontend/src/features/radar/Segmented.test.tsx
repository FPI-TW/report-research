import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Segmented } from './Segmented'

const OPTIONS = [
  { value: 'a', label: '甲' },
  { value: 'b', label: '乙' },
  { value: 'c', label: '丙' },
] as const

function Harness() {
  const [value, setValue] = useState<'a' | 'b' | 'c'>('a')
  return <Segmented label="測試" options={OPTIONS} value={value} onChange={setValue} itemWidth={60} />
}

describe('Segmented', () => {
  it('是 radiogroup，只有選中那格在 Tab 順序裡', () => {
    render(<Harness />)
    expect(screen.getByRole('radiogroup', { name: '測試' })).toBeInTheDocument()
    const [a, b] = screen.getAllByRole('radio')
    expect(a).toHaveAttribute('aria-checked', 'true')
    expect(a).toHaveAttribute('tabindex', '0')
    expect(b).toHaveAttribute('tabindex', '-1')
  })

  it('滑動指示依選中索引 × 寬度位移', () => {
    const { container } = render(<Harness />)
    fireEvent.click(screen.getByRole('radio', { name: '丙' }))
    const indicator = container.querySelector('[aria-hidden="true"]') as HTMLElement
    expect(indicator.style.transform).toBe('translateX(120px)')
  })

  it('方向鍵移動並選取，兩端循環；Home／End 到兩端', () => {
    render(<Harness />)
    const a = screen.getByRole('radio', { name: '甲' })
    a.focus()
    fireEvent.keyDown(a, { key: 'ArrowLeft' })
    const c = screen.getByRole('radio', { name: '丙' })
    expect(c).toHaveAttribute('aria-checked', 'true')
    expect(c).toHaveFocus()
    fireEvent.keyDown(c, { key: 'ArrowRight' })
    expect(screen.getByRole('radio', { name: '甲' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.keyDown(screen.getByRole('radio', { name: '甲' }), { key: 'End' })
    expect(screen.getByRole('radio', { name: '丙' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.keyDown(screen.getByRole('radio', { name: '丙' }), { key: 'Home' })
    expect(screen.getByRole('radio', { name: '甲' })).toHaveAttribute('aria-checked', 'true')
  })
})
