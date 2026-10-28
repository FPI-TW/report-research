import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { EpsGroup, RatingConsensus, TargetConsensus } from '../../lib/radarSchemas'
import { ConsensusCards } from './ConsensusCards'
import { primaryTargetGroup } from './consensusData'

const RATING: RatingConsensus = {
  distribution: [
    { rating: 'buy', count: 6 },
    { rating: 'overweight', count: 2 },
    { rating: 'neutral', count: 1 },
    { rating: 'underweight', count: 0 },
    { rating: 'sell', count: 0 },
  ],
  bullish: 8, neutral: 1, bearish: 0, unknown: 0, total_rated: 9,
  median_rating: 'buy', upgrades: 2, downgrades: 1, unchanged: 6,
}

const TARGET: TargetConsensus = {
  primary_currency: 'TWD',
  groups: [
    { currency: 'USD', median: 45, q1: 44, q3: 46, low: 40, high: 50, count: 1, revision_direction: 'none' },
    {
      currency: 'TWD', median: 1400, q1: 1300, q3: 1500, low: 1190, high: 1652, count: 7,
      revision_pct: 4.2, revision_direction: 'up',
    },
  ],
  note: '另有 1 家 USD 計價，未併入 TWD 中位數',
}

const EPS: EpsGroup = {
  fiscal_year: 2027, period: 'FY', currency: 'TWD', unit: 'per_share',
  median: 63.6, count: 6, revision_pct: -2.1, revision_direction: 'down',
}

function card(name: string) {
  return screen.getByRole('region', { name })
}

describe('ConsensusCards', () => {
  it('共識評等：中位數、圖例家數、調升調降', () => {
    render(<ConsensusCards rating={RATING} target={TARGET} eps={EPS} />)
    const c = card('共識評等')
    expect(c).toHaveTextContent('買進中位數')
    const legend = within(c).getByRole('list', { name: '評等分布' })
    expect(within(legend).getAllByRole('listitem').map(li => li.textContent)).toEqual(['買進6', '加碼2', '中立1'])
    expect(c).toHaveTextContent('調升 2')
    expect(c).toHaveTextContent('調降 1')
  })

  it('目標價中位數取主要幣別那一組，附區間與較前次', () => {
    expect(primaryTargetGroup(TARGET)?.currency).toBe('TWD')
    render(<ConsensusCards rating={RATING} target={TARGET} eps={EPS} />)
    const c = card('目標價中位數')
    expect(c).toHaveTextContent('1,400TWD')
    expect(within(c).getByRole('img')).toHaveAccessibleName('目標價區間 1,190 至 1,652，中位數 1,400，7 家券商')
    expect(c).toHaveTextContent('另有 1 家 USD 計價')
    expect(c).toHaveTextContent('較前次上升+4.2%')
  })

  it('EPS 中位數：年度、家數、較前次下降', () => {
    render(<ConsensusCards rating={RATING} target={TARGET} eps={EPS} />)
    const c = card('EPS 中位數 FY2027')
    expect(c).toHaveTextContent('63.6TWD')
    expect(c).toHaveTextContent('6 家券商提供')
    expect(c).toHaveTextContent('較前次下降−2.1%')
  })

  it('沒有資料時各卡片照實說沒有', () => {
    render(<ConsensusCards rating={null} target={null} eps={null} />)
    expect(card('共識評等')).toHaveTextContent('尚無評等')
    expect(card('目標價中位數')).toHaveTextContent('尚無目標價')
    expect(card('EPS 中位數')).toHaveTextContent('尚無 EPS 預估')
  })

  it('沒有可比較的前次報告時較前次是破折號', () => {
    render(<ConsensusCards rating={RATING} target={{ ...TARGET, groups: [{ ...TARGET.groups[1], revision_pct: null, revision_direction: 'none' }] }} eps={null} />)
    expect(card('目標價中位數')).toHaveTextContent('較前次—')
  })
})
