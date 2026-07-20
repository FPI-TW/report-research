import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router'
import type { ReactNode } from 'react'
import SearchPage from './SearchPage'
import * as searchApi from '../../lib/searchApi'
import * as useStatsMod from '../../lib/useStats'

vi.mock('../../lib/searchApi')
vi.mock('../../lib/useStats')

function listResp(ids: string[], total: number) {
  return {
    total, offset: 0,
    items: ids.map(id => ({
      report_id: id, file_hash: id.repeat(64).slice(0, 64), file_name: id + '.pdf',
      market: 'TW', source: '元大', summary: '摘要',
      report_date: '2026-06-25', report_type: '個股', instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
    })),
  }
}

function wrap(node: ReactNode, entry = '/search') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes><Route path="/search" element={node} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(useStatsMod.useStats).mockReturnValue({
    data: { total_reports: 5, markets: [{ market: 'TW', count: 5 }], instrument_types: [], report_types: [] },
  } as unknown as ReturnType<typeof useStatsMod.useStats>)
})

function searchResp(ids: string[], total: number, facets: { market: string; count: number }[] = []) {
  return {
    query: 'q', market: null, total, market_facets: facets,
    results: ids.map((id, i) => ({
      report_id: id, file_hash: id.repeat(64).slice(0, 64), file_name: id + '.pdf',
      market: 'TW', source: '元大', summary: '摘要',
      report_date: '2026-06-25', report_type: '個股', instrument_types: null,
      relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
      rank: i + 1, best_score: 0.9, match_count: 3, passages: [],
    })),
  }
}

describe('SearchPage 整合', () => {
  it('browse 無篩選 → Bento 簡報牆（頭條＋數據磚＋查看全部入口）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />)
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(screen.getByText('b.pdf')).toBeTruthy()          // 其餘進「更多最新入庫」清單磚
    expect(screen.getByText('篇研報 · DOCS')).toBeTruthy()   // 數據磚讀全語料庫
    expect(screen.getByText(/查看全部 5 篇/)).toBeTruthy()
  })

  it('查看全部（?all=1）→ 完整清單與 meta，跳過 Bento', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />, '/search?all=1')
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(screen.getByText(/共 2 篇/)).toBeTruthy()
    expect(screen.queryByText('篇研報 · DOCS')).toBeNull()
  })

  it('搜尋態 → 命中組成條讀後端分面，chips 顯示命中數而非全庫計數', async () => {
    vi.mocked(searchApi.searchReports).mockResolvedValue(
      searchResp(['a', 'b'], 8, [{ market: 'TW', count: 6 }, { market: 'US', count: 2 }]),
    )
    wrap(<SearchPage />, '/search?q=散熱')
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    // 找到 8 篇 · 台股佔 75%（6/8）——色譜描述的是命中集合，不是全語料庫
    expect(screen.getByText(/找到/)).toHaveTextContent('找到 8 篇 · 台股佔 75%')
    // 全庫 TW=5，但 chips 應顯示命中數 6
    expect(screen.getByRole('button', { name: '台股 6' })).toBeTruthy()
    // 分面涵蓋全部命中 → 未出現在分面的市場就是零命中，不佔版面
    expect(screen.queryByRole('button', { name: /^港股/ })).toBeNull()
  })

  it('搜尋且已選市場 → chips 不顯示計數（其他市場命中數無從得知，不編造）', async () => {
    vi.mocked(searchApi.searchReports).mockResolvedValue(
      searchResp(['a'], 6, [{ market: 'TW', count: 6 }]),
    )
    wrap(<SearchPage />, '/search?q=散熱&market=TW')
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(screen.getByRole('button', { name: '台股' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /台股 \d/ })).toBeNull()
  })

  it('切表格檢視不重抓（browseReports 次數不變）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a'], 1))
    // 1a 重設計：工具列（含檢視切換）只在非 hero 態顯示；帶 market 篩選以離開 hero 但仍為 browse
    wrap(<SearchPage />, '/search?market=TW')
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('tab', { name: '表格檢視' }))
    await waitFor(() => expect(screen.getAllByRole('columnheader').length).toBeGreaterThan(0))
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
  })
})
