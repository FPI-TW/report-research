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

// 說明頁曾整整一個里程碑都在教一個已不存在的動線（點結果會「彈出視窗」內嵌 PDF），
// 而說明文字沒有任何機械守門。這條反向釘死那句承諾不再出現。
test('說明頁描述的是研報閱讀頁，不是已移除的彈出視窗', () => {
  render(<HelpPage />)
  expect(screen.getByRole('link', { name: '查看完整報告（閱讀頁）' })).toHaveAttribute('href', '#full')
  expect(screen.getByText(/會開啟該篇的/)).toBeInTheDocument()
  expect(screen.queryByText(/彈出視窗/)).toBeNull()
  expect(screen.queryByText(/點視窗外的灰色區域/)).toBeNull()
})
