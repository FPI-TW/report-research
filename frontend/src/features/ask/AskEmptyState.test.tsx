import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { AskEmptyState } from './AskEmptyState'

test('顯示標題/副標/輸入框，無範例膠囊', () => {
  render(<AskEmptyState value="" onChange={() => {}} onSubmit={() => {}} />)
  expect(screen.getByText('向廷豐智能體提問')).toBeInTheDocument()
  expect(screen.getByText(/以自然語言詢問研究主題/)).toBeInTheDocument()
  expect(screen.getByPlaceholderText('輸入你的問題…')).toBeInTheDocument()
  // 無範例膠囊：不應出現任何「範例」按鈕
  expect(screen.queryByRole('button', { name: /台積電|AI 伺服器|Fed/ })).toBeNull()
})
