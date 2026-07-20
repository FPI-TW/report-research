import type { ReactElement } from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { ResultCard } from './ResultCard'
import type { ReportRow } from '../../lib/schemas'

const HASH = 'a'.repeat(64)

function row(p: Partial<ReportRow>): ReportRow {
  return {
    report_id: 'r1', file_hash: HASH, file_name: '研報.pdf', market: 'TW', source: '元大', summary: '摘要內容',
    report_date: '2026-06-25', report_type: '個股', instrument_types: ['股票'],
    relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null, ...p,
  }
}

function wrap(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('ResultCard', () => {
  it('browse：顯示摘要、無相關度條', () => {
    wrap(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} />)
    expect(screen.getByText('摘要內容')).toBeTruthy()
    expect(screen.queryByText(/相關度/)).toBeNull()
  })
  it('search：顯示高亮片段 + 相關度 + 最新徽章', () => {
    const r = row({ rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 0, content: '台積電營收成長' }] })
    wrap(<ResultCard row={r} mode="search" isLatest terms={['台積']} />)
    // 1a 重設計：相關度改為分數條＋百分比（best_score 0.83 → 83%）
    expect(screen.getByText('83%')).toBeTruthy()
    expect(document.querySelector('mark')).not.toBeNull()
    expect(screen.getByText('最新')).toBeTruthy()
  })
  it('商品類型代碼顯示中文（equity→股票、index→指數）', () => {
    wrap(<ResultCard row={row({ instrument_types: ['equity', 'index'] })}
      mode="browse" isLatest={false} terms={[]} />)
    // 1a 重設計：商品類型併入高密度標籤行（股票 · 指數 · …），比對合併後文字
    const tagLine = screen.getByText(/股票/)
    expect(tagLine.textContent).toContain('指數')
    expect(tagLine.textContent).not.toContain('equity')
  })
  // 整卡改為真連結（非 role=button）：cmd+click／中鍵開新分頁／複製連結才拿得回來
  it('整卡是連往閱讀頁的真連結', () => {
    wrap(<ResultCard row={row({})} mode="browse" isLatest={false} terms={[]} />)
    expect(screen.getByRole('link', { name: /研報\.pdf/ }))
      .toHaveAttribute('href', `/report/${HASH}`)
  })
  it('search 態把命中的 chunk_index 帶進連結供閱讀頁定位', () => {
    const r = row({ passages: [{ score: 0.9, chunk_index: 7, content: '台積電營收成長' }] })
    wrap(<ResultCard row={r} mode="search" isLatest={false} terms={[]} />)
    expect(screen.getByRole('link', { name: /研報\.pdf/ }))
      .toHaveAttribute('href', `/report/${HASH}?chunk=7`)
  })
})
