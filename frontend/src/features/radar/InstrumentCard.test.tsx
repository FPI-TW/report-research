import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { RadarInstrumentItem } from '../../lib/radarSchemas'
import { InstrumentCard } from './InstrumentCard'

function item(partial?: Partial<RadarInstrumentItem>): RadarInstrumentItem {
  return {
    market: 'TW', market_display: '台股',
    instrument_code: '2330', instrument_name: '台積電',
    broker_count: 22, report_count: 68,
    latest_report_date: '2026-07-14', coverage_state: 'ok',
    consensus: {
      window: '90',
      stance: {
        rating: 'buy', bullish: 12, neutral: 5, bearish: 1, total_rated: 18,
        distribution: [
          { rating: 'buy', count: 9 }, { rating: 'overweight', count: 3 },
          { rating: 'neutral', count: 5 }, { rating: 'underweight', count: 1 },
          { rating: 'sell', count: 0 },
        ],
        upgrades: 6, downgrades: 0, net_rating: 6,
      },
      target: { currency: 'TWD', median: 1280, revision_pct: 6.2, revision_direction: 'up' },
    },
    ...partial,
  }
}

describe('InstrumentCard', () => {
  it('有共識時顯示立場、淨變動與目標價', () => {
    render(<InstrumentCard item={item()} onSelect={() => {}} />)
    expect(screen.getByText('台積電')).toBeInTheDocument()
    expect(screen.getByText('買進')).toBeInTheDocument() // 中位立場
    expect(screen.getByText('淨上調 6')).toBeInTheDocument()
    expect(screen.getByText(/NT\$1,280/)).toBeInTheDocument()
  })

  it('無共識時顯示擷取中淡態', () => {
    render(<InstrumentCard item={item({ consensus: null })} onSelect={() => {}} />)
    expect(screen.getByText('台積電')).toBeInTheDocument()
    expect(screen.getByText(/資料擷取中/)).toBeInTheDocument()
  })

  it('點擊卡片回傳 market/code', () => {
    const onSelect = vi.fn()
    render(<InstrumentCard item={item()} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('TW', '2330')
  })
})
