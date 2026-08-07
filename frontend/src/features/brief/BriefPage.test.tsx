import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import * as briefApi from '../../lib/briefApi'
import type { BriefEnvelope } from '../../lib/briefSchemas'
import BriefPage from './BriefPage'

vi.mock('../../lib/briefApi')

function wrap(entry = '/brief') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/brief" element={<BriefPage />} />
          <Route path="/report/:hash" element={<div>閱讀頁</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const MARKDOWN = [
  '## 今日重點',
  '- 記憶體報價續揚，兩家券商同步上調目標價（citic）',
  '',
  '## 市場焦點',
  '台股量縮整理，電子股相對強勢。',
].join('\n')

function envelope(partial?: Partial<NonNullable<BriefEnvelope['brief']>>): BriefEnvelope {
  return {
    status: 'ready',
    brief: {
      brief_date: '2026-08-07',
      window_start: '2026-08-06T03:00:00+00:00',
      window_end: '2026-08-07T03:00:00+00:00',
      markdown: MARKDOWN,
      report_count: 15,
      signal_count: 2,
      reports: [
        {
          report_id: 'rep-1',
          file_hash: 'a'.repeat(64),
          file_name: '624726992507895929_260807_citic.pdf',
          title: '華邦電：高雄廠擴產',
          market: 'TW',
          source: 'citic',
          source_display: '中信投顧',
          report_date: '2026-08-07',
        },
      ],
      created_at: '2026-08-07T03:05:00+00:00',
      ...partial,
    },
    available_dates: ['2026-08-07', '2026-08-06'],
  }
}

describe('BriefPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('渲染簡報本文、日期與統計', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap()

    expect(await screen.findByRole('heading', { name: '每日簡報' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('2026 年 8 月 7 日')).toBeInTheDocument())
    expect(screen.getByText('新進研報 15 篇')).toBeInTheDocument()
    expect(screen.getByText('已擷取評等訊號 2 筆')).toBeInTheDocument()
    // markdown 有被渲染成結構，不是原樣字串
    expect(screen.getByRole('heading', { name: '今日重點' })).toBeInTheDocument()
    expect(screen.getByText(/記憶體報價續揚/)).toBeInTheDocument()
  })

  it('來源研報顯示標題而非檔名，並連到閱讀頁', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap()

    const link = await screen.findByRole('link', { name: '華邦電：高雄廠擴產' })
    expect(link).toHaveAttribute('href', `/report/${'a'.repeat(64)}`)
    expect(screen.queryByText(/624726992507895929/)).not.toBeInTheDocument()
  })

  it('缺 title 時回退檔名（缺值是常態不是錯誤）', async () => {
    const data = envelope()
    data.brief!.reports[0].title = null
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(data)
    wrap()

    expect(
      await screen.findByRole('link', { name: '624726992507895929_260807_citic.pdf' }),
    ).toBeInTheDocument()
  })

  it('列出的篇數少於實際篇數時要說出差額', async () => {
    // 靜默只列一部分會讓讀者以為那天只有這幾篇。
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap()

    expect(await screen.findByText('另有 14 篇未列入本期彙整。')).toBeInTheDocument()
  })

  it('尚未產生任何簡報時顯示等待訊息，不是錯誤', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue({
      status: 'pending',
      brief: null,
      available_dates: [],
    })
    wrap()

    expect(await screen.findByText(/還沒產生/)).toBeInTheDocument()
    expect(screen.queryByText(/載入失敗/)).not.toBeInTheDocument()
  })

  it('指定日期查無簡報時顯示該日期沒有簡報，而非通用錯誤', async () => {
    vi.mocked(briefApi.getBriefByDate).mockRejectedValue(new ApiError(404, 'HTTP 404'))
    wrap('/brief?date=2026-08-01')

    expect(await screen.findByText('2026 年 8 月 1 日 沒有簡報。')).toBeInTheDocument()
    expect(screen.queryByText(/載入失敗/)).not.toBeInTheDocument()
  })

  it('非 404 的失敗才顯示通用錯誤', async () => {
    vi.mocked(briefApi.getLatestBrief).mockRejectedValue(new ApiError(500, 'HTTP 500'))
    wrap()

    expect(await screen.findByText(/載入失敗/)).toBeInTheDocument()
  })

  it('帶合法 date 參數時走指定日期端點', async () => {
    vi.mocked(briefApi.getBriefByDate).mockResolvedValue(envelope({ brief_date: '2026-08-06' }))
    wrap('/brief?date=2026-08-06')

    await waitFor(() => expect(briefApi.getBriefByDate).toHaveBeenCalledWith('2026-08-06'))
    expect(briefApi.getLatestBrief).not.toHaveBeenCalled()
  })

  it('date 參數格式不合法時退回最新一份，不打壞掉的請求', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap('/brief?date=昨天')

    await waitFor(() => expect(briefApi.getLatestBrief).toHaveBeenCalled())
    expect(briefApi.getBriefByDate).not.toHaveBeenCalled()
  })
})
