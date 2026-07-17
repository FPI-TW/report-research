import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DirectionTag } from './DirectionTag'

describe('DirectionTag', () => {
  it('上修顯示圖示與文字與百分比（語意不只靠色）', () => {
    const { container } = render(
      <DirectionTag direction="up" pct={3.2} />,
    )
    expect(screen.getByText('上修')).toBeInTheDocument()
    expect(screen.getByText('3.2%')).toBeInTheDocument()
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('不可比較顯示對應標籤與圖示', () => {
    const { container } = render(
      <DirectionTag direction="incomparable" label="EPS" pct={99.9} />,
    )
    expect(screen.getByText('不可比較')).toBeInTheDocument()
    expect(screen.queryByText('EPS')).not.toBeInTheDocument()
    expect(screen.queryByText('99.9%')).not.toBeInTheDocument()
    expect(container.querySelector('svg')).toBeTruthy()
  })

  it('none 無標籤時顯示破折號', () => {
    render(<DirectionTag direction="none" />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
