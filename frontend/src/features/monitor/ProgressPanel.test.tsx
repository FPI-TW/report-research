import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProgressPanel } from './ProgressPanel'

test('有資料：done/total/pct + 速率行 + 進度條寬', () => {
  const { container } = render(
    <ProgressPanel title="語意標註" data={{ done: 80, total: 100, pct: 80 }} rateLine="速率 2.0 標註/分 · 預估剩餘 ~10 分" idleText="目前無執行中的標註" />,
  )
  expect(screen.getByText('語意標註')).toBeInTheDocument()
  expect(screen.getByText('80')).toBeInTheDocument()
  expect(screen.getByText('/ 100 篇')).toBeInTheDocument()
  expect(screen.getByText('80.0%')).toBeInTheDocument()
  expect(screen.getByText('速率 2.0 標註/分 · 預估剩餘 ~10 分')).toBeInTheDocument()
  const fill = container.querySelector('[class*="barFill"]') as HTMLElement
  expect(fill.style.width).toBe('80%')
})

test('data null → 閒置態，不顯進度條', () => {
  const { container } = render(
    <ProgressPanel title="語意標註" data={null} rateLine="" idleText="目前無執行中的標註" />,
  )
  expect(screen.getByText('目前無執行中的標註')).toBeInTheDocument()
  expect(container.querySelector('[class*="barFill"]')).toBeNull()
})
