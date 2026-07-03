import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ResultCard } from './ResultCard'
import type { ReportRow } from '../../lib/schemas'

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_name: '研報.pdf', market: 'TW', source: '元大', summary: '摘要內容',
    report_date: '2026-06-25', report_type: '個股', instrument_types: ['股票'],
    relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}

describe('ResultCard', () => {
  it('browse：顯示摘要、無相關度條', () => {
    render(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} onOpen={() => {}} />)
    expect(screen.getByText('摘要內容')).toBeTruthy()
    expect(screen.queryByText(/相關度/)).toBeNull()
  })
  it('search：顯示高亮片段 + 相關度 + 最新徽章', () => {
    const r = row({ rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 0, content: '台積電營收成長' }] })
    render(<ResultCard row={r} mode="search" isLatest terms={['台積']} onOpen={() => {}} />)
    expect(screen.getByText(/相關度/)).toBeTruthy()
    expect(document.querySelector('mark')).not.toBeNull()
    expect(screen.getByText('最新')).toBeTruthy()
  })
  it('整卡可點 → onOpen(id, fileName)', () => {
    const onOpen = vi.fn()
    render(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: /研報\.pdf/ }))
    expect(onOpen).toHaveBeenCalledWith('r1', '研報.pdf')
  })
})
