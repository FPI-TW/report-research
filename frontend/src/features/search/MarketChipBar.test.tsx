import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MarketChipBar } from './MarketChipBar'

describe('MarketChipBar', () => {
  it('渲染 全部 + 各市場、標示 active', () => {
    render(<MarketChipBar value="TW" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: '全部' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '台股' }).getAttribute('aria-pressed')).toBe('true')
  })
  it('點擊 → onChange(code)', () => {
    const onChange = vi.fn()
    render(<MarketChipBar value="ALL" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '美股' }))
    expect(onChange).toHaveBeenCalledWith('US')
  })
  it('顯示篇數', () => {
    render(<MarketChipBar value="ALL" onChange={() => {}} counts={{ TW: 500 }} />)
    expect(screen.getByRole('button', { name: '台股 500' })).toBeTruthy()
  })
})
