import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ViewSwitch } from './ViewSwitch'

describe('ViewSwitch', () => {
  it('切表格 → onChange(table)、active 標示', () => {
    const onChange = vi.fn()
    render(<ViewSwitch view="cards" onChange={onChange} />)
    // animate-ui Tabs：trigger 為 role=tab，active 以 data-state 標示（取代舊 aria-pressed）
    expect(screen.getByRole('tab', { name: '列表檢視' })).toHaveAttribute('data-state', 'active')
    expect(screen.getByRole('tab', { name: '表格檢視' })).toHaveAttribute('data-state', 'inactive')
    fireEvent.click(screen.getByRole('tab', { name: '表格檢視' }))
    expect(onChange).toHaveBeenCalledWith('table')
  })
})
