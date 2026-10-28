import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { AskEmptyState } from './AskEmptyState'
import { AI_NOTE } from './Composer'

test('標題、浮動輸入框、AI 聲明與 2×2 建議題', () => {
  render(<AskEmptyState value="" onChange={() => {}} onSubmit={() => {}} />)
  expect(screen.getByRole('heading', { name: '今天想研究什麼？' })).toBeInTheDocument()
  expect(screen.getByLabelText('輸入問題')).toHaveAttribute('placeholder', '輸入問題…')
  expect(screen.getByText(AI_NOTE)).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: /台股|外匯|總經|加密/ })).toHaveLength(4)
  // 不加說明性副標
  expect(screen.queryByText(/以自然語言/)).toBeNull()
})

test('點建議題只填進輸入框，不直接送出', () => {
  const onChange = vi.fn(); const onSubmit = vi.fn()
  render(<AskEmptyState value="" onChange={onChange} onSubmit={onSubmit} />)
  fireEvent.click(screen.getByRole('button', { name: /台積電 2027 年的資本支出/ }))
  expect(onChange).toHaveBeenCalledWith('台積電 2027 年的資本支出，各家券商怎麼看？')
  expect(onSubmit).not.toHaveBeenCalled()
})
