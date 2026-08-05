import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { RatingConsensus } from '../../lib/radarSchemas'
import { ConsensusSnapshot } from './ConsensusSnapshot'
import styles from './ConsensusSnapshot.module.css'

function consensus(partial: Partial<RatingConsensus> = {}): RatingConsensus {
  return {
    distribution: [
      { rating: 'buy', count: 1 },
      { rating: 'overweight', count: 1 },
      { rating: 'neutral', count: 0 },
      { rating: 'underweight', count: 0 },
      { rating: 'sell', count: 0 },
    ],
    bullish: 2, neutral: 0, bearish: 0, unknown: 0,
    total_rated: 2, median_rating: 'overweight',
    upgrades: 0, downgrades: 0, unchanged: 2,
    ...partial,
  }
}

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

  it.each([
    ['淨下調時用下調色', { upgrades: 0, downgrades: 4 }, '淨下調 4 家', 'down'] as const,
    ['淨上調時用上調色', { upgrades: 3, downgrades: 0 }, '淨上調 3 家', 'up'] as const,
    ['持平時用中性色', { upgrades: 2, downgrades: 2 }, '持平', 'flat'] as const,
  ])('評等動能的顏色跟著方向走：%s', (_label, counts, text, tone) => {
    // 舊寫法把 .mvNet b 的 color 寫死成 --tf-success，於是「淨下調 4 家」與「持平」
    // 都印成成功綠——這是整區唯一一句直接下結論的話，顏色卻在說相反的事。
    // 這條只釘「TSX 有掛對方向 class」。另一半（.mvNet b 不得自帶 color，否則
    // 它的 0,1,1 會蓋掉 .down 的 0,1,0）用元件測試驗不到——class 照樣掛得上去、
    // 畫面卻不會變——所以那一半釘在 RadarCssContracts 的靜態字串契約裡。
    render(<ConsensusSnapshot window="90" rating={consensus(counts)} />)

    expect(screen.getByText(text)).toHaveClass(styles[tone])
    expect(styles[tone]).toBeTruthy()
  })

  it('中位立場吃語意桶著色，與標的卡的同一筆資料同色', () => {
    // 舊寫法一律鎏金：同一個「中位立場」在 picker 的標的卡是綠／墨／紅，
    // 進到詳情頁卻變金色，同一筆資料兩套語意色。
    const { rerender } = render(
      <ConsensusSnapshot window="90" rating={consensus({ median_rating: 'buy' })} />,
    )
    expect(screen.getByText('買進')).toHaveClass(styles.wBull)

    rerender(<ConsensusSnapshot window="90" rating={consensus({ median_rating: 'sell' })} />)
    expect(screen.getByText('賣出')).toHaveClass(styles.wBear)
  })
})
