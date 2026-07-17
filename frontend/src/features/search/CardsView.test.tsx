import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { CardsView } from './CardsView'
import { MonthGroup } from './MonthGroup'
import type { ReportRow } from '../../lib/schemas'

function row(id: string, date: string | null): ReportRow {
  return {
    report_id: id, file_hash: id.repeat(64).slice(0, 64), file_name: id + '.pdf',
    market: 'TW', source: null, summary: null,
    report_date: date, report_type: null, instrument_types: null,
    relates_stock: null, relates_futures: null, stock_targets: null, futures_targets: null,
  }
}

describe('MonthGroup', () => {
  it('顯示標題、篇數、子內容', () => {
    render(<MonthGroup title="2026 年 6 月" count={3}><div>卡片</div></MonthGroup>)
    expect(screen.getByText('2026 年 6 月')).toBeTruthy()
    expect(screen.getByText('3 篇')).toBeTruthy()
    expect(screen.getByText('卡片')).toBeTruthy()
  })
})

describe('CardsView', () => {
  it('依月分組並標記最新', () => {
    render(
      <MemoryRouter>
        <CardsView rows={[row('a', '2026-06-25'), row('b', '2026-05-01')]}
          mode="browse" terms={[]} latestId="a" />
      </MemoryRouter>,
    )
    expect(screen.getByText('2026 年 6 月')).toBeTruthy()
    expect(screen.getByText('2026 年 5 月')).toBeTruthy()
    expect(screen.getByText('最新')).toBeTruthy()
  })
})
