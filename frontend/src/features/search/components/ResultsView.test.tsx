import React from 'react'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { ResultsView } from './ResultsView'
import { GroupedList } from './GroupedList'
import { MarketIndex } from './MarketIndex'
import { LoadMore } from './LoadMore'
import { ResultsMeta } from './ResultsMeta'
import { EmptyState, ErrorState } from './states'
import type { Row } from '../lib/normalize'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

const twRow: Row = {
  report_id: '1',
  file_name: 'f1',
  market: 'TW',
  report_date: '2026-06-01',
  source: null,
  summary: null,
  report_type: null,
  instrument_types: [],
  relates_stock: false,
  stock_targets: null,
  relates_futures: false,
  futures_targets: null,
}

const usRow: Row = {
  report_id: '2',
  file_name: 'f2',
  market: 'US',
  report_date: '2026-05-01',
  source: null,
  summary: null,
  report_type: null,
  instrument_types: [],
  relates_stock: false,
  stock_targets: null,
  relates_futures: false,
  futures_targets: null,
}

// ── ResultsView: grouped ──────────────────────────────────────────────────────

test('ResultsView grouped 顯示分組標題與卡片', () => {
  const rows = [twRow, usRow]
  wrap(
    <ResultsView
      view="grouped"
      group="month"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
    />,
  )
  expect(screen.getByText('f1')).toBeInTheDocument()
  expect(screen.getByText('f2')).toBeInTheDocument()
})

test('ResultsView grouped 依月份分組顯示標題', () => {
  const rows = [twRow, usRow]
  wrap(
    <ResultsView
      view="grouped"
      group="month"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
    />,
  )
  expect(screen.getByText('2026 年 6 月')).toBeInTheDocument()
  expect(screen.getByText('2026 年 5 月')).toBeInTheDocument()
})

test('ResultsView grouped by market 顯示市場標題', () => {
  const rows = [twRow, usRow]
  wrap(
    <ResultsView
      view="grouped"
      group="market"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
    />,
  )
  // market labels as group headers
  const headers = screen.getAllByTestId('group-header')
  const texts = headers.map((h) => h.textContent ?? '')
  expect(texts.some((t) => t.includes('台股'))).toBe(true)
  expect(texts.some((t) => t.includes('美股'))).toBe(true)
})

// ── ResultsView: index ────────────────────────────────────────────────────────

test('ResultsView index 渲染市場清單', () => {
  const rows = [twRow, usRow]
  wrap(
    <ResultsView
      view="index"
      group="market"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
    />,
  )
  expect(screen.getByTestId('market-index')).toBeInTheDocument()
})

test('ResultsView index 點擊市場呼叫 onPickMarket', () => {
  const onPickMarket = vi.fn()
  const rows = [twRow, usRow]
  wrap(
    <ResultsView
      view="index"
      group="market"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={onPickMarket}
    />,
  )
  // Click the TW market button
  const items = screen.getAllByTestId('market-index-item')
  const twItem = items.find((el) => el.getAttribute('data-market') === 'TW')
  expect(twItem).toBeTruthy()
  twItem!.click()
  expect(onPickMarket).toHaveBeenCalledWith('TW')
})

// ── ResultsView: drill ────────────────────────────────────────────────────────

test('ResultsView drill 渲染市場標頭', () => {
  const rows = [twRow]
  wrap(
    <ResultsView
      view="drill"
      group="month"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
      market="TW"
    />,
  )
  expect(screen.getByTestId('drill-view')).toBeInTheDocument()
  expect(screen.getByTestId('drill-header')).toBeInTheDocument()
  // Market label in header
  expect(screen.getByTestId('drill-header')).toHaveTextContent('台股')
})

test('ResultsView drill 顯示該市場的卡片', () => {
  const rows = [twRow]
  wrap(
    <ResultsView
      view="drill"
      group="month"
      rows={rows}
      mode="browse"
      onOpen={() => {}}
      onPickMarket={() => {}}
      market="TW"
    />,
  )
  expect(screen.getByText('f1')).toBeInTheDocument()
})

