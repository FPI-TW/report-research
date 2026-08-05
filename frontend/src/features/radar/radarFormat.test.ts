import { describe, expect, it } from 'vitest'
import {
  currencyPrefix,
  daysSinceReport,
  directionVerb,
  epsPeriodLabel,
  fmtDate,
  fmtDateOrNA,
  fmtEps,
  fmtEpsParts,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtPriceOrNA,
  isReportStale,
  NOT_PROVIDED,
  RATING_DISPLAY,
  STALE_REPORT_DAYS,
  STALE_REPORT_LABEL,
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

  // 缺值字樣分兩種、刻意不同義：'—' 給「這一格本來就沒有欄位」（共識卡、分布條），
  // '未提供' 給「這份研報沒有揭露這個數字」。混用會讓讀者把後者讀成系統沒抓到。
  it('券商面板的缺值說「未提供」，不是破折號', () => {
    expect(NOT_PROVIDED).toBe('未提供')
    expect(fmtPriceOrNA(null, 'TWD')).toBe('未提供')
    expect(fmtPriceOrNA(1185, 'TWD')).toBe('NT$1,185')
    // 後端 report_date 為 NULL 時給的是**空字串**不是 null——擋 falsy 而非擋 null。
    expect(fmtDateOrNA('')).toBe('未提供')
    expect(fmtDateOrNA(null)).toBe('未提供')
    expect(fmtDateOrNA('2026-07-11')).toBe('2026/07/11')
  })

  describe('研報過期判定', () => {
    // 以「當地日曆日」建構，不用 ISO 字串：`new Date('2026-08-05T00:00:00Z')` 在
    // UTC 以西的時區會落在 8/4，天數全部差一天——CI（UTC）綠、本機紅的那種故障。
    const now = new Date(2026, 7, 5)

    it('天數由報告日期與今天現算，時區無關', () => {
      expect(daysSinceReport('2026-08-05', now)).toBe(0)
      expect(daysSinceReport('2026-07-06', now)).toBe(30)
      // ISO datetime 也吃得下（後端 report_date 是 date，但 as_of 一類欄位是 datetime）
      expect(daysSinceReport('2026-05-07T12:34:56Z', now)).toBe(90)
    })

    it('日期未知回 null，且一律不算過期——不知道日期不等於過期', () => {
      expect(daysSinceReport('', now)).toBeNull()
      expect(daysSinceReport(null, now)).toBeNull()
      expect(daysSinceReport('不是日期', now)).toBeNull()
      expect(isReportStale('', now)).toBe(false)
      expect(isReportStale(null, now)).toBe(false)
    })

    it('恰好 90 天不算過期，91 天才算', () => {
      expect(STALE_REPORT_DAYS).toBe(90)
      expect(isReportStale('2026-05-07', now)).toBe(false)
      expect(isReportStale('2026-05-06', now)).toBe(true)
    })

    it('標籤由門檻推導，兩者不會各說各話', () => {
      expect(STALE_REPORT_LABEL).toBe('最新報告已超過 90 天')
      expect(STALE_REPORT_LABEL).toContain(String(STALE_REPORT_DAYS))
    })
  })

  describe('EPS 顯示不外洩內部欄位', () => {
    it('epsPeriodLabel 縮成 FY27E，period 為 FY 時不重複附註', () => {
      expect(epsPeriodLabel(2027, 'FY')).toBe('FY27E')
      expect(epsPeriodLabel(2026, '1H')).toBe('FY26E 1H')
      expect(epsPeriodLabel(null, '1H')).toBe('1H')
      expect(epsPeriodLabel(2026, null)).toBe('FY26E')
      expect(epsPeriodLabel(null, null)).toBe('')
    })

    it('per_share 收成「／股」後綴，unit 本身不進畫面', () => {
      expect(fmtEpsParts(5, 'USD', 2027, 'FY', 'per_share')).toEqual({
        value: 'US$5／股',
        meta: ' · FY27E',
      })
      expect(fmtEps(5, 'USD', 2027, 'FY', 'per_share')).toBe('US$5／股 · FY27E')
    })

    it('未收錄的 unit 一律不外洩（舊寫法會把資料庫值原樣印出來）', () => {
      expect(fmtEps(3, 'TWD', 2026, '1H', 'per_adr')).toBe('NT$3 · FY26E 1H')
      expect(fmtEps(3, 'TWD', 2026, '1H', 'per_adr')).not.toContain('per_adr')
    })

    it('無值時走「未提供」且 meta 為空字串（不是 " · "）', () => {
      expect(fmtEpsParts(null, 'TWD', 2026, 'FY', 'per_share'))
        .toEqual({ value: '未提供', meta: '' })
      expect(fmtEpsParts(12.34, 'TWD')).toEqual({ value: 'NT$12.34', meta: '' })
    })

    // fmtEps 由 fmtEpsParts 推導，兩者不可能分家；這條守的是「別再拆回兩份平行實作」。
    it.each([
      ['完整', [5, 'USD', 2027, 'FY', 'per_share']],
      ['缺期間', [12.34, 'TWD', 2026, null, 'per_share']],
      ['只有值與幣別', [12.34, 'TWD', null, null, null]],
      ['無值', [null, 'TWD', 2026, 'FY', 'per_share']],
    ] as [string, Parameters<typeof fmtEps>][])('%s：value + meta 逐字等於 fmtEps', (_label, args) => {
      const parts = fmtEpsParts(...args)
      expect(parts.value + parts.meta).toBe(fmtEps(...args))
    })
  })
})
