import React from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { SearchBar } from './SearchBar'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
})

// ── debounce ──────────────────────────────────────────────────────────────────

test('typing debounces onSubmit 450ms', () => {
  const onSubmit = vi.fn()
  wrap(<SearchBar value="" onSubmit={onSubmit} onClear={vi.fn()} />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: 'AI 伺服器' } })
  expect(onSubmit).not.toHaveBeenCalled()
  vi.advanceTimersByTime(449)
  expect(onSubmit).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1)
  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(onSubmit).toHaveBeenCalledWith('AI 伺服器')
})

test('多次連續輸入只觸發一次 onSubmit（最後一次值）', () => {
  const onSubmit = vi.fn()
  wrap(<SearchBar value="" onSubmit={onSubmit} onClear={vi.fn()} />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: 'A' } })
  fireEvent.change(input, { target: { value: 'AB' } })
  fireEvent.change(input, { target: { value: 'ABC' } })
  vi.advanceTimersByTime(450)
  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(onSubmit).toHaveBeenCalledWith('ABC')
})

// ── Enter ─────────────────────────────────────────────────────────────────────

test('Enter 立即觸發 onSubmit 並取消 debounce', () => {
  const onSubmit = vi.fn()
  wrap(<SearchBar value="" onSubmit={onSubmit} onClear={vi.fn()} />)
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '台積電' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(onSubmit).toHaveBeenCalledWith('台積電')
  // debounce 應被取消，不再重複觸發
  vi.advanceTimersByTime(450)
  expect(onSubmit).toHaveBeenCalledTimes(1)
})

// ── clear ─────────────────────────────────────────────────────────────────────

test('有文字時顯示清除按鈕', () => {
  wrap(<SearchBar value="台積電" onSubmit={vi.fn()} onClear={vi.fn()} />)
  expect(screen.getByRole('button', { name: '清除搜尋' })).toBeInTheDocument()
})

test('無文字時不顯示清除按鈕', () => {
  wrap(<SearchBar value="" onSubmit={vi.fn()} onClear={vi.fn()} />)
  expect(screen.queryByRole('button', { name: '清除搜尋' })).toBeNull()
})

test('清除按鈕點擊觸發 onClear', () => {
  const onClear = vi.fn()
  wrap(<SearchBar value="台積電" onSubmit={vi.fn()} onClear={onClear} />)
  screen.getByRole('button', { name: '清除搜尋' }).click()
  expect(onClear).toHaveBeenCalledTimes(1)
})

test('清除後輸入框文字清空', () => {
  wrap(<SearchBar value="台積電" onSubmit={vi.fn()} onClear={vi.fn()} />)
  // 使用 act() 確保 setState('') 提交到 DOM 後再斷言
  act(() => {
    screen.getByRole('button', { name: '清除搜尋' }).click()
  })
  const input = screen.getByRole('textbox') as HTMLInputElement
  expect(input.value).toBe('')
})
