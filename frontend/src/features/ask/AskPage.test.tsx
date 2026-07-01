import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../theme'
import AskPage from './AskPage'
import { getConversations, getConversation } from './api'
import type { ReportState } from './hooks/useReportStream'

// cancelSpy 需在 vi.mock factory 之外仍可存取以供斷言，經 vi.hoisted 提升共享
// （vi.mock 會被提升到 import 之上，factory 不能引用一般外層 const）。
// reportOverride 讓個別測試可暫時覆寫 useReportStream 回傳值（模擬研報完成事件），
// 不透過 mockReturnValue／restoreAllMocks（會清掉 factory 預設值，波及其他測試）。
const { cancelSpy, reportOverride } = vi.hoisted(() => ({
  cancelSpy: vi.fn(),
  reportOverride: { current: null as { report: unknown; start: unknown; cancel: unknown } | null },
}))

vi.mock('./api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  getConversation: vi.fn().mockResolvedValue([]),
  deleteConversation: vi.fn(),
  sendFeedback: vi.fn(),
  getReportFull: vi.fn().mockResolvedValue({ report_id: 'r1', title: '台積電深度研報', markdown: '內容' }),
}))

// 只 mock cancel：其餘欄位維持最小可用（idle），聚焦驗證「切換對話時是否呼叫 cancelReport」
// 這條跨對話研報殘留 bug 的關鍵接線（見 useReportStream.cancel 重置 + useAskStream 唯一 turn id 修正）。
// reportOverride 有值時優先回傳，供「研報完成後跨輪持久化」測試模擬 phase:'done'。
vi.mock('./hooks/useReportStream', () => ({
  useReportStream: () =>
    reportOverride.current ?? {
      report: { turnId: null, phase: 'idle', stage: null, markdown: '', done: null, error: null },
      start: vi.fn(),
      cancel: cancelSpy,
    },
}))

afterEach(() => {
  vi.restoreAllMocks()
  cancelSpy.mockClear()
  reportOverride.current = null
})

function buildTree(qc: QueryClient) {
  return (
    <MemoryRouter initialEntries={['/ask']}>
      <QueryClientProvider client={qc}>
        <MantineProvider theme={theme}>
          <AskPage />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>
  )
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(buildTree(qc))
  return { ...utils, rerender: () => utils.rerender(buildTree(qc)) }
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

describe('AskPage - 研報跨輪持久化（第二輪研報完成不應抹除第一輪的完成卡片）', () => {
  function doneFor(turnId: string, reportId: string, title: string): ReportState {
    return {
      turnId,
      phase: 'done',
      stage: null,
      markdown: '',
      done: { report_id: reportId, title, download_url: `/api/report-doc/${reportId}/pdf` },
      error: null,
    }
  }

  test('第二份研報完成（共享 report 狀態轉移到另一輪）後，第一輪仍顯示已完成的下載卡片', async () => {
    vi.mocked(getConversations).mockResolvedValueOnce([
      { conversation_id: 'c1', title: '對話一', last_at: null, turn_count: 2 },
    ])
    vi.mocked(getConversation).mockResolvedValueOnce([
      { id: 'q1', question: '台積電最新展望？', answer: '答案一' },
      { id: 'q2', question: '聯電呢？', answer: '答案二' },
    ])

    const { rerender } = renderPage()
    fireEvent.click(await screen.findByTestId('ask-hist-open'))
    await waitFor(() => expect(getConversation).toHaveBeenCalledWith('c1'))
    await screen.findByText('答案二') // 確認兩輪都已載入（turn id 為 hq1 / hq2）

    // 第一份研報完成：此時 turn hq1 已存在，effect 應把結果落地到 turn.reports
    reportOverride.current = { report: doneFor('hq1', 'r1', '台積電深度研報'), start: vi.fn(), cancel: cancelSpy }
    rerender()
    await waitFor(() =>
      expect(screen.getByTestId('report-download')).toHaveAttribute('href', '/api/report-doc/r1/pdf'),
    )

    // 第二份研報開始並完成：共享的 live report 狀態轉移到 hq2 → hq1 不應退化回 offer/消失
    reportOverride.current = { report: doneFor('hq2', 'r2', '聯電深度研報'), start: vi.fn(), cancel: cancelSpy }
    rerender()

    await waitFor(() => {
      const hrefs = screen.getAllByTestId('report-download').map((el) => el.getAttribute('href'))
      expect(hrefs).toEqual(expect.arrayContaining(['/api/report-doc/r1/pdf', '/api/report-doc/r2/pdf']))
    })
    expect(screen.getAllByTestId('report-done')).toHaveLength(2)

    // 再次以相同 report 狀態重渲染，驗證 appendTurnReport 去重冪等（不重複附加造成重複卡片）
    rerender()
    await waitFor(() => expect(screen.getAllByTestId('report-done')).toHaveLength(2))
  })
})
