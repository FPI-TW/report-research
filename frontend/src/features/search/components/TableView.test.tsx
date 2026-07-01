import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { TableView } from './TableView'
import type { Row } from '../lib/normalize'

const rows: Row[] = [
  { report_id: 'a', file_name: 'Zebra', market: 'TW', report_date: '2026-01-01' } as Row,
  { report_id: 'b', file_name: 'Apple', market: 'US', report_date: '2026-06-01' } as Row,
]
const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('渲染表頭與列', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  expect(screen.getByText('報告名稱')).toBeInTheDocument()
  expect(screen.getAllByRole('row')).toHaveLength(3) // 表頭 + 2 列
})

test('點報告名稱表頭升冪排序', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  fireEvent.click(screen.getByText('報告名稱'))
  const bodyRows = screen.getAllByRole('row').slice(1)
  expect(within(bodyRows[0]).getByText('Apple')).toBeInTheDocument()
})

test('再點同表頭翻為降冪', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  const th = screen.getByText('報告名稱')
  fireEvent.click(th)
  fireEvent.click(th)
  const bodyRows = screen.getAllByRole('row').slice(1)
  expect(within(bodyRows[0]).getByText('Zebra')).toBeInTheDocument()
})

test('search 模式多相關度/命中欄', () => {
  wrap(<TableView rows={rows} mode="search" onOpen={vi.fn()} />)
  expect(screen.getByText('相關度')).toBeInTheDocument()
  expect(screen.getByText('命中')).toBeInTheDocument()
})

test('點列觸發 onOpen 帶 report_id', () => {
  const onOpen = vi.fn()
  wrap(<TableView rows={rows} mode="browse" onOpen={onOpen} />)
  fireEvent.click(within(screen.getAllByRole('row')[1]).getByText('Zebra'))
  expect(onOpen).toHaveBeenCalledWith('a')
})

test('表頭有 aria-sort', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  fireEvent.click(screen.getByText('報告名稱'))
  expect(screen.getByText(/報告名稱/).closest('th')).toHaveAttribute('aria-sort', 'ascending')
})

// ── Phase 2c a11y/空值 ────────────────────────────────────────────────────────

test('列有 aria-label 為報告名稱（可及性）', () => {
  wrap(<TableView rows={rows} mode="browse" onOpen={vi.fn()} />)
  const bodyRows = screen.getAllByRole('row').slice(1)
  expect(bodyRows[0]).toHaveAttribute('aria-label', 'Zebra')
  expect(bodyRows[1]).toHaveAttribute('aria-label', 'Apple')
})

test('市場為空時渲染「—」而非空白', () => {
  const rowsWithEmptyMarket: Row[] = [
    { report_id: 'c', file_name: 'NoMarket', market: null, report_date: '2026-02-01' } as Row,
  ]
  wrap(<TableView rows={rowsWithEmptyMarket} mode="browse" onOpen={vi.fn()} />)
  const bodyRow = screen.getAllByRole('row')[1]
  // 市場欄＝第 2 個儲存格（第 1 個為報告名稱）
  const marketCell = within(bodyRow).getAllByRole('cell')[1]
  expect(marketCell).toHaveTextContent('—')
})
