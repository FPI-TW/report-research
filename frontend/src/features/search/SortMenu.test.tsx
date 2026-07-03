import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SortMenu } from './SortMenu'

describe('SortMenu', () => {
  it('顯示目前排序、開選單、選取回呼', () => {
    const onChange = vi.fn()
    render(<SortMenu mode="search" value="relevance" onChange={onChange} />)
    expect(screen.getByText(/排序：相關度/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /排序/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: '日期（新→舊）' }))
    expect(onChange).toHaveBeenCalledWith('date_desc')
  })
  it('browse 模式無 relevance 選項', () => {
    render(<SortMenu mode="browse" value="date_desc" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /排序/ }))
    expect(screen.queryByRole('menuitem', { name: '相關度' })).toBeNull()
  })
})
