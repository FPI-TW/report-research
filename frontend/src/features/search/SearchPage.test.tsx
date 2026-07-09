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
      report_id: id, file_name: id + '.pdf', market: 'TW', source: '元大', summary: '摘要',
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

describe('SearchPage 整合', () => {
  it('browse 載入結果並渲染卡片 + meta', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a', 'b'], 2))
    wrap(<SearchPage />)
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(screen.getByText(/共 2 篇/)).toBeTruthy()
  })

  it('切表格檢視不重抓（browseReports 次數不變）', async () => {
    vi.mocked(searchApi.browseReports).mockResolvedValue(listResp(['a'], 1))
    // 1a 重設計：工具列（含檢視切換）只在非 hero 態顯示；帶 market 篩選以離開 hero 但仍為 browse
    wrap(<SearchPage />, '/search?market=TW')
    await waitFor(() => expect(screen.getByText('a.pdf')).toBeTruthy())
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '表格檢視' }))
    await waitFor(() => expect(screen.getAllByRole('columnheader').length).toBeGreaterThan(0))
    expect(searchApi.browseReports).toHaveBeenCalledTimes(1)
  })
})
