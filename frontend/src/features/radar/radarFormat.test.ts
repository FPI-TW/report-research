import { describe, expect, it } from 'vitest'
import {
  epsFyLabel, fmtShortDate, fmtSignedPct, fmtValue, RATING_DISPLAY, WINDOW_OPTIONS,
} from './radarFormat'

describe('radarFormat', () => {
  it('RATING_DISPLAY 五級加未評等齊全', () => {
    expect(Object.keys(RATING_DISPLAY).sort()).toEqual(
      ['buy', 'neutral', 'overweight', 'sell', 'underweight', 'unknown'],
    )
  })

  it('fmtValue：≥100 取整數加千分位，其餘最多兩位小數，缺值是破折號', () => {
    expect(fmtValue(1568.4)).toBe('1,568')
    expect(fmtValue(62.7)).toBe('62.7')
    expect(fmtValue(5.125)).toBe('5.13')
    expect(fmtValue(null)).toBe('—')
  })

  it('fmtSignedPct：帶正負號、一位小數，負號是 U+2212', () => {
    expect(fmtSignedPct(4.24)).toBe('+4.2%')
    expect(fmtSignedPct(-6)).toBe('−6.0%')
    expect(fmtSignedPct(0)).toBe('0.0%')
    expect(fmtSignedPct(null)).toBe('')
  })

  describe('fmtShortDate', () => {
    const now = new Date(2026, 9, 28)
    it('今年只印月日', () => {
      expect(fmtShortDate('2026-10-27', now)).toBe('10-27')
      expect(fmtShortDate('2026-01-05T08:00:00Z', now)).toBe('01-05')
    })
    it('其他年份留年份，免得跨年的日期長得一樣', () => {
      expect(fmtShortDate('2025-10-27', now)).toBe('2025-10-27')
    })
    it('缺值或格式不符是破折號（後端 NULL 日期給空字串）', () => {
      expect(fmtShortDate('', now)).toBe('—')
      expect(fmtShortDate(null, now)).toBe('—')
      expect(fmtShortDate('not-a-date', now)).toBe('—')
    })
  })

  it('epsFyLabel：全年只印年度，其他期間接在後面', () => {
    expect(epsFyLabel(2027, 'FY')).toBe('FY2027')
    expect(epsFyLabel(2027, '1H')).toBe('FY2027 1H')
    expect(epsFyLabel(null, '1H')).toBe('1H')
    expect(epsFyLabel(null, null)).toBe('')
  })

  it('期間選項與 API 的 window 逐字相同', () => {
    expect(WINDOW_OPTIONS.map(o => o.value)).toEqual(['30', '90', '180', 'all'])
    expect(WINDOW_OPTIONS.map(o => o.label)).toEqual(['30 天', '90 天', '180 天', '全部'])
  })
})