// ── GroupedList 空鍵 ──────────────────────────────────────────────────────────

test('GroupedList 無 report_date 群組顯示「未分類」', () => {
  const noDateRow: Row = { ...twRow, report_id: 'x', report_date: null }
  wrap(
    <GroupedList rows={[noDateRow]} group="month" mode="browse" onOpen={() => {}} />,
  )
  const headers = screen.getAllByTestId('group-header')
  expect(headers.some((h) => h.textContent?.includes('未分類'))).toBe(true)
})

// ── MarketIndex ───────────────────────────────────────────────────────────────

test('MarketIndex 顯示市場名稱與篇數', () => {
  const rows = [twRow, twRow, usRow]
  wrap(<MarketIndex rows={rows} onPickMarket={() => {}} />)
  expect(screen.getByText('台股')).toBeInTheDocument()
  expect(screen.getByText('美股')).toBeInTheDocument()
})

// ── LoadMore ──────────────────────────────────────────────────────────────────

test('LoadMore hasMore=false 不渲染按鈕', () => {
  wrap(<LoadMore hasMore={false} loading={false} onMore={() => {}} />)
  expect(screen.queryByTestId('load-more-btn')).toBeNull()
  expect(screen.queryByTestId('load-more-wrap')).toBeNull()
})

test('LoadMore hasMore=true 顯示按鈕', () => {
  wrap(<LoadMore hasMore={true} loading={false} onMore={() => {}} />)
  expect(screen.getByTestId('load-more-btn')).toHaveTextContent('載入更多')
})

test('LoadMore loading=true 按鈕 disabled 且文字改變', () => {
  wrap(<LoadMore hasMore={true} loading={true} onMore={() => {}} />)
  const btn = screen.getByTestId('load-more-btn')
  expect(btn).toBeDisabled()
  expect(btn).toHaveTextContent('載入中…')
})

test('LoadMore 點擊觸發 onMore', () => {
  const onMore = vi.fn()
  wrap(<LoadMore hasMore={true} loading={false} onMore={onMore} />)
  screen.getByTestId('load-more-btn').click()
  expect(onMore).toHaveBeenCalled()
})

// ── ResultsMeta ───────────────────────────────────────────────────────────────

test('ResultsMeta search 顯示查詢字串與篇數', () => {
  wrap(<ResultsMeta mode="search" q="AI 伺服器" market="全部" total={42} />)
  const meta = screen.getByTestId('results-meta')
  expect(meta).toHaveTextContent('「AI 伺服器」')
  expect(meta).toHaveTextContent('找到 42 篇研報')
})

test('ResultsMeta browse 顯示市場與篇數', () => {
  wrap(<ResultsMeta mode="browse" q="" market="TW" total={100} />)
  const meta = screen.getByTestId('results-meta')
  expect(meta).toHaveTextContent('台股')
  expect(meta).toHaveTextContent('100 篇')
})

// ── EmptyState ────────────────────────────────────────────────────────────────

test('EmptyState 顯示找不到文字', () => {
  wrap(<EmptyState onReset={() => {}} />)
  expect(screen.getByTestId('empty-state')).toHaveTextContent('找不到符合的研報')
})

test('EmptyState 點擊清除篩選觸發 onReset', () => {
  const onReset = vi.fn()
  wrap(<EmptyState onReset={onReset} />)
  screen.getByTestId('empty-reset-btn').click()
  expect(onReset).toHaveBeenCalled()
})

// ── ErrorState ────────────────────────────────────────────────────────────────

test('ErrorState 顯示載入失敗文字', () => {
  wrap(<ErrorState onRetry={() => {}} />)
  expect(screen.getByTestId('error-state')).toHaveTextContent('載入失敗')
})

test('ErrorState 點擊重試觸發 onRetry', () => {
  const onRetry = vi.fn()
  wrap(<ErrorState onRetry={onRetry} />)
  screen.getByTestId('error-retry-btn').click()
  expect(onRetry).toHaveBeenCalled()
})
