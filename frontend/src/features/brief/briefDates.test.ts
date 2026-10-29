import { describe, expect, it } from 'vitest'
import { addMonths, dayTitle, mergeDates, monthCells, monthDay, neighbours, todayIso, yearWeekday } from './briefDates'

describe('briefDates', () => {
  it('大日期與年份星期', () => {
    expect(dayTitle('2026-10-28')).toBe('10 月 28 日')
    expect(yearWeekday('2026-10-28')).toBe('2026 年 星期三')
    expect(yearWeekday('2026-10-04')).toBe('2026 年 星期日')
  })

  it('今天取本機日期，不經 UTC 換算', () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe('2026-01-05')
  })

  it('來源研報的月日', () => {
    expect(monthDay('2026-10-27')).toBe('10-27')
    expect(monthDay(null)).toBe('')
  })

  it('月曆格：週日起算補空格，天數正確', () => {
    const oct = monthCells({ y: 2026, m: 10 })
    expect(oct.blanks).toBe(4) // 2026-10-01 是星期四
    expect(oct.days).toHaveLength(31)
    expect(monthCells({ y: 2028, m: 2 }).days).toHaveLength(29)
  })

  it('跨年翻月', () => {
    expect(addMonths({ y: 2026, m: 1 }, -1)).toEqual({ y: 2025, m: 12 })
    expect(addMonths({ y: 2026, m: 12 }, 1)).toEqual({ y: 2027, m: 1 })
  })

  it('合併日期清單：去重、新到舊、丟掉格式不對的', () => {
    expect(mergeDates(['2026-10-01', '2026-10-05'], ['2026-10-05', 'x', '2026-09-30'], undefined))
      .toEqual(['2026-10-05', '2026-10-01', '2026-09-30'])
  })

  it('前一份與後一份', () => {
    const dates = ['2026-10-05', '2026-10-03', '2026-10-02']
    expect(neighbours(dates, '2026-10-03')).toEqual({ older: '2026-10-02', newer: '2026-10-05' })
    expect(neighbours(dates, '2026-10-05')).toEqual({ older: '2026-10-03', newer: null })
    expect(neighbours(dates, '2026-10-02')).toEqual({ older: null, newer: '2026-10-03' })
    expect(neighbours(dates, '2026-10-04')).toEqual({ older: '2026-10-03', newer: '2026-10-05' })
  })
})
