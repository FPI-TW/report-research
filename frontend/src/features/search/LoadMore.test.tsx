import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoadMore } from './LoadMore'

describe('LoadMore', () => {
  it('顯示剩餘數、點擊回呼', () => {
    const onClick = vi.fn()
    render(<LoadMore remaining={30} loading={false} onClick={onClick} />)
    expect(screen.getByRole('button', { name: /還有 30 篇/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalled()
  })
  it('loading 時停用', () => {
    render(<LoadMore remaining={30} loading onClick={() => {}} />)
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })
})
