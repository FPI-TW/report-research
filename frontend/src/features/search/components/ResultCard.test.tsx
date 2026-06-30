import React from 'react'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { ResultCard } from './ResultCard'
import type { Row } from '../lib/normalize'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

const baseRow: Row = {
  report_id: 'r1',
  file_name: '台積電研究報告',
  market: 'TW',
  source: '元富',
  report_date: '2026-06-01',
  report_type: '法說會',
  summary: '本報告分析台積電 Q2 業績',
  instrument_types: ['equity'],
  relates_stock: true,
  stock_targets: ['2330'],
  relates_futures: false,
  futures_targets: null,
}

// ── browse mode ───────────────────────────────────────────────────────────────

test('browse: 渲染市場徽章', () => {
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByTestId('market-badge')).toHaveTextContent('台股')
})

test('browse: 渲染檔名', () => {
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('台積電研究報告')).toBeInTheDocument()
})

test('browse: 渲染商品類型標籤', () => {
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('股票')).toBeInTheDocument()
})

test('browse: 渲染摘要', () => {
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('本報告分析台積電 Q2 業績')).toBeInTheDocument()
})

test('browse: 整卡點擊觸發 onOpen 帶 report_id', async () => {
  const onOpen = vi.fn()
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={onOpen} />)
  screen.getByTestId('result-card').click()
  expect(onOpen).toHaveBeenCalledWith('r1')
})

test('browse: Enter 鍵觸發 onOpen', () => {
  const onOpen = vi.fn()
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={onOpen} />)
  const card = screen.getByTestId('result-card')
  card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  expect(onOpen).toHaveBeenCalledWith('r1')
})

test('browse: Space 鍵觸發 onOpen', () => {
  const onOpen = vi.fn()
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={onOpen} />)
  const card = screen.getByTestId('result-card')
  card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
  expect(onOpen).toHaveBeenCalledWith('r1')
})

test('browse: 不渲染命中片段資訊', () => {
  const row: Row = { ...baseRow, matchCount: 5, rank: 1, bestScore: 0.9 }
  wrap(<ResultCard row={row} mode="browse" onOpen={vi.fn()} />)
  expect(screen.queryByTestId('match-count')).toBeNull()
  expect(screen.queryByTestId('score-bar-track')).toBeNull()
})

// ── search mode ───────────────────────────────────────────────────────────────

test('search: 顯示命中片段數', () => {
  const row: Row = { ...baseRow, matchCount: 7, rank: 2, bestScore: 0.85 }
  wrap(<ResultCard row={row} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByTestId('match-count')).toHaveTextContent('命中 7 片段')
})

test('search: 顯示排名', () => {
  const row: Row = { ...baseRow, matchCount: 3, rank: 2, bestScore: 0.75 }
  wrap(<ResultCard row={row} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByText('#2')).toBeInTheDocument()
})

test('search: 顯示相關度分數條', () => {
  const row: Row = { ...baseRow, matchCount: 3, rank: 1, bestScore: 0.8 }
  wrap(<ResultCard row={row} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByTestId('score-bar-track')).toBeInTheDocument()
  expect(screen.getByTestId('score-bar-fill')).toBeInTheDocument()
})

test('search: 顯示第一段片段（純文字）', () => {
  const row: Row = {
    ...baseRow,
    matchCount: 2,
    rank: 1,
    bestScore: 0.9,
    passages: [
      { score: 0.9, chunk_index: 0, content: '台積電 2nm 技術進入量產階段，良率超越預期。' },
      { score: 0.7, chunk_index: 1, content: '第二段片段' },
    ],
  }
  wrap(<ResultCard row={row} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByTestId('passage-text')).toHaveTextContent('台積電 2nm 技術進入量產階段')
})

test('search: 片段內容不含 HTML 標籤（無 dangerouslySetInnerHTML）', () => {
  const maliciousContent = '<script>alert(1)</script> 正常文字'
  const row: Row = {
    ...baseRow,
    matchCount: 1,
    rank: 1,
    bestScore: 0.6,
    passages: [{ score: 0.6, chunk_index: 0, content: maliciousContent }],
  }
  wrap(<ResultCard row={row} mode="search" onOpen={vi.fn()} />)
  const passageEl = screen.getByTestId('passage-text')
  // text content includes raw string, no injected script
  expect(passageEl.innerHTML).not.toContain('<script>')
  expect(passageEl.textContent).toContain('正常文字')
})

test('search: 整卡點擊觸發 onOpen 帶 report_id（對齊 live，modal 可達）', () => {
  const onOpen = vi.fn()
  const row: Row = { ...baseRow, matchCount: 3, rank: 1, bestScore: 0.8 }
  wrap(<ResultCard row={row} mode="search" onOpen={onOpen} />)
  const card = screen.getByTestId('result-card')
  expect(card).toHaveAttribute('role', 'button')
  card.click()
  expect(onOpen).toHaveBeenCalledWith('r1')
})

test('search: Enter 鍵觸發 onOpen', () => {
  const onOpen = vi.fn()
  const row: Row = { ...baseRow, matchCount: 3, rank: 1, bestScore: 0.8 }
  wrap(<ResultCard row={row} mode="search" onOpen={onOpen} />)
  const card = screen.getByTestId('result-card')
  card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  expect(onOpen).toHaveBeenCalledWith('r1')
})

test('browse: 無摘要時不渲染摘要區', () => {
  const row: Row = { ...baseRow, summary: null }
  wrap(<ResultCard row={row} mode="browse" onOpen={vi.fn()} />)
  expect(screen.queryByText('本報告分析台積電 Q2 業績')).toBeNull()
})

test('info row: 來源 · 日期 · 類型', () => {
  wrap(<ResultCard row={baseRow} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('元富')).toBeInTheDocument()
  expect(screen.getByText('2026/06/01')).toBeInTheDocument()
  expect(screen.getByText('法說會')).toBeInTheDocument()
})
