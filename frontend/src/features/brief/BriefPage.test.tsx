import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import * as briefApi from '../../lib/briefApi'
import type { BriefEnvelope, BriefReportRef } from '../../lib/briefSchemas'
import BriefPage from './BriefPage'

vi.mock('../../lib/briefApi')

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname + loc.search}</output>
}

function wrap(entry = '/brief') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/brief" element={<><BriefPage /><Where /></>} />
          <Route path="/report/:hash" element={<div>閱讀頁</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const MARKDOWN = [
  '## 今日重點',
  '- 記憶體報價續揚，兩家券商同步上調目標價（citic）。',
  '- 台股量縮整理，電子股相對強勢（kgi）。',
  '',
  '## 市場焦點',
  '**台股**',
  '台股量縮整理，電子股相對強勢（kgi）。',
  '',
  '**美股**',
  'AI 加速器需求延續。',
  '',
  '## 評等與目標價',
  '- 華邦電（TW 2344）｜citic｜評等 中立→買進｜目標價 TWD 30→TWD 45（+50.0%）',
].join('\n')

function report(n: number, over: Partial<BriefReportRef> = {}): BriefReportRef {
  return {
    report_id: `rep-${n}`,
    file_hash: String(n).repeat(64).slice(0, 64),
    file_name: `624726992507895929_26080${n}_citic.pdf`,
    title: `研報標題 ${n}`,
    market: 'TW',
    source: 'citic',
    source_display: '中信投顧',
    report_date: '2026-08-07',
    ...over,
  }
}

const DATES = ['2026-08-07', '2026-08-06', '2026-08-04']

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
      reports: [report(1, { title: '華邦電：高雄廠擴產' }), report(2, { source: 'kgi', source_display: '凱基', market: 'US' })],
      created_at: '2026-08-07T03:05:00+00:00',
      markets: [{ market: 'TW', report_count: 10 }, { market: 'US', report_count: 4 }, { market: null, report_count: 1 }],
      broker_count: 3,
      ...partial,
    },
    available_dates: DATES,
  }
}

