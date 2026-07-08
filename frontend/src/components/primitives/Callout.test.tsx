import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Callout } from './Callout'

test('error variant 顯示內容與重試 action', () => {
  const onClick = vi.fn()
  render(<Callout variant="error" action={{ label: '重試', onClick }}>查詢逾時或失敗</Callout>)
  const box = screen.getByRole('alert')
  expect(box).toHaveTextContent('查詢逾時或失敗')
  expect(box.className).toMatch(/error/)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(onClick).toHaveBeenCalledOnce()
})

test('warning variant 無 action 時不渲染按鈕', () => {
  render(<Callout variant="warning">無法回答此問題</Callout>)
  expect(screen.getByRole('alert').className).toMatch(/warning/)
  expect(screen.queryByRole('button')).toBeNull()
})
