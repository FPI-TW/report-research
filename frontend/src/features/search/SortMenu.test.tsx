import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SortMenu } from './SortMenu'

describe('SortMenu', () => {
  it('search 顯示相關度；選單只有相關度、無日期選項', () => {
    const onChange = vi.fn()
    render(<SortMenu mode="search" value="relevance" onChange={onChange} />)
    expect(screen.getByText(/排序：相關度/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /排序/ }))
    expect(screen.queryByRole('menuitem', { name: /日期/ })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: '相關度' }))
    expect(onChange).toHaveBeenCalledWith('relevance')
  })
  it('browse 模式無可選排序 → 不顯示排序鈕', () => {
    const { container } = render(<SortMenu mode="browse" value="date_desc" onChange={() => {}} />)
    expect(container.querySelector('button')).toBeNull()
  })
})
