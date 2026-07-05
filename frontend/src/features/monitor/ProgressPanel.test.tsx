import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProgressPanel } from './ProgressPanel'

test('有資料：done/total/pct + 速率行 + 進度條寬 + aria progressbar', () => {
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
  const bar = screen.getByRole('progressbar')
  expect(bar).toHaveAttribute('aria-valuenow', '80')
  expect(bar).toHaveAttribute('aria-valuemin', '0')
  expect(bar).toHaveAttribute('aria-valuemax', '100')
  expect(bar).toHaveAttribute('aria-label', '語意標註')
})

test('進度條寬與 aria-valuenow 夾在 0–100（防越界資料）', () => {
  const { container: over } = render(
    <ProgressPanel title="摘要生成" data={{ done: 150, total: 100, pct: 150 }} rateLine="" idleText="—" />,
  )
  expect((over.querySelector('[class*="barFill"]') as HTMLElement).style.width).toBe('100%')
  expect(over.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '100')

  const { container: under } = render(
    <ProgressPanel title="摘要生成" data={{ done: 0, total: 100, pct: -10 }} rateLine="" idleText="—" />,
  )
  expect((under.querySelector('[class*="barFill"]') as HTMLElement).style.width).toBe('0%')
  expect(under.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '0')
})

test('data null → 閒置態，不顯進度條/progressbar', () => {
  const { container } = render(
    <ProgressPanel title="語意標註" data={null} rateLine="" idleText="目前無執行中的標註" />,
  )
  expect(screen.getByText('目前無執行中的標註')).toBeInTheDocument()
  expect(container.querySelector('[class*="barFill"]')).toBeNull()
  expect(container.querySelector('[role="progressbar"]')).toBeNull()
})
