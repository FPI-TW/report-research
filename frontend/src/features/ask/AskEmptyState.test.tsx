import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, test } from 'vitest'
import { AskEmptyState } from './AskEmptyState'

function renderEmpty() {
  return render(
    <MemoryRouter initialEntries={['/ask']}>
      <AskEmptyState value="" onChange={() => {}} onSubmit={() => {}} />
    </MemoryRouter>,
  )
}

test('顯示標題/副標/輸入框，無範例膠囊', () => {
  renderEmpty()
  expect(screen.getByText('向廷豐智能體提問')).toBeInTheDocument()
  expect(screen.getByText(/以自然語言詢問研究主題/)).toBeInTheDocument()
  expect(screen.getByPlaceholderText('輸入你的問題…')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /台積電|AI 伺服器|Fed/ })).toBeNull()
})

test('含檢索↔問答切換鈕：問答 active、檢索連向 /search', () => {
  renderEmpty()
  expect(screen.getByText('智能問答').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /檢索研報/ })).toHaveAttribute('href', '/search')
})
