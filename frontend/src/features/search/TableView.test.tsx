import type { ReactElement } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { TableView } from './TableView'
import type { TableSort } from '../../lib/tableSort'
import type { ReportRow } from '../../lib/schemas'

const HASH = 'b'.repeat(64)

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_hash: HASH, file_name: 'A.pdf', market: 'TW', source: '元大', summary: null,
    report_date: '2026-06-25', report_type: '個股', instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}
const sort: TableSort = { key: 'date', dir: 'desc' }

/** 顯示當前網址，供導覽斷言。 */
function Here() {
  const loc = useLocation()
  return <div data-testid="here">{loc.pathname + loc.search}</div>
}

function wrap(ui: ReactElement) {
  return render(
    <MemoryRouter initialEntries={['/search']}>
      <Routes>
        <Route path="/search" element={ui} />
        <Route path="/report/:hash" element={<Here />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TableView', () => {
  it('browse 6 欄（含 標的、無 相關度/命中）', () => {
    wrap(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} />)
    const heads = screen.getAllByRole('columnheader')
    expect(heads.length).toBe(6)
    expect(heads.some(h => h.textContent?.includes('標的'))).toBe(true)
    expect(heads.some(h => h.textContent?.includes('相關度'))).toBe(false)
  })
  it('search 8 欄（多 相關度/命中）', () => {
    wrap(<TableView rows={[row({ best_score: 0.8, match_count: 3 })]} mode="search" sort={sort} onSort={() => {}} />)
    expect(screen.getAllByRole('columnheader').length).toBe(8)
    expect(screen.getByText('80%')).toBeTruthy()
  })
  it('點可排序表頭 → onSort(key)；標的不可排', () => {
    const onSort = vi.fn()
    wrap(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={onSort} />)
    fireEvent.click(screen.getByText(/報告名稱/))
    expect(onSort).toHaveBeenCalledWith('name')
    fireEvent.click(screen.getByText('標的'))
    expect(onSort).toHaveBeenCalledTimes(1)
  })
  it('類型欄英文碼顯示中文（memo→備忘）', () => {
    wrap(<TableView rows={[row({ report_type: 'memo' })]} mode="browse" sort={sort} onSort={() => {}} />)
    expect(screen.getByText('備忘')).toBeTruthy()
    expect(screen.queryByText('memo')).toBeNull()
  })
  // <tr> 不能是連結，故報告名稱另包真 <Link>，讓表格檢視也拿得回 cmd+click／複製連結
  it('報告名稱是連往閱讀頁的真連結', () => {
    wrap(<TableView rows={[row({})]} mode="browse" sort={sort} onSort={() => {}} />)
    expect(screen.getByRole('link', { name: 'A.pdf' })).toHaveAttribute('href', `/report/${HASH}`)
  })
  // 反向釘死：導覽仍要成立，但不得再帶 ?chunk（閱讀頁已無命中定位的落點）
  it('點列導向閱讀頁，且不帶 ?chunk', () => {
    wrap(<TableView
      rows={[row({ passages: [{ score: 0.9, chunk_index: 3, content: 'x' }] })]}
      mode="search" sort={sort} onSort={() => {}} />)
    fireEvent.click(screen.getByRole('link', { name: 'A.pdf' }))
    expect(screen.getByTestId('here')).toHaveTextContent(`/report/${HASH}`)
    expect(screen.getByTestId('here').textContent).not.toContain('chunk')
  })
})
