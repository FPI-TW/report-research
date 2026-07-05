import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiGrid } from './KpiGrid'
import type { Progress } from './progressSchema'

function mk(over: Partial<Progress> = {}): Progress {
  return {
    ts: '', db: { reports: 1234, chunks: 56789, markets: [{ market: 'TW', count: 5 }, { market: 'US', count: 3 }] },
    summary: { done: 90, total: 100, remaining: 10, pct: 90 },
    tagging: { done: 80, total: 100, fail: 20, pct: 80 },
    ingest: null, pipelines: { web: true, ingest: false, tag: true, summaries: false }, orchestrator: null,
    ...over,
  }
}

test('4 卡值與情境副字', () => {
  render(<KpiGrid progress={mk()} />)
  expect(screen.getByText('1,234')).toBeInTheDocument()
  expect(screen.getByText('2 個市場')).toBeInTheDocument()
  expect(screen.getByText('向量片段總數')).toBeInTheDocument()
  expect(screen.getByText('已標註 80 / 100')).toBeInTheDocument()
  expect(screen.getByText('已生成 90 / 100')).toBeInTheDocument()
  expect(screen.getByText('80.00')).toBeInTheDocument()
  expect(screen.getByText('90.00')).toBeInTheDocument()
})

test('tagging null → 標註卡顯 —、無副字', () => {
  render(<KpiGrid progress={mk({ tagging: null })} />)
  expect(screen.getByText('—')).toBeInTheDocument()
  expect(screen.queryByText(/已標註/)).toBeNull()
})
