import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { ReportDetailModal } from './ReportDetailModal'
import * as api from '../lib/searchApi'

vi.mock('../lib/searchApi')

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrap = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return render(<ReportDetailModal reportId="r1" onClose={() => {}} />, { wrapper: wrap })
}
type Full = Awaited<ReturnType<typeof api.getReportFull>>
const full = (over: Partial<Full>): Full => ({
  report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大',
  summary: null, report_date: null, report_type: null, has_file: true, ...over,
})

beforeEach(() => vi.resetAllMocks())

describe('ReportDetailModal', () => {
  it('has_file + PDF → 內嵌 iframe（src 指向 /file）', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ file_name: 'a.pdf', has_file: true }))
    renderModal()
    await waitFor(() => expect(document.querySelector('iframe')).not.toBeNull())
    expect(document.querySelector('iframe')?.getAttribute('src')).toContain('/api/report/r1/file')
  })
  it('has_file + 非 PDF → 下載提示 + 下載連結', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ file_name: 'a.docx', has_file: true }))
    renderModal()
    await waitFor(() => expect(screen.getByText(/無法內嵌預覽/)).toBeTruthy())
    expect(screen.getByText('下載原始檔').getAttribute('href')).toContain('/api/report/r1/file')
  })
  it('!has_file → 缺檔文案', async () => {
    vi.mocked(api.getReportFull).mockResolvedValue(full({ has_file: false }))
    renderModal()
    await waitFor(() => expect(screen.getByText('找不到原始檔。')).toBeTruthy())
  })
  it('/full 錯誤 → 載入失敗 fallback', async () => {
    vi.mocked(api.getReportFull).mockRejectedValue(new Error('boom'))
    renderModal()
    await waitFor(() => expect(screen.getByText(/報告載入失敗/)).toBeTruthy())
  })
})
