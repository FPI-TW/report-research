import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrokerDistribution } from './BrokerDistribution'
import type { SourceCount } from './progressSchema'

const SOURCES: SourceCount[] = [
  { source: 'masterlink', display: '元富', count: 20, latest: '2025-08-04' },
  { source: 'kgi', display: '凱基', count: 80, latest: '2026-08-04' },
  { source: null, display: null, count: 10, latest: '2026-07-31' },
]

test('依篇數由大到小，顯示中文名、篇數與最新日期', () => {
  const { container } = render(<BrokerDistribution sources={SOURCES} />)
  const rows = [...container.querySelectorAll('[class*="brkRow"]')]
  expect(rows).toHaveLength(3)
  // 後端已 ORDER BY，但排序是本元件的視覺契約，不靠上游維持
  expect(rows[0].textContent).toContain('凱基')
  expect(rows[0].textContent).toContain('2026-08-04')
  const fill = rows[0].querySelector('[class*="brkFill"]') as HTMLElement
  expect(fill.style.width).toBe('100%')   // 80/80
  expect(screen.getByText('80')).toBeInTheDocument()
})

test('NULL source → 標「未辨識」，且計入總篇數但不計入券商家數', () => {
  render(<BrokerDistribution sources={SOURCES} />)
  expect(screen.getByText('未辨識')).toBeInTheDocument()
  // 3 列裡只有 2 家是真的券商；110 篇是三列相加（分母必須等於 db.reports）
  expect(screen.getByText(/共 2 家券商 · 110 篇/)).toBeInTheDocument()
  expect(screen.getByText(/未辨識 10 篇/)).toBeInTheDocument()
})

test('latest 為 null → 顯 —（不是空白）', () => {
  const { container } = render(
    <BrokerDistribution sources={[{ source: 'kgi', display: '凱基', count: 1, latest: null }]} />,
  )
  const row = container.querySelector('[class*="brkRow"]') as HTMLElement
  expect(row.textContent).toContain('—')
})

test('對照表未收錄的代碼 → 用後端原樣回傳的 display，不會變成未辨識', () => {
  render(<BrokerDistribution sources={[{ source: 'newbroker', display: 'newbroker', count: 3, latest: null }]} />)
  expect(screen.getByText('newbroker')).toBeInTheDocument()
  expect(screen.queryByText('未辨識')).toBeNull()
})

// 三態必須分得開：undefined（舊後端缺欄位）與 []（真的沒有研報）若都塌成同一個
// 畫面，滾動部署期間的缺欄位會長得跟空語料一模一樣，沒有人分得出來。
test('undefined（舊後端）→ 降級文案，卡片不消失', () => {
  render(<BrokerDistribution sources={undefined} />)
  expect(screen.getByText('券商分佈')).toBeInTheDocument()
  expect(screen.getByText('此版後端未提供券商統計')).toBeInTheDocument()
})

test('空陣列 → 顯 —，與 undefined 的文案不同', () => {
  render(<BrokerDistribution sources={[]} />)
  expect(screen.getByText('—')).toBeInTheDocument()
  expect(screen.queryByText('此版後端未提供券商統計')).toBeNull()
})
