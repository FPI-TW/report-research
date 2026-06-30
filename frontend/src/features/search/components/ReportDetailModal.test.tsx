import React from 'react'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { ReportDetailModal } from './ReportDetailModal'

function wrap(ui: React.ReactElement) {
  return render(<MantineProvider>{ui}</MantineProvider>)
}

const mockReport = {
  report_id: 'r1',
  file_name: '台積電深度研究報告',
  market: 'TW',
  source: '元富',
  summary: '本報告深度分析台積電 2nm 技術進展',
  report_date: '2026-06-01',
  report_type: '深度報告',
  has_file: true,
}

function stubFetch(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    }),
  )
}

afterEach(() => vi.unstubAllGlobals())

// ── 載入後渲染 metadata ───────────────────────────────────────────────────────

test('載入後顯示 modal body', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  expect(await screen.findByTestId('modal-body')).toBeInTheDocument()
})

test('顯示市場中文標籤（台股）', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  await screen.findByTestId('modal-body')
  expect(screen.getByText(/台股/)).toBeInTheDocument()
})

test('顯示來源', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  await screen.findByTestId('modal-body')
  expect(screen.getByText(/元富/)).toBeInTheDocument()
})

test('顯示格式化日期', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  await screen.findByTestId('modal-body')
  expect(screen.getByText(/2026\/06\/01/)).toBeInTheDocument()
})

test('顯示摘要文字', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  expect(await screen.findByTestId('modal-summary')).toBeInTheDocument()
  expect(screen.getByText(/2nm 技術進展/)).toBeInTheDocument()
})

// ── has_file ──────────────────────────────────────────────────────────────────

test('has_file=true 時顯示原始檔連結，href 正確', async () => {
  stubFetch(mockReport)
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  const link = await screen.findByTestId('modal-file-link')
  expect(link).toHaveAttribute('href', '/api/report/r1/file')
})

test('has_file=false 時不顯示原始檔連結', async () => {
  stubFetch({ ...mockReport, has_file: false })
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  await screen.findByTestId('modal-body')
  expect(screen.queryByTestId('modal-file-link')).toBeNull()
})

// ── 錯誤狀態 ──────────────────────────────────────────────────────────────────

test('fetch 失敗時顯示錯誤訊息', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network error')))
  wrap(<ReportDetailModal reportId="r1" onClose={vi.fn()} />)
  expect(await screen.findByTestId('modal-error')).toBeInTheDocument()
})

// ── closed state ──────────────────────────────────────────────────────────────

test('reportId=null 時不發送 fetch 請求', () => {
  const fetchSpy = vi.fn()
  vi.stubGlobal('fetch', fetchSpy)
  wrap(<ReportDetailModal reportId={null} onClose={vi.fn()} />)
  expect(fetchSpy).not.toHaveBeenCalled()
})
