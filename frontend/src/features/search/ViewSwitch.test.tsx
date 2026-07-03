import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ViewSwitch } from './ViewSwitch'

describe('ViewSwitch', () => {
  it('切表格 → onChange(table)、active 標示', () => {
    const onChange = vi.fn()
    render(<ViewSwitch view="cards" onChange={onChange} />)
    expect(screen.getByRole('button', { name: '卡片檢視' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: '表格檢視' }))
    expect(onChange).toHaveBeenCalledWith('table')
  })
})
