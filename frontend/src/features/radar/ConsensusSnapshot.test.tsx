import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { RatingConsensus } from '../../lib/radarSchemas'
import { ConsensusSnapshot } from './ConsensusSnapshot'
import styles from './ConsensusSnapshot.module.css'
import { RATING_SAMPLE_MIN } from './radarFormat'

function consensus(partial: Partial<RatingConsensus> = {}): RatingConsensus {
  return {
    distribution: [
      { rating: 'buy', count: 2 },
      { rating: 'overweight', count: 1 },
      { rating: 'neutral', count: 1 },
      { rating: 'underweight', count: 0 },
      { rating: 'sell', count: 0 },
    ],
    bullish: 3, neutral: 1, bearish: 0, unknown: 0,
    total_rated: 4, median_rating: 'overweight',
    upgrades: 1, downgrades: 0, unchanged: 3,
    ...partial,
  }
}

describe('ConsensusSnapshot（評等共識窄列）', () => {
  it.each([
    ['上調', 'rating-upgrades', '1', 'up'],
    ['下調', 'rating-downgrades', '0', 'down'],
    ['維持', 'rating-unchanged', '3', 'flat'],
  ] as const)('評等升降的 %s 數字掛對方向 class', (_label, testId, value, tone) => {
    // 這條只釘「TSX 有掛對方向 class」。另一半（.movement b 不得自帶 color，否則
    // 它的 0,1,1 會蓋掉 .down 的 0,1,0）用元件測試驗不到——class 照樣掛得上去、
    // 畫面卻不會變——所以那一半釘在 RadarCssContracts 的靜態字串契約裡。
    render(<ConsensusSnapshot window="90" rating={consensus()} />)

    expect(styles[tone]).toBeTruthy()
    expect(within(screen.getByTestId(testId)).getByText(value)).toHaveClass(styles[tone])
  })

  it('中位立場吃語意桶著色，與標的卡的同一筆資料同色', () => {
    const { rerender } = render(
      <ConsensusSnapshot window="90" rating={consensus({ median_rating: 'buy' })} />,
    )
    expect(screen.getByText('買進')).toHaveClass(styles.wBull)

    rerender(<ConsensusSnapshot window="90" rating={consensus({ median_rating: 'sell' })} />)
    expect(screen.getByText('賣出')).toHaveClass(styles.wBear)
  })

  it('分布條是唯一非文字元素，必須帶完整的 aria-label', () => {
    render(<ConsensusSnapshot window="90" rating={consensus()} />)

    expect(screen.getByRole('img')).toHaveAccessibleName(
      '評等分布，共 4 家：買進 2 家、加碼 1 家、中立 1 家',
    )
  })

  it('五級明細只在有兩級以上時才印（單一級距時它逐字等於「N 家已評等」）', () => {
    const single = consensus({
      distribution: [
        { rating: 'buy', count: 1 },
        { rating: 'overweight', count: 0 },
        { rating: 'neutral', count: 0 },
        { rating: 'underweight', count: 0 },
        { rating: 'sell', count: 0 },
      ],
      bullish: 1, neutral: 0, bearish: 0, total_rated: 1, median_rating: 'buy',
    })
    const { rerender } = render(<ConsensusSnapshot window="90" rating={single} />)
    expect(screen.getByText('1 家已評等')).toBeInTheDocument()
    expect(screen.queryByText('買進 1')).not.toBeInTheDocument()

    rerender(<ConsensusSnapshot window="90" rating={consensus()} />)
    for (const text of ['買進 2', '加碼 1', '中立 1']) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
  })

  it('五級明細各自帶色點，分佈條的顏色才有地方對照', () => {
    // 改版前那份色點圖例被拿掉後，整頁沒有任何 rating → 色 的對照，
    // 而分佈條仍然是五種顏色——等於畫了一個沒有圖例的色彩編碼。
    render(<ConsensusSnapshot window="90" rating={consensus()} />)

    const swatch = screen.getByText('買進 2').querySelector('i')
    expect(swatch).toHaveClass(styles.buy)
    expect(styles.buy).toBeTruthy()
  })

  it(`已評等券商少於 ${RATING_SAMPLE_MIN} 家時標示樣本不足，達門檻即消失`, () => {
    const { rerender } = render(
      <ConsensusSnapshot window="90" rating={consensus({ total_rated: RATING_SAMPLE_MIN - 1 })} />,
    )
    expect(screen.getByText('樣本不足')).toBeInTheDocument()

    rerender(
      <ConsensusSnapshot window="90" rating={consensus({ total_rated: RATING_SAMPLE_MIN })} />,
    )
    expect(screen.queryByText('樣本不足')).not.toBeInTheDocument()
  })

  it('窗期字樣跟著 window 走，不寫死 90 天', () => {
    const { rerender } = render(<ConsensusSnapshot window="30" rating={consensus()} />)
    expect(screen.getByText(/近 30 天/)).toBeInTheDocument()

    rerender(<ConsensusSnapshot window="all" rating={consensus()} />)
    expect(screen.getByText(/全部歷程/)).toBeInTheDocument()
  })

  it.each([
    ['null', null],
    ['零家已評等', { total_rated: 0 }],
  ] as const)('%s 時退成說明句而不是空白列', (_label, partial) => {
    render(
      <ConsensusSnapshot
        window="90"
        rating={partial === null ? null : consensus(partial)}
      />,
    )
    expect(screen.getByText('此窗期尚無評等分布。')).toBeInTheDocument()
  })
})
