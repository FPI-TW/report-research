import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrokerHistory, BrokerSnapshot } from '../../lib/radarSchemas'
import { BrokerTimeline } from './BrokerTimeline'
import { useBrokerHistory } from './useRadar'

vi.mock('./useRadar', () => ({ useBrokerHistory: vi.fn() }))

function snapshot(
  reportId: string,
  reportDate: string,
  thesis: BrokerSnapshot['thesis'] = [],
): BrokerSnapshot {
  return {
    report_id: reportId, report_date: reportDate, in_window: true,
    rating: 'buy', rating_raw: 'Buy', target_price: 120, target_currency: 'TWD',
    eps: [{
      fiscal_year: 2025, period: 'FY', currency: 'TWD', unit: 'per_share',
      median: 50, count: 1, revision_pct: null, revision_direction: 'none',
    }],
    primary_eps: {
      fiscal_year: 2027, period: 'FY', currency: 'TWD', unit: 'per_share',
      median: 66.4, count: 1, revision_pct: null, revision_direction: 'none',
    },
    thesis, extraction_status: 'valid', report_link: { report_id: reportId },
  }
}

function history(partial: Partial<BrokerHistory> = {}): BrokerHistory {
  return {
    market: 'TW', instrument_code: '8046', broker: 'daiwa', broker_display: '大和',
    window: '90', as_of: '2026-07-11', current_rating: 'buy', report_count: 3,
    snapshots: [
      snapshot('r3', '2026-07-11', [
        {
          dimension: 'outlook', dimension_display: '展望', stance: 'positive',
          summary: '需求轉強', evidence: '展望證據',
        },
        {
          dimension: 'catalyst', dimension_display: '催化劑', stance: 'positive',
          summary: '新產品放量', evidence: '催化劑證據',
        },
      ]),
      snapshot('r2', '2026-06-01'),
      snapshot('r1', '2026-05-01'),
    ],
    diffs: [{
      from_report_id: 'r1', from_report_date: '2026-05-01', to_report_date: '2026-07-11',
      changes: [{
        field: 'eps', dimension: null, label: 'EPS 組別變更',
        direction: 'incomparable', prev_value: 'FY2026 · TWD',
        curr_value: 'FY2027 · USD', pct_change: 99.9, comparable: false,
        reason_code: 'eps_group_mismatch',
        incomparable_reason: 'FY 與幣別不同，無法直接比較',
      }],
      has_prior_report: true, has_prior_comparable: false, note: '沒有可比較數值',
    }],
    coverage_state: 'ok',
    ...partial,
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(useBrokerHistory).mockReturnValue({
    data: history(), isLoading: false, isError: false, refetch: vi.fn(),
  } as never)
})

describe('BrokerTimeline', () => {
  it('使用 from_report_id 對準前次可比較研報，並完整顯示不可比較原因', () => {
    render(
      <BrokerTimeline
        code="8046" market="TW" broker="daiwa" window="90" expanded
        onCollapse={vi.fn()} onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByText('前次研報（2026/05/01）')).toBeInTheDocument()
    expect(screen.getAllByText('不可比較').length).toBeGreaterThan(0)
    expect(screen.getAllByText('FY2026 · TWD → FY2027 · USD').length).toBeGreaterThan(0)
    expect(screen.getAllByText('FY 與幣別不同，無法直接比較').length).toBeGreaterThan(0)
    expect(screen.queryByText('99.9%')).not.toBeInTheDocument()
  })

  it('使用 primary EPS 並逐項呈現所有 thesis 證據', () => {
    render(
      <BrokerTimeline
        code="8046" market="TW" broker="daiwa" window="90" expanded
        onCollapse={vi.fn()} onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getAllByText('NT$66.40 · FY2027 · FY · 每股').length).toBeGreaterThan(0)
    expect(screen.getByText('展望').parentElement).toHaveTextContent('展望：需求轉強 — 展望證據')
    expect(screen.getByText('催化劑').parentElement).toHaveTextContent(
      '催化劑：新產品放量 — 催化劑證據',
    )
  })

  it('pending_extraction 顯示整理中，而非一般載入錯誤', () => {
    vi.mocked(useBrokerHistory).mockReturnValue({
      data: history({
        report_count: 0, snapshots: [], diffs: [], coverage_state: 'pending_extraction',
      }),
      isLoading: false, isError: false, refetch: vi.fn(),
    } as never)

    render(
      <BrokerTimeline
        code="8046" market="TW" broker="daiwa" window="90" expanded
        onCollapse={vi.fn()} onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByText('此券商研報尚待觀點資料整理')).toBeInTheDocument()
    expect(screen.queryByText('載入券商歷程失敗。')).not.toBeInTheDocument()
  })

  it('has_prior_report=false 時不誤用第二份 snapshot', () => {
    vi.mocked(useBrokerHistory).mockReturnValue({
      data: history({
        diffs: [{
          from_report_id: null, from_report_date: null, to_report_date: '2026-07-11',
          changes: [], has_prior_report: false, has_prior_comparable: false,
          note: '沒有前次研報',
        }],
      }),
      isLoading: false, isError: false, refetch: vi.fn(),
    } as never)

    render(
      <BrokerTimeline
        code="8046" market="TW" broker="daiwa" window="90" expanded
        onCollapse={vi.fn()} onOpenReport={vi.fn()}
      />,
    )

    expect(screen.getByText('前次研報')).toBeInTheDocument()
    expect(screen.queryByText('前次研報（2026/06/01）')).not.toBeInTheDocument()
    expect(screen.getAllByText('沒有前次研報').length).toBeGreaterThan(0)
    expect(screen.queryByText(/沒有前次可比較研報/)).not.toBeInTheDocument()
  })
})
