import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TableView } from './TableView'
import type { TableSort } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_name: 'A.pdf', market: 'TW', source: '元大', summary: null,
    report_date: '2026-06-25', report_type: '個股', instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}
const sort: TableSort = { key: 'date', dir: 'desc' }

describe('TableView', () => {
  it('browse 6 欄（含 標的、無 相關度/命中）', () => {
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} onOpen={() => {}} />)
    const heads = screen.getAllByRole('columnheader')
    expect(heads.length).toBe(6)
    expect(heads.some(h => h.textContent?.includes('標的'))).toBe(true)
    expect(heads.some(h => h.textContent?.includes('相關度'))).toBe(false)
  })
  it('search 8 欄（多 相關度/命中）', () => {
    render(<TableView rows={[row({ best_score: 0.8, match_count: 3 })]} mode="search" sort={sort} onSort={() => {}} onOpen={() => {}} />)
    expect(screen.getAllByRole('columnheader').length).toBe(8)
    expect(screen.getByText('80%')).toBeTruthy()
  })
  it('點可排序表頭 → onSort(key)；標的不可排', () => {
    const onSort = vi.fn()
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={onSort} onOpen={() => {}} />)
    fireEvent.click(screen.getByText(/報告名稱/))
    expect(onSort).toHaveBeenCalledWith('name')
    fireEvent.click(screen.getByText('標的'))
    expect(onSort).toHaveBeenCalledTimes(1)
  })
  it('類型欄英文碼顯示中文（memo→備忘）', () => {
    render(<TableView rows={[row({ report_type: 'memo' })]} mode="browse" sort={sort} onSort={() => {}} onOpen={() => {}} />)
    expect(screen.getByText('備忘')).toBeTruthy()
    expect(screen.queryByText('memo')).toBeNull()
  })
  it('點列 → onOpen(id, fileName)', () => {
    const onOpen = vi.fn()
    render(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} onOpen={onOpen} />)
    fireEvent.click(screen.getByText('A.pdf'))
    expect(onOpen).toHaveBeenCalledWith('r1', 'A.pdf')
  })
})
