import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ActiveChips } from './ActiveChips'
import { defaultState } from '../../lib/searchFilters'

describe('ActiveChips', () => {
  it('無作用中篩選 → 不渲染任何 chip', () => {
    const { container } = render(<ActiveChips state={defaultState()} onPatch={() => {}} />)
    expect(container.querySelectorAll('button').length).toBe(0)
  })
  it('市場 chip 可移除 → onPatch(market ALL)', () => {
    const onPatch = vi.fn()
    render(<ActiveChips state={{ ...defaultState(), market: 'TW' }} onPatch={onPatch} />)
    fireEvent.click(screen.getByRole('button', { name: /台股/ }))
    expect(onPatch).toHaveBeenCalledWith({ market: 'ALL' })
  })
  it('個股 chip 可移除 → onPatch(relates_stock false)', () => {
    const onPatch = vi.fn()
    render(<ActiveChips state={{ ...defaultState(), relates_stock: true }} onPatch={onPatch} />)
    fireEvent.click(screen.getByRole('button', { name: /個股/ }))
    expect(onPatch).toHaveBeenCalledWith({ relates_stock: false })
  })
})
