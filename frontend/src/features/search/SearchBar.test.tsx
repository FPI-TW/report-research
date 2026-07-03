import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { SearchBar } from './SearchBar'

const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

describe('SearchBar', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('輸入後 debounce 自動送出 trim 後的字（免按 Enter）', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '  台積電  ' } })
    expect(onSubmit).not.toHaveBeenCalled()          // 尚未到期
    advance(350)
    expect(onSubmit).toHaveBeenCalledWith('台積電')
  })

  it('Enter 立即送出並取消 debounce（不重複送）', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.change(input, { target: { value: '台積電' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('台積電')
    advance(350)
    expect(onSubmit).toHaveBeenCalledTimes(1)         // debounce 已取消，不重複
  })

  it('IME 組字中不觸發搜尋；選字完成才送出', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '注音' } })
    advance(350)
    expect(onSubmit).not.toHaveBeenCalled()           // 組字中不排程
    fireEvent.compositionEnd(input, { target: { value: '注音' } })
    advance(350)
    expect(onSubmit).toHaveBeenCalledWith('注音')
  })

  it('IME 組字中的 Enter 不送出', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="" onSubmit={onSubmit} />)
    const input = screen.getByLabelText('搜尋研報')
    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '注音' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('清除鈕立即送出空字（回瀏覽）', () => {
    const onSubmit = vi.fn()
    render(<SearchBar initial="AI" onSubmit={onSubmit} />)
    fireEvent.click(screen.getByLabelText('清除搜尋'))
    expect(onSubmit).toHaveBeenCalledWith('')
  })
})
