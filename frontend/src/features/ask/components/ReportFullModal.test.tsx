import { afterEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'

vi.mock('../api', () => ({
  getReportFull: vi.fn(),
}))

import { ReportFullModal } from './ReportFullModal'
import { getReportFull } from '../api'

afterEach(() => vi.clearAllMocks())

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MantineProvider theme={theme}>{ui}</MantineProvider>
    </QueryClientProvider>,
  )
}

describe('ReportFullModal', () => {
  test('opened + 成功 → 顯示 report-full-body 與標題', async () => {
    vi.mocked(getReportFull).mockResolvedValue({
      report_id: 'r1',
      title: '台積電深度研報',
      markdown: '這是研報正文內容',
    })
    wrap(<ReportFullModal reportId="r1" opened onClose={vi.fn()} />)

    const body = await screen.findByTestId('report-full-body')
    expect(body).toHaveTextContent('這是研報正文內容')
    expect(screen.getByText('台積電深度研報')).toBeInTheDocument()
  })

  test('opened + 失敗 → 顯示 report-full-error', async () => {
    vi.mocked(getReportFull).mockRejectedValue(new Error('載入失敗'))
    wrap(<ReportFullModal reportId="r2" opened onClose={vi.fn()} />)

    expect(await screen.findByTestId('report-full-error')).toBeInTheDocument()
    expect(screen.queryByTestId('report-full-body')).toBeNull()
  })

  test('opened=false → 不觸發查詢、不渲染 body', () => {
    wrap(<ReportFullModal reportId="r3" opened={false} onClose={vi.fn()} />)

    expect(getReportFull).not.toHaveBeenCalled()
    expect(screen.queryByTestId('report-full-body')).toBeNull()
  })
})
