import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import HelpPage from './HelpPage'

test('說明頁渲染標題、目錄錨點與實時市場圖例', () => {
  render(<HelpPage />)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('使用說明')
  // 目錄為頁內錨點
  expect(screen.getByRole('link', { name: '智能問答（向 AI 提問）' })).toHaveAttribute('href', '#ask')
  // 市場圖例以實時 chip 呈現（非截圖）
  expect(screen.getByText('台股')).toBeInTheDocument()
  // 「加密」同時出現在市場徽章與商品類型圖例
  expect(screen.getAllByText('加密').length).toBeGreaterThanOrEqual(2)
})
