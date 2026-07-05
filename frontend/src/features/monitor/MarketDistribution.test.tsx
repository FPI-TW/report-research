import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MarketDistribution } from './MarketDistribution'
import { marketLabel } from '../../lib/meta'

test('依 count 由大到小、寬度與佔比', () => {
  const { container } = render(<MarketDistribution markets={[{ market: 'US', count: 20 }, { market: 'TW', count: 80 }]} />)
  const rows = [...container.querySelectorAll('[class*="mktRow"]')]
  // 第一列應為 TW（count 大）
  expect(rows[0].textContent).toContain(marketLabel('TW'))
  const fill = rows[0].querySelector('[class*="mktFill"]') as HTMLElement
  expect(fill.style.width).toBe('100%')   // 80/80（DOM 正規化去除多餘 .0）
  expect(screen.getByText('80')).toBeInTheDocument()
  expect(screen.getByText('80%')).toBeInTheDocument()   // 80/100
})

test('空 markets → 顯 —', () => {
  render(<MarketDistribution markets={[]} />)
  expect(screen.getByText('—')).toBeInTheDocument()
})
