import React from 'react'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { FilterSidebar, activeFilterCount } from './FilterSidebar'
import { DEFAULT_FILTERS } from '../lib/filters'
import type { StatsResponse } from '../schemas'

const stats: StatsResponse = {
  total_reports: 10,
  total_chunks: 50,
  markets: [{ market: 'TW', count: 5 }],
  instrument_types: [{ type: 'equity', count: 3 }],
  report_types: [{ type: '法說會', count: 2 }],
  username: 'u',
}

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

// ── 市場 chip ──────────────────────────────────────────────
test('點市場 chip 觸發 onChange', async () => {
  const onChange = vi.fn()
  wrap(<FilterSidebar stats={stats} filters={DEFAULT_FILTERS} onChange={onChange} />)
  ;(await screen.findByRole('button', { name: /TW/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ market: 'TW' }))
})

test('全部 chip 恢復預設市場', async () => {
  // 使用只有市場資料的 stats（無 instrument_types/report_types），避免多個「全部」鈕衝突
  const statsMarketsOnly = { ...stats, instrument_types: [], report_types: [] }
  const onChange = vi.fn()
  wrap(
    <FilterSidebar
      stats={statsMarketsOnly}
      filters={{ ...DEFAULT_FILTERS, market: 'TW' }}
      onChange={onChange}
    />,
  )
  ;(await screen.findByRole('button', { name: /^全部/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ market: '全部' }))
})

// ── sort 選項隨 q 切換 ────────────────────────────────────
test('無 q 時 sort 選項不含相關度', () => {
  wrap(<FilterSidebar stats={stats} filters={DEFAULT_FILTERS} onChange={vi.fn()} />)
  expect(screen.queryByRole('button', { name: /相關度/ })).toBeNull()
  expect(screen.getByRole('button', { name: /日期新/ })).toBeTruthy()
})

test('有 q 時 sort 選項含相關度', () => {
  const { rerender } = wrap(
    <FilterSidebar stats={stats} filters={DEFAULT_FILTERS} onChange={vi.fn()} />,
  )
  expect(screen.queryByRole('button', { name: /相關度/ })).toBeNull()

  rerender(
    <MantineProvider>
      <FilterSidebar
        stats={stats}
        filters={{ ...DEFAULT_FILTERS, q: 'AI 伺服器' }}
        onChange={vi.fn()}
      />
    </MantineProvider>,
  )
  expect(screen.getByRole('button', { name: /相關度/ })).toBeTruthy()
})

// ── 個股 / 期貨 toggle ────────────────────────────────────
test('個股 toggle 點擊觸發 onChange stock=true', async () => {
  const onChange = vi.fn()
  wrap(<FilterSidebar stats={stats} filters={DEFAULT_FILTERS} onChange={onChange} />)
  ;(await screen.findByRole('button', { name: /個股/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ stock: true }))
})

test('期貨 toggle 點擊觸發 onChange futures=true', async () => {
  const onChange = vi.fn()
  wrap(<FilterSidebar stats={stats} filters={DEFAULT_FILTERS} onChange={onChange} />)
  ;(await screen.findByRole('button', { name: /期貨/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ futures: true }))
})

test('已開啟個股再點擊切換為 false', async () => {
  const onChange = vi.fn()
  wrap(
    <FilterSidebar
      stats={stats}
      filters={{ ...DEFAULT_FILTERS, stock: true }}
      onChange={onChange}
    />,
  )
  ;(await screen.findByRole('button', { name: /個股/ })).click()
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ stock: false }))
})

// ── activeFilterCount ─────────────────────────────────────
test('activeFilterCount 預設 = 0', () => {
  expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0)
})

test('activeFilterCount 累計非預設維度', () => {
  expect(activeFilterCount({ ...DEFAULT_FILTERS, market: 'TW' })).toBe(1)
  expect(activeFilterCount({ ...DEFAULT_FILTERS, market: 'TW', stock: true })).toBe(2)
  expect(
    activeFilterCount({
      ...DEFAULT_FILTERS,
      market: 'TW',
      instrument: 'equity',
      stock: true,
      futures: true,
      type: '法說會',
    }),
  ).toBe(5)
})
