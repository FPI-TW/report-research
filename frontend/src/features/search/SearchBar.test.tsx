import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { SearchBar } from './SearchBar'

describe('SearchBar', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('打字不送出，Enter 才送出 trim 後的字', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '  台積電  ' } })
    act(() => { vi.advanceTimersByTime(2000) })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('台積電')
  })

  it('IME 組字中的 Enter 不送出', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '注音' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('清除鈕送出空字並把焦點留在輸入框', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="AI" onSubmit={onSubmit} />)
    fireEvent.click(screen.getByLabelText('清除搜尋'))
    expect(onSubmit).toHaveBeenCalledWith('')
    expect(screen.getByLabelText('搜尋研報')).toHaveFocus()
    expect(screen.queryByLabelText('清除搜尋')).toBeNull()
  })

  it('外部清空查詢（清除條件）時輸入框跟著清空', () => {
    const { rerender } = render(<SearchBar initial="AI" onSubmit={() => {}} />)
    expect(screen.getByLabelText('搜尋研報')).toHaveValue('AI')
    rerender(<SearchBar initial="" onSubmit={() => {}} />)
    expect(screen.getByLabelText('搜尋研報')).toHaveValue('')
  })
})
