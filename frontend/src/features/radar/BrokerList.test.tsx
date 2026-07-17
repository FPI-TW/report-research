import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BrokerSummary } from '../../lib/radarSchemas'
import { BrokerList } from './BrokerList'

function broker(partial: Partial<BrokerSummary> = {}): BrokerSummary {
  return {
    broker: 'daiwa', broker_display: '大和', latest_rating: 'buy',
    latest_rating_raw: 'Buy', latest_target_price: 120, latest_target_currency: 'TWD',
    latest_eps_value: 5, latest_eps_fy: 2027, latest_eps_period: 'FY',
    latest_eps_currency: 'USD', latest_eps_unit: 'per_share',
    latest_report_date: '2026-07-11',
    report_link: { report_id: 'r1' },
    recent_change_label: null, recent_change_direction: 'none',
    stale: false, has_history: true,
    ...partial,
  }
}

describe('BrokerList', () => {
  it('桌機與手機都使用 EPS 自身 metadata，不借用目標價幣別', () => {
    render(
      <BrokerList
        brokers={[broker()]}
        code="8046"
        market="TW"
        window="90"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByTestId('broker-eps-daiwa-desktop')).toHaveTextContent(
      'US$5 · FY2027 · FY · 每股',
    )
    expect(screen.getByTestId('broker-eps-daiwa-mobile')).toHaveTextContent(
      'US$5 · FY2027 · FY · 每股',
    )
    expect(screen.queryByText(/NT\$5/)).not.toBeInTheDocument()
    expect(screen.getByTestId('broker-row-daiwa')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByTestId('broker-row-daiwa').tagName).toBe('BUTTON')
  })

  it('防禦性過濾無 canonical broker 的舊後端資料', () => {
    render(
      <BrokerList
        brokers={[broker({ broker: '   ', broker_display: null })]}
        code="8046"
        market="TW"
        window="90"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByText('尚無券商清單')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /歷程/ })).not.toBeInTheDocument()
  })
})