describe('BriefPage', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // 只假時鐘（「今天」），不動計時器：react-query 與 waitFor 照常運作。
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 7, 7, 10, 0))
    vi.mocked(briefApi.getBriefDates).mockResolvedValue(DATES)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('概況卡：大日期、年份星期、統計與市場分布', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap()

    expect(screen.getByRole('heading', { level: 1, name: '每日簡報' })).toBeInTheDocument()
    const overview = await screen.findByRole('region', { name: '簡報概況' })
    expect(within(overview).getByRole('heading', { name: '8 月 7 日' })).toBeInTheDocument()
    expect(within(overview).getByText('2026 年 星期五')).toBeInTheDocument()
    const stat = (label: string) => within(overview).getByText(label).closest('div')!
    expect(within(stat('篇新進研報')).getByText('15')).toBeInTheDocument()
    expect(within(stat('個市場')).getByText('2')).toBeInTheDocument()
    expect(within(stat('家券商')).getByText('3')).toBeInTheDocument()
    expect(within(overview).getByRole('img', { name: '新進研報市場分布：台股 10 篇，美股 4 篇，其他 1 篇' })).toBeInTheDocument()
  })

  it('今日重點與市場焦點；評等與目標價段與複製按鈕都不顯示', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    wrap()

    const hl = await screen.findByRole('region', { name: '今日重點' })
    const items = within(hl).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('記憶體報價續揚，兩家券商同步上調目標價。')
    // 句末的來源代碼換成本期來源研報的中文券商名
    expect(within(items[0]).getByText('中信投顧')).toBeInTheDocument()
    expect(within(items[1]).getByText('凱基')).toBeInTheDocument()

    const focus = screen.getByRole('region', { name: '市場焦點' })
    expect(within(focus).getByRole('heading', { level: 3, name: '台股' })).toBeInTheDocument()
    expect(within(focus).getByText('10 篇')).toBeInTheDocument()
    expect(within(focus).getByText('4 篇')).toBeInTheDocument()
    expect(within(focus).getByText('台股量縮整理，電子股相對強勢（凱基）。')).toBeInTheDocument()

    expect(screen.queryByText(/評等與目標價/)).not.toBeInTheDocument()
    expect(screen.queryByText(/TWD 30/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /複製/ })).not.toBeInTheDocument()
  })

  it('來源研報顯示標題而非檔名，連到閱讀頁；缺 title 回退檔名', async () => {
    const data = envelope()
    data.brief!.reports[1].title = null
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(data)
    wrap()

    const link = await screen.findByRole('link', { name: /華邦電：高雄廠擴產/ })
    expect(link).toHaveAttribute('href', `/report/${'1'.repeat(64)}`)
    expect(link).toHaveTextContent('08-07')
    expect(screen.getByRole('link', { name: /624726992507895929_260802_citic\.pdf/ })).toBeInTheDocument()
    expect(screen.queryByText(/624726992507895929_260801/)).not.toBeInTheDocument()
  })

  it('來源研報預設列 6 篇，「其餘 N 篇」可展開收合；列出的少於實際篇數時說出差額', async () => {
    const reports = Array.from({ length: 9 }, (_, i) => report(i + 1))
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope({ reports, report_count: 12 }))
    wrap()

    const src = await screen.findByRole('region', { name: '本期來源研報' })
    expect(within(src).getAllByRole('link')).toHaveLength(6)
    expect(within(src).queryByText('另有 3 篇未列入本期彙整。')).not.toBeInTheDocument()
    const more = within(src).getByRole('button', { name: '其餘 3 篇' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(more)
    expect(within(src).getAllByRole('link')).toHaveLength(9)
    expect(within(src).getByText('另有 3 篇未列入本期彙整。')).toBeInTheDocument()
    fireEvent.click(within(src).getByRole('button', { name: '收合' }))
    expect(within(src).getAllByRole('link')).toHaveLength(6)
  })

  it('前一份／後一份切換日期；最新一份沒有後一份', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    vi.mocked(briefApi.getBriefByDate).mockResolvedValue(envelope({ brief_date: '2026-08-06' }))
    wrap()

    await screen.findByRole('region', { name: '簡報概況' })
    expect(screen.getByRole('button', { name: '後一份簡報' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '前一份簡報' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/brief?date=2026-08-06')
    await waitFor(() => expect(briefApi.getBriefByDate).toHaveBeenCalledWith('2026-08-06'))
    expect(await screen.findByRole('heading', { name: '8 月 6 日' })).toBeInTheDocument()
  })

  it('月曆點有簡報的日期就切過去；沒有簡報的日期不可點', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope())
    vi.mocked(briefApi.getBriefByDate).mockResolvedValue(envelope({ brief_date: '2026-08-04' }))
    wrap()

    await screen.findByRole('region', { name: '簡報概況' })
    expect(screen.getByRole('heading', { name: '2026 年 8 月' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '8 月 4 日' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '8 月 5 日，無簡報' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '8 月 4 日' }))
    await waitFor(() => expect(briefApi.getBriefByDate).toHaveBeenCalledWith('2026-08-04'))
    expect(await screen.findByRole('heading', { name: '8 月 4 日' })).toBeInTheDocument()
  })

  it('後端沒給市場分布時只顯示篇數，不畫分布條', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue(envelope({ markets: null, broker_count: null }))
    wrap()

    const overview = await screen.findByRole('region', { name: '簡報概況' })
    expect(within(overview).getByText('篇新進研報')).toBeInTheDocument()
    expect(within(overview).queryByText('個市場')).not.toBeInTheDocument()
    expect(within(overview).queryByText('家券商')).not.toBeInTheDocument()
    expect(within(overview).queryByRole('img')).not.toBeInTheDocument()
  })

  it('尚未產生任何簡報時是空白狀態，不是錯誤', async () => {
    vi.mocked(briefApi.getLatestBrief).mockResolvedValue({ status: 'pending', brief: null, available_dates: [] })
    vi.mocked(briefApi.getBriefDates).mockResolvedValue([])
    wrap()

    expect(await screen.findByRole('heading', { name: '8 月 7 日的簡報尚未產生' })).toBeInTheDocument()
    expect(screen.getByText('每天上午彙整前一日入庫的研報。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '看前一份簡報' })).not.toBeInTheDocument()
    expect(screen.queryByText(/載入失敗/)).not.toBeInTheDocument()
  })

  it('指定日期查無簡報：空白狀態＋看前一份簡報', async () => {
    vi.mocked(briefApi.getBriefByDate).mockImplementation(async date => {
      if (date === '2026-08-05') throw new ApiError(404, 'HTTP 404')
      return envelope({ brief_date: date })
    })
    wrap('/brief?date=2026-08-05')

    expect(await screen.findByRole('heading', { name: '8 月 5 日沒有簡報' })).toBeInTheDocument()
    expect(screen.queryByText(/載入失敗/)).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '看前一份簡報' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/brief?date=2026-08-04')
    expect(await screen.findByRole('heading', { name: '8 月 4 日' })).toBeInTheDocument()
  })

  it('比最舊一份還早的日期沒有「前一份」可看', async () => {
    vi.mocked(briefApi.getBriefByDate).mockRejectedValue(new ApiError(404, 'HTTP 404'))
    wrap('/brief?date=2026-08-01')

    expect(await screen.findByRole('heading', { name: '8 月 1 日沒有簡報' })).toBeInTheDocument()
    await waitFor(() => expect(briefApi.getBriefDates).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '看前一份簡報' })).not.toBeInTheDocument()
  })

  it('今天的簡報還沒產生時說「尚未產生」', async () => {
    vi.mocked(briefApi.getBriefByDate).mockRejectedValue(new ApiError(404, 'HTTP 404'))
    vi.setSystemTime(new Date(2026, 7, 8, 8, 0))
    wrap('/brief?date=2026-08-08')

    expect(await screen.findByRole('heading', { name: '8 月 8 日的簡報尚未產生' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '看前一份簡報' })).toBeInTheDocument()
  })

  it('非 404 的失敗才顯示載入失敗，重試會重新取', async () => {
    vi.mocked(briefApi.getLatestBrief)
      .mockRejectedValueOnce(new ApiError(500, 'HTTP 500'))
      .mockResolvedValueOnce(envelope())
    wrap()

    expect(await screen.findByRole('heading', { name: '簡報載入失敗' })).toBeInTheDocument()
    expect(screen.getByText('請稍後再試。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重試' }))
    expect(await screen.findByRole('region', { name: '簡報概況' })).toBeInTheDocument()
    expect(briefApi.getLatestBrief).toHaveBeenCalledTimes(2)
  })

  it('連線失敗（沒有 HTTP 狀態）說連線逾時', async () => {
    vi.mocked(briefApi.getLatestBrief).mockRejectedValue(new TypeError('Failed to fetch'))
    wrap()

    expect(await screen.findByText('連線逾時，請稍後再試。')).toBeInTheDocument()
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
