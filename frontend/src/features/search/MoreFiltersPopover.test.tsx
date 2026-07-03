import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { defaultState } from '../../lib/searchFilters'

const base = { state: defaultState(), instrumentOptions: ['股票', '期貨'], reportTypeOptions: ['個股報告'] }

describe('MoreFiltersPopover', () => {
  it('badge 顯示作用中進階篩選數', () => {
    render(<MoreFiltersPopover {...base} state={{ ...defaultState(), report_type: '個股報告', relates_stock: true }}
      onPatch={() => {}} onClear={() => {}} />)
    expect(screen.getByText('2')).toBeTruthy()
  })
  it('切換商品類型 → onPatch', () => {
    const onPatch = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={onPatch} onClear={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.change(screen.getByLabelText('商品類型'), { target: { value: '股票' } })
    expect(onPatch).toHaveBeenCalledWith({ instrument_type: '股票' })
  })
  it('個股 checkbox → onPatch(relates_stock)', () => {
    const onPatch = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={onPatch} onClear={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.click(screen.getByLabelText('只看個股相關'))
    expect(onPatch).toHaveBeenCalledWith({ relates_stock: true })
  })
  it('清除 → onClear', () => {
    const onClear = vi.fn()
    render(<MoreFiltersPopover {...base} onPatch={() => {}} onClear={onClear} />)
    fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))
    fireEvent.click(screen.getByRole('button', { name: '清除篩選' }))
    expect(onClear).toHaveBeenCalled()
  })
})
