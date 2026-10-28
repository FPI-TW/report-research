import { describe, it, expect } from 'vitest'
import { fmtDate, fmtRevisionPct, fmtTargetPrice, isValidHash, reportHref } from './readingFormat'

const HASH = 'a'.repeat(64)

describe('isValidHash', () => {
  it('64-hex 通過（大小寫皆可）', () => {
    expect(isValidHash(HASH)).toBe(true)
    expect(isValidHash('A'.repeat(64))).toBe(true)
  })
  it('長度不符或含非 hex 字元一律擋下', () => {
    expect(isValidHash('a'.repeat(63))).toBe(false)
    expect(isValidHash('a'.repeat(65))).toBe(false)
    expect(isValidHash('g'.repeat(64))).toBe(false)
    expect(isValidHash('')).toBe(false)
    expect(isValidHash(null)).toBe(false)
    expect(isValidHash(undefined)).toBe(false)
  })
})

describe('reportHref', () => {
  // 反向釘死：閱讀頁已無命中定位的落點，連結不得再帶任何 query。
  // 帶了不會報錯、只會在網址列留下一個沒有消費端的參數。
  it('永遠是乾淨的閱讀頁網址，不帶 query', () => {
    expect(reportHref(HASH)).toBe(`/report/${HASH}`)
    expect(reportHref(HASH)).not.toContain('?')
  })
})

describe('fmtDate', () => {
  it('只取 YYYY-MM-DD，缺值回 null', () => {
    expect(fmtDate('2026-07-14T00:00:00')).toBe('2026-07-14')
    expect(fmtDate(null)).toBeNull()
    expect(fmtDate(undefined)).toBeNull()
  })
})

describe('fmtTargetPrice', () => {
  it('缺值回破折號', () => {
    expect(fmtTargetPrice(null)).toBe('—')
    expect(fmtTargetPrice(undefined)).toBe('—')
  })
  it('整數不補小數', () => {
    expect(fmtTargetPrice(2444)).toBe('2,444')
  })
})

describe('fmtRevisionPct', () => {
  it('取絕對值；整數不補小數，其餘留一位', () => {
    expect(fmtRevisionPct(8)).toBe('8%')
    expect(fmtRevisionPct(-5)).toBe('5%')
    expect(fmtRevisionPct(12.5)).toBe('12.5%')
    expect(fmtRevisionPct(3.04)).toBe('3%')
  })
})
