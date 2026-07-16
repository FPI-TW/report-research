import { describe, expect, it } from 'vitest'
import {
  currencyPrefix,
  directionVerb,
  fmtDate,
  fmtNum,
  fmtPct,
  fmtPrice,
  RATING_DISPLAY,
} from './radarFormat'

describe('radarFormat', () => {
  it('fmtPrice 含幣別前綴', () => {
    expect(fmtPrice(1185, 'TWD')).toBe('NT$1,185')
    expect(fmtPrice(null, 'TWD')).toBe('—')
  })

  it('fmtPct 取絕對值一位小數', () => {
    expect(fmtPct(-3.25)).toBe('3.3%')
    expect(fmtPct(null)).toBe('')
  })

  it('fmtDate 轉成 YYYY/MM/DD', () => {
    expect(fmtDate('2026-07-11')).toBe('2026/07/11')
    expect(fmtDate(null)).toBe('—')
  })

  it('directionVerb 區分評等與數值用語', () => {
    expect(directionVerb('up', 'rating')).toBe('上調')
    expect(directionVerb('up', 'number')).toBe('上修')
    expect(directionVerb('incomparable')).toBe('不可比較')
  })

  it('RATING_DISPLAY 五級齊全', () => {
    expect(RATING_DISPLAY.buy).toBe('買進')
    expect(RATING_DISPLAY.unknown).toBe('未評等')
  })

  it('currencyPrefix / fmtNum 基本行為', () => {
    expect(currencyPrefix('USD')).toBe('US$')
    expect(fmtNum(66.4)).toBe('66.4')
  })
})
