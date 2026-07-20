import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConsensusSnapshot } from './ConsensusSnapshot'

describe('ConsensusSnapshot', () => {
  it('將中位標線固定在五級量表的加碼級距中心，而非分布條的 75% 處', () => {
    render(
      <ConsensusSnapshot
        window="90"
        rating={{
          distribution: [
            { rating: 'buy', count: 1 },
            { rating: 'overweight', count: 1 },
            { rating: 'neutral', count: 0 },
            { rating: 'underweight', count: 0 },
            { rating: 'sell', count: 0 },
          ],
          bullish: 2,
          neutral: 0,
          bearish: 0,
          unknown: 0,
          total_rated: 2,
          median_rating: 'overweight',
          upgrades: 0,
          downgrades: 0,
          unchanged: 2,
        }}
      />,
    )

    expect(screen.getByText('中位').parentElement).toHaveStyle({ left: '30%' })
  })
})
