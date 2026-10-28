import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as clipboard from '../../lib/clipboard'
import * as radarApi from '../../lib/radarApi'
import type { BrokerHistory, BrokerSummary, EpsGroup } from '../../lib/radarSchemas'
import * as toast from '../../lib/toast'
import { BrokerTable } from './BrokerTable'

vi.mock('../../lib/radarApi')

function broker(key: string, over: Partial<BrokerSummary> = {}): BrokerSummary {
  return {
    broker: key, broker_display: key === 'daiwa' ? '大和' : key,
    latest_rating: 'buy', latest_rating_raw: 'Buy',
    latest_target_price: 1568, latest_target_currency: 'TWD',
    latest_eps_value: 62.7, latest_eps_fy: 2027, latest_eps_period: 'FY',
    latest_eps_currency: 'TWD', latest_eps_unit: 'per_share',
    latest_report_date: '2024-10-27',
    report_link: { report_id: `r-${key}` },
    recent_change_label: '目標價上修 8.0%', recent_change_direction: 'up',
    stale: false, has_history: true,
    ...over,
  } as BrokerSummary
}

const EPS: EpsGroup = {
  fiscal_year: 2027, period: 'FY', currency: 'TWD', unit: 'per_share',
  median: 60, count: 3, revision_direction: 'none',
}

function history(): BrokerHistory {
  const snap = (id: string, date: string, rating: 'buy' | 'overweight', tp: number, inWindow = true) => ({
    report_id: id, report_date: date, in_window: inWindow, rating, rating_raw: null,
    target_price: tp, target_currency: 'TWD', eps: [], primary_eps: null,
    thesis: [], extraction_status: 'valid',
    report_link: { report_id: id, title: `報告 ${id}` },
  })
  return {
    market: 'TW', instrument_code: '2330', broker: 'daiwa', broker_display: '大和',
    window: '90', as_of: '2024-10-27', current_rating: 'buy', report_count: 2,
    snapshots: [snap('r2', '2024-10-27', 'buy', 1568), snap('r1', '2024-06-01', 'overweight', 1400, false)],
    diffs: [
      {
        to_report_date: '2024-10-27', from_report_date: '2024-06-01', has_prior_report: true, has_prior_comparable: true,
        changes: [{ field: 'rating', label: '加碼 → 買進', direction: 'up', comparable: true }],
      },
      { to_report_date: '2024-06-01', has_prior_report: false, has_prior_comparable: false, changes: [] },
    ],
    coverage_state: 'ok',
  } as unknown as BrokerHistory
}

function Harness({ brokers, onOpenReport = vi.fn() }: { brokers: BrokerSummary[]; onOpenReport?: () => void }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <BrokerTable
      brokers={brokers}
      code="2330"
      market="TW"
      window="90"
      targetCurrency="TWD"
      epsGroup={EPS}
      openBroker={open}
      onOpenBrokerChange={setOpen}
      onOpenReport={onOpenReport}
    />
  )
}

function renderTable(brokers: BrokerSummary[], onOpenReport = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Harness brokers={brokers} onOpenReport={onOpenReport} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('BrokerTable', () => {
  it('每列是展開鈕，可及名稱帶欄名與值', () => {
    renderTable([broker('daiwa')])
    const row = screen.getByTestId('broker-row-daiwa')
    expect(row).toHaveAttribute('aria-expanded', 'false')
    // jsdom 不會把格線子項目當成區塊，格與格之間的空白算不出來；瀏覽器裡格線子項目一律是區塊，
    // 所以這裡只釘「每個值前面都有欄名」與順序，空白用 \s* 放寬。
    expect(row).toHaveAccessibleName(
      /^大和\s*評等 買進\s*目標價 1,568\s*EPS 62\.7\s*近期變化 目標價上修 8\.0%\s*最新研報 2024-10-27$/,
    )
  })

  it('不同幣別的目標價與不同年度的 EPS 補上標示；沒有值是破折號', () => {
    renderTable([
      broker('ms', { latest_target_price: 52, latest_target_currency: 'USD', latest_eps_fy: 2026 }),
      broker('none', { latest_target_price: null, latest_eps_value: null, recent_change_label: null }),
    ])
    expect(screen.getByTestId('broker-row-ms')).toHaveTextContent('52USD')
    expect(screen.getByTestId('broker-row-ms')).toHaveTextContent('FY2026')
    const none = screen.getByTestId('broker-row-none')
    expect(none).toHaveAccessibleName(/目標價 未提供 EPS 未提供 近期變化 無變動/)
  })

  it('期間外的券商標「期間外」', () => {
    renderTable([broker('old', { stale: true })])
    expect(within(screen.getByTestId('broker-row-old')).getByText('期間外')).toBeInTheDocument()
  })

  it('展開後列出歷程：最新在上、備註寫這份改了什麼，點一列開研報', async () => {
    vi.mocked(radarApi.getBrokerHistory).mockResolvedValue(history())
    const onOpenReport = vi.fn()
    renderTable([broker('daiwa')], onOpenReport)
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))
    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'true')
    await screen.findByText('首次納入')
    const historyItems = screen.getAllByRole('listitem').filter(li => li.closest('ol'))
    expect(historyItems).toHaveLength(2)
    expect(within(historyItems[0]).getByRole('button'))
      .toHaveAccessibleName(/^2024-10-27\s*評等 買進\s*目標價 1,568\s*備註 評等 加碼 → 買進$/)
    expect(within(historyItems[1]).getByRole('button'))
      .toHaveAccessibleName(/^2024-06-01\s*評等 加碼\s*目標價 1,400\s*備註 首次納入$/)
    fireEvent.click(within(historyItems[0]).getByRole('button'))
    expect(onOpenReport).toHaveBeenCalledWith('r2', '報告 r2')
  })

  it('歷程載入失敗可重試', async () => {
    vi.mocked(radarApi.getBrokerHistory)
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValueOnce(history())
    renderTable([broker('daiwa')])
    fireEvent.click(screen.getByTestId('broker-row-daiwa'))
    const alert = await screen.findByRole('alert')
    fireEvent.click(within(alert).getByRole('button', { name: '重試' }))
    expect(await screen.findByText('首次納入')).toBeInTheDocument()
  })

  it('複製表格成功與失敗都有提示', async () => {
    const copy = vi.spyOn(clipboard, 'copyText').mockResolvedValueOnce(undefined)
    const show = vi.spyOn(toast, 'showToast')
    renderTable([broker('daiwa')])
    fireEvent.click(screen.getByRole('button', { name: '複製表格' }))
    await waitFor(() => expect(show).toHaveBeenCalledWith('已複製表格，可貼入試算表', { ok: true }))
    expect(copy.mock.calls[0][0].split('\n')[0].startsWith('券商\t評等')).toBe(true)

    copy.mockRejectedValueOnce(new Error('denied'))
    fireEvent.click(screen.getByRole('button', { name: '複製表格' }))
    await waitFor(() => expect(show).toHaveBeenLastCalledWith('複製失敗，請再試一次'))
  })

  it('沒有具名券商時不畫表格', () => {
    renderTable([broker('', { broker: '  ' })])
    expect(screen.getByText('還沒有具名券商的觀點')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '複製表格' })).not.toBeInTheDocument()
  })
})
