import { describe, it, expect } from 'vitest'
import { askHref, briefHighlights, fullDate, isSameLocalDay, monthOf, shortDate } from './libraryFormat'

const NOW = new Date(2026, 9, 28, 10, 0, 0)

describe('libraryFormat', () => {
  it('短日期：今年只寫月日，跨年寫完整日期', () => {
    expect(shortDate('2026-10-05', NOW)).toBe('10-05')
    expect(shortDate('2025-12-30', NOW)).toBe('2025-12-30')
    expect(shortDate(null, NOW)).toBe('')
    expect(fullDate('2026-10-05T00:00:00')).toBe('2026-10-05')
  })

  it('同一天與月份標題', () => {
    expect(isSameLocalDay('2026-10-28', NOW)).toBe(true)
    expect(isSameLocalDay('2026-10-27', NOW)).toBe(false)
    expect(monthOf('2026-10-05')).toBe('2026 年 10 月')
  })

  it('簡報重點只取「今日重點」段的條列，並拿掉結尾的券商代碼', () => {
    const md = [
      '## 今日重點',
      '- 美股主要指數收高，納指上漲 1.2%（yuanta）。',
      '- **台股**量能不足（kgi）',
      '- 第三條',
      '',
      '## 市場焦點',
      '- 不該被取到',
    ].join('\n')
    expect(briefHighlights(md, 4)).toEqual([
      '美股主要指數收高，納指上漲 1.2%。',
      '台股量能不足',
      '第三條',
    ])
  })

  it('沒有「今日重點」段時退回全文第一批條列，且遵守條數上限', () => {
    expect(briefHighlights('前言\n- 一\n- 二\n- 三', 2)).toEqual(['一', '二'])
    expect(briefHighlights('沒有條列', 4)).toEqual([])
  })

  it('就這篇提問的網址沿用閱讀頁寫法', () => {
    expect(askHref('南電')).toBe(`/assistant?q=${encodeURIComponent('關於《南電》：')}`)
  })
})
