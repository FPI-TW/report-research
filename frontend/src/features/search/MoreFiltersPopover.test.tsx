import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MoreFiltersPopover } from './MoreFiltersPopover'
import { defaultState } from '../../lib/searchFilters'

// options 為後端原碼；pill 顯示中文，但送出的值仍是原碼。
const base = {
  instrumentOptions: ['equity', 'futures'],
  reportTypeOptions: ['memo', '策略'],
}
const openPopover = () => fireEvent.click(screen.getByRole('button', { name: /更多篩選/ }))

describe('MoreFiltersPopover', () => {
  it('badge 顯示已套用的進階篩選數', () => {
    render(<MoreFiltersPopover {...base}
      state={{ ...defaultState(), report_type: '策略', relates_stock: true }}
      onApply={() => {}} />)
    expect(screen.getByText('2')).toBeTruthy()
  })

  it('商品類型 pill 顯示中文（equity→股票），套用送出原碼', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base} state={defaultState()} onApply={onApply} />)
    openPopover()
    fireEvent.click(screen.getByRole('button', { name: '股票' }))   // 顯示中文
    fireEvent.click(screen.getByRole('button', { name: '套用' }))
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ instrument_type: 'equity' }))
  })

  it('報告類型英文碼顯示中文（memo→備忘），套用送出原碼', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base} state={defaultState()} onApply={onApply} />)
    openPopover()
    fireEvent.click(screen.getByRole('button', { name: '備忘' }))
    fireEvent.click(screen.getByRole('button', { name: '套用' }))
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ report_type: 'memo' }))
  })

  it('個股相關 pill → 套用 → onApply(relates_stock:true)', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base} state={defaultState()} onApply={onApply} />)
    openPopover()
    fireEvent.click(screen.getByRole('button', { name: '個股相關' }))
    fireEvent.click(screen.getByRole('button', { name: '套用' }))
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ relates_stock: true }))
  })

  it('套用前不觸發 onApply（apply-on-button 語意）', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base} state={defaultState()} onApply={onApply} />)
    openPopover()
    fireEvent.click(screen.getByRole('button', { name: '股票' }))
    expect(onApply).not.toHaveBeenCalled()
  })

  it('開啟時以已套用狀態種入草稿，pill 呈 aria-pressed 且可反選', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base}
      state={{ ...defaultState(), instrument_type: 'equity' }} onApply={onApply} />)
    openPopover()
    const pill = screen.getByRole('button', { name: '股票' })
    expect(pill.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(pill)                                   // 反選
    fireEvent.click(screen.getByRole('button', { name: '套用' }))
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ instrument_type: '' }))
  })

  it('清除條件 → 套用 → onApply 清空進階篩選', () => {
    const onApply = vi.fn()
    render(<MoreFiltersPopover {...base}
      state={{ ...defaultState(), instrument_type: 'equity', relates_stock: true }}
      onApply={onApply} />)
    openPopover()
    fireEvent.click(screen.getByRole('button', { name: '清除條件' }))
    fireEvent.click(screen.getByRole('button', { name: '套用' }))
    expect(onApply).toHaveBeenCalledWith({
      instrument_type: '', report_type: '', relates_stock: false, relates_futures: false,
    })
  })
})
