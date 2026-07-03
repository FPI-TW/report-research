import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SearchBar } from './SearchBar'

describe('SearchBar', () => {
  it('Enter 送出 trim 後的字', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '  台積電  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
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
  it('清除鈕送出空字（回瀏覽）', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="AI" onSubmit={onSubmit} />)
    fireEvent.click(screen.getByLabelText('清除搜尋'))
    expect(onSubmit).toHaveBeenCalledWith('')
  })
})
