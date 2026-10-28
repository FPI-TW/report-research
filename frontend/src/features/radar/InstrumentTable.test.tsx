import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { CatalogSort, RadarInstrumentItem } from '../../lib/radarSchemas'
import { changeParts } from './catalogState'
import catalogSource from './catalogState.ts?raw'
import { InstrumentTable } from './InstrumentTable'
import tableSource from './InstrumentTable.tsx?raw'
import tableCss from './InstrumentTable.module.css?raw'

function item(partial?: Partial<RadarInstrumentItem>): RadarInstrumentItem {
  return {
    market: 'TW',
    market_display: '台股',
    instrument_code: '2330',
    instrument_name: '台積電',
    broker_count: 20,
    report_count: 1772,
    latest_report_date: '2026-08-07',
    coverage_state: 'ok',
    consensus: {
      window: '90',
      stance: {
        rating: 'buy',
        bullish: 12, neutral: 7, bearish: 1, total_rated: 20,
        // 後端恆回五級（zod 也要求 length(5)），零家數的級別同樣在陣列裡
        distribution: [
          { rating: 'buy', count: 9 },
          { rating: 'overweight', count: 3 },
          { rating: 'neutral', count: 7 },
          { rating: 'underweight', count: 0 },
          { rating: 'sell', count: 1 },
        ],
        upgrades: 2, downgrades: 0, net_rating: 2,
      },
      target: { revision_direction: 'none' },
    },
    ...partial,
  }
}

function Probe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}{location.search}</div>
}

function renderTable(items: RadarInstrumentItem[], opts: { sort?: CatalogSort; loading?: boolean } = {}) {
  return render(
    <MemoryRouter initialEntries={['/consensus']}>
      <Routes>
        <Route
          path="/consensus"
          element={(
            <>
              <InstrumentTable
                items={items}
                sort={opts.sort ?? 'latest'}
                loading={opts.loading}
                hrefFor={(m, c) => `/consensus?market=${m}&code=${c}`}
              />
              <Probe />
            </>
          )}
        />
      </Routes>
    </MemoryRouter>,
  )
}

function firstRow() {
  return screen.getAllByRole('row')[1]
}

describe('InstrumentTable', () => {
  it('六欄表頭依交接包順序', () => {
    renderTable([item()])
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toEqual([
      '標的', '共識評等', '評等分布', '近期變化', '資料涵蓋', '最新研報',
    ])
  })

  it('標的欄是列標題：名稱是連結，下方是代號與市場', () => {
    renderTable([item()])
    const head = within(firstRow()).getByRole('rowheader')
    expect(within(head).getByRole('link', { name: '台積電' })).toHaveAttribute('href', '/consensus?market=TW&code=2330')
    expect(head).toHaveTextContent('2330')
    expect(head).toHaveTextContent('台股')
  })

  it('共識評等是徽章，評等分布印出有家數的級別', () => {
    renderTable([item()])
    const row = firstRow()
    expect(within(row).getByText('買進', { selector: 'span[class*="badge"]' })).toBeInTheDocument()
    // 減碼 0 家不印；全形空白分隔，不用「·」
    // getByText 會把全形空白正規化成半形；原文仍要是全形空白
    expect(within(row).getByText('買進 9 加碼 3 中立 7 賣出 1').textContent).toBe('買進 9\u3000加碼 3\u3000中立 7\u3000賣出 1')
  })

  it('近期變化：調升與調降各自列出家數', () => {
    expect(changeParts(item())).toEqual([{ tone: 'up', text: '調升 2' }])
    const both = item({
      consensus: { ...item().consensus!, stance: { ...item().consensus!.stance, upgrades: 1, downgrades: 2 } },
    })
    expect(changeParts(both)).toEqual([
      { tone: 'up', text: '調升 1' },
      { tone: 'down', text: '調降 2' },
    ])
  })

  it('評等沒有變動時才看目標價方向，而且只有方向', () => {
    const flat = item({
      consensus: {
        ...item().consensus!,
        stance: { ...item().consensus!.stance, upgrades: 0, downgrades: 0, net_rating: 0 },
        target: { revision_direction: 'down' },
      },
    })
    expect(changeParts(flat)).toEqual([{ tone: 'down', text: '目標價下修' }])
    renderTable([{ ...flat, consensus: { ...flat.consensus!, target: { revision_direction: 'none' } } }])
    expect(within(firstRow()).getByText('無變動')).toBeInTheDocument()
  })

  it('資料涵蓋印券商數與篇數，最新研報印短日期', () => {
    renderTable([item({ latest_report_date: '2024-08-07' })])
    const row = firstRow()
    expect(row).toHaveTextContent('20 家券商')
    expect(row).toHaveTextContent('1772 篇')
    // 不是今年就留年份
    expect(within(row).getByText('2024-08-07')).toBeInTheDocument()
  })

  it('清單不出現任何目標價數值，原始碼也不碰目標價聚合欄位', () => {
    renderTable([item()])
    expect(firstRow().textContent).not.toMatch(/NT\$|目標價\s*\d/)
    for (const source of [tableSource, catalogSource]) {
      for (const banned of ['target.median', 'revision_pct', 'fmtValue', 'fmtPrice']) {
        expect(source, banned).not.toContain(banned)
      }
    }
  })

  it('還沒擷取出共識的列：評等為破折號，分布寫資料擷取中', () => {
    renderTable([item({ consensus: null })])
    const row = firstRow()
    expect(row).toHaveTextContent('資料擷取中')
    expect(within(row).queryByText('無變動')).not.toBeInTheDocument()
  })

  it('每列只有一個連結（Tab 停點），整列可點進詳情', () => {
    renderTable([item(), item({ instrument_code: '2317', instrument_name: '鴻海' })])
    const rows = screen.getAllByRole('row').slice(1)
    for (const row of rows) expect(within(row).getAllByRole('link')).toHaveLength(1)
    fireEvent.click(within(rows[1]).getByText(/08-07$/))
    expect(screen.getByTestId('location')).toHaveTextContent('/consensus?market=TW&code=2317')
  })

  it('載入中顯示骨架列，表頭留著', () => {
    renderTable([], { loading: true })
    expect(screen.getAllByTestId('picker-skeleton')).toHaveLength(5)
    expect(screen.getAllByRole('columnheader')).toHaveLength(6)
    expect(screen.getByRole('table')).toHaveAttribute('aria-busy', 'true')
  })

  it('目前的排序反映在對應欄的 aria-sort', () => {
    const { unmount } = renderTable([item()], { sort: 'reports' })
    expect(screen.getByRole('columnheader', { name: '資料涵蓋' })).toHaveAttribute('aria-sort', 'descending')
    expect(screen.getByRole('columnheader', { name: '最新研報' })).not.toHaveAttribute('aria-sort')
    unmount()
    renderTable([item()], { sort: 'code' })
    expect(screen.getByRole('columnheader', { name: '標的' })).toHaveAttribute('aria-sort', 'ascending')
  })
})

describe('InstrumentTable 版面契約', () => {
  it('欄寬是交接包定稿值', () => {
    expect(tableCss).toContain('grid-template-columns: minmax(0, 1.6fr) 90px minmax(0, 1.4fr) 130px 150px 80px;')
  })

  it('列的進場動畫與按壓回饋尊重減少動態', () => {
    const reduced = tableCss.slice(tableCss.indexOf('@media (prefers-reduced-motion: reduce)'))
    expect(reduced).toMatch(/\.row\s*\{[^}]*animation:\s*none/)
  })
})
