import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../theme'
import AskPage from './AskPage'
import { getConversations, getConversation } from './api'

// cancelSpy 需在 vi.mock factory 之外仍可存取以供斷言，經 vi.hoisted 提升共享
// （vi.mock 會被提升到 import 之上，factory 不能引用一般外層 const）。
const { cancelSpy } = vi.hoisted(() => ({ cancelSpy: vi.fn() }))

vi.mock('./api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  getConversation: vi.fn().mockResolvedValue([]),
  deleteConversation: vi.fn(),
  sendFeedback: vi.fn(),
  getReportFull: vi.fn().mockResolvedValue({ report_id: 'r1', title: '台積電深度研報', markdown: '內容' }),
}))

// 只 mock cancel：其餘欄位維持最小可用（idle），聚焦驗證「切換對話時是否呼叫 cancelReport」
// 這條跨對話研報殘留 bug 的關鍵接線（見 useReportStream.cancel 重置 + useAskStream 唯一 turn id 修正）。
vi.mock('./hooks/useReportStream', () => ({
  useReportStream: () => ({
    report: { turnId: null, phase: 'idle', stage: null, markdown: '', done: null, error: null },
    start: vi.fn(),
    cancel: cancelSpy,
  }),
}))

afterEach(() => {
  vi.restoreAllMocks()
  cancelSpy.mockClear()
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/ask']}>
      <QueryClientProvider client={qc}>
        <MantineProvider theme={theme}>
          <AskPage />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('AskPage', () => {
  test('渲染輸入框與新對話鈕', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('ask-input')).toBeInTheDocument())
    expect(screen.getByTestId('ask-new')).toBeInTheDocument()
  })
})

describe('AskPage - 跨對話研報殘留防護（cancelReport 接線）', () => {
  test('點擊「新對話」會呼叫 cancelReport，避免舊研報殘留到新對話', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('ask-new')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ask-new'))
    expect(cancelSpy).toHaveBeenCalled()
  })

  test('切換至歷史對話（openConversation）會呼叫 cancelReport，避免舊研報殘留到新對話', async () => {
    vi.mocked(getConversations).mockResolvedValueOnce([
      { conversation_id: 'c1', title: '對話一', last_at: null, turn_count: 1 },
    ])
    renderPage()
    const openBtn = await screen.findByTestId('ask-hist-open')
    fireEvent.click(openBtn)
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith('c1'))
    expect(cancelSpy).toHaveBeenCalled()
  })

  test('歷史研報卡片點擊「查看全文」會開啟 ReportFullModal（onOpenFull 正確接線）', async () => {
    vi.mocked(getConversations).mockResolvedValueOnce([
      { conversation_id: 'c1', title: '對話一', last_at: null, turn_count: 1 },
    ])
    vi.mocked(getConversation).mockResolvedValueOnce([
      {
        id: 'q1',
        question: '台積電最新展望？',
        answer: '答案內容',
        reports: [
          { report_id: 'r1', title: '台積電深度研報', download_url: '/api/report-doc/r1/pdf', created_at: null },
        ],
      },
    ])
    renderPage()
    fireEvent.click(await screen.findByTestId('ask-hist-open'))
    const viewFullBtn = await screen.findByTestId('report-viewfull')
    fireEvent.click(viewFullBtn)
    await waitFor(() => expect(screen.getByTestId('report-full-modal')).toBeInTheDocument())
  })
})
