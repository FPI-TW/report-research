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

  it('stale 券商在桌機與手機兩份 DOM 都標示「窗外最新」', () => {
    // 兩份 DOM 是各自手寫的，桌機有、手機漏掉過一次。stale 代表這家券商在所選窗期內
    // 沒有訊號、顯示的是窗外最近一筆——窗期正是這頁的語意核心，少了它，同一家券商
    // 在寬窄兩種螢幕上講的是不同的話（≤900px 會把窗期外的舊評等讀成當期最新）。
    render(
      <BrokerList
        brokers={[broker({ stale: true })]}
        code="8046"
        market="TW"
        window="30"
        onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getAllByText('窗外最新')).toHaveLength(2)
  })

  it('手機展開鈕帶自己的可及名稱，不靠整張卡的數據串接', () => {
    // 沒有 aria-label 時，按鈕名稱會變成「大和 · Buy 目標價 NT$120 EPS … 2026/07/11」
    // 一長串，沒有任何一個字說明按下去會發生什麼（aria-expanded 只唸得出「已收合」）。
    render(
      <BrokerList
        brokers={[broker()]}
        code="8046"
        market="TW"
        window="90"
        onOpenReport={vi.fn()}
      />,
    )

    // 桌機與手機各一顆，兩者名稱一致。
    // 需要 hidden: true —— .cards 的**基礎**樣式就是 display: none（手機版靠
    // @media (max-width: 900px) 才打開），而 jsdom 不求值 media query，所以手機那份 DOM
    // 在 role 查詢眼中恆為不可及。這也是同檔其他測試改用 getByTestId 取手機節點的原因。
    expect(screen.getAllByRole('button', { name: '展開 大和 歷程', hidden: true }))
      .toHaveLength(2)
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
