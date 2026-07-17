import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KeyFigures } from './KeyFigures'

describe('KeyFigures', () => {
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
