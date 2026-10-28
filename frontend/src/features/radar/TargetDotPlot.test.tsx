import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BrokerSummary, TargetGroup } from '../../lib/radarSchemas'
import { targetDots } from './consensusData'
import { TargetDotPlot } from './TargetDotPlot'

function broker(key: string, price: number | null, over: Partial<BrokerSummary> = {}): BrokerSummary {
  return {
    broker: key, broker_display: key.toUpperCase(),
    latest_rating: 'buy', latest_target_price: price, latest_target_currency: 'TWD',
    latest_report_date: '2026-10-20', stale: false, has_history: false,
    report_link: { report_id: key },
    recent_change_direction: 'none',
    ...over,
  } as BrokerSummary
}

const GROUP: TargetGroup = {
  currency: 'TWD', median: 1400, q1: 1300, q3: 1500, low: 1200, high: 1600,
  count: 4, revision_direction: 'none',
}

describe('targetDots', () => {
  it('只收期間內、同幣別、有目標價的券商，依價位排序並換成 0–100 的位置', () => {
    const dots = targetDots([
      broker('a', 1600), broker('b', 1200), broker('c', 1400),
      broker('usd', 40, { latest_target_currency: 'USD' }),
      broker('old', 1500, { stale: true }),
      broker('none', null),
    ], GROUP)
    expect(dots.map(d => d.key)).toEqual(['b', 'c', 'a'])
    expect(dots.map(d => d.x)).toEqual([0, 50, 100])
    expect(dots.every(d => d.level === 0)).toBe(true)
  })

  it('靠得太近的點往上疊', () => {
    const dots = targetDots([broker('a', 1400), broker('b', 1401), broker('c', 1402), broker('d', 1600)], {
      ...GROUP, low: 1200,
    })
    expect(dots.map(d => d.level)).toEqual([0, 1, 2, 0])
  })

  it('只有一家（全距為 0）時放在正中間', () => {
    const dots = targetDots([broker('a', 1400)], { ...GROUP, low: 1400, high: 1400 })
    expect(dots[0].x).toBe(50)
  })
})

describe('TargetDotPlot', () => {
  it('每個點是按鈕，可及名稱是券商與價位；點下去選那家券商', () => {
    const onSelect = vi.fn()
    render(<TargetDotPlot brokers={[broker('a', 1600), broker('b', 1200)]} group={GROUP} onSelect={onSelect} />)
    expect(screen.getByRole('heading', { name: '各券商目標價' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '各券商目標價分布，中位數 1,400' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'A，目標價 1,600' }))
    expect(onSelect).toHaveBeenCalledWith('a')
  })

  it('沒有可畫的點就整張不出現', () => {
    const { container } = render(
      <TargetDotPlot brokers={[broker('a', null)]} group={GROUP} onSelect={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
