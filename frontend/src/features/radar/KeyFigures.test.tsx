import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KeyFigures } from './KeyFigures'

describe('KeyFigures', () => {
  it('長目標價會保留完整數字，不會在千分位數字中間斷行', () => {
    render(
      <KeyFigures
        target={{
          primary_currency: 'USD',
          groups: [{
            currency: 'USD', median: 123456, q1: 120000, q3: 125000, low: 110000, high: 130000,
            count: 2, revision_pct: null, revision_direction: 'none',
          }],
          note: null,
        }}
        eps={null}
        coverage={{
          state: 'ok',
          brokers_total: 2,
          brokers_extracted: 2,
          brokers_in_consensus: 2,
          reports_available: 2,
          note: '',
        }}
      />,
    )

    expect(screen.getByText('123,456')).toHaveStyle({ whiteSpace: 'nowrap' })
  })

  it('partial 即使已擷取比例為 100%，品質卡仍明示非完整品質', () => {
    render(
      <KeyFigures
        target={null}
        eps={null}
        coverage={{
          state: 'partial',
          brokers_total: 2,
          brokers_extracted: 2,
          brokers_in_consensus: 2,
          reports_available: 8,
          note: '歷史研報仍有部分欄位尚待整理。',
        }}
      />,
    )

    const qualityCard = screen.getByText('資料品質').parentElement
    expect(qualityCard).not.toBeNull()
    expect(within(qualityCard!).getByText('2 / 2 家')).toBeInTheDocument()
    expect(within(qualityCard!).getByText('部分資料 · 非完整品質')).toBeInTheDocument()
    expect(qualityCard!.querySelector('i')).toHaveStyle({ width: '100%' })
  })
})
