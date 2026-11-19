import { act, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { exportFilename } from '../features/admin/flags/flagText'
import { businessToday, sinceForDays } from '../features/admin/analytics/analyticsFormat'
import { brandingResponseSchema, fromResponse, setBranding } from '../lib/branding'
import {
  DEFAULT_TIME_ZONE,
  LocaleBoundary,
  businessDate,
  formatDate,
  getTimeZone,
  setLocale,
  setTimeZone,
  t,
  timeZoneLabel,
} from '.'

afterEach(() => {
  setBranding(null)
})

describe('業務時區：預設', () => {
  it('沒抓到 profile 時是 Asia/Taipei、地名「台北」／Taipei（與改動前的畫面相同）', () => {
    expect(getTimeZone()).toBe(DEFAULT_TIME_ZONE)
    expect(DEFAULT_TIME_ZONE).toBe('Asia/Taipei')
    expect(timeZoneLabel()).toBe('台北')
    expect(t('admin.quota.adminQuotaPage.zoneTime2', { zone: timeZoneLabel(), day: '2026-10-07' })).toBe('2026-10-07（台北時間）')
    act(() => setLocale('en'))
    expect(timeZoneLabel()).toBe('Taipei')
  })

  it('預設時區在 UTC 16:00 換日', () => {
    expect(businessDate(new Date('2026-10-06T15:59:59Z'))).toBe('2026-10-06')
    expect(businessDate(new Date('2026-10-06T16:00:00Z'))).toBe('2026-10-07')
  })
})

describe('業務時區：profile 宣告其他時區', () => {
  it('非 +8 的時區：今天、匯出檔名與日期格式化都依它切日', () => {
    setTimeZone({ name: 'America/New_York', label: '紐約', labelEn: 'New York' })
    expect(getTimeZone()).toBe('America/New_York')
    // 2026-10-07 03:30 UTC＝紐約 10/06 23:30（EDT，UTC-4）；台北早已是 10/07。
    const at = new Date('2026-10-07T03:30:00Z')
    expect(businessDate(at)).toBe('2026-10-06')
    expect(businessToday(at)).toBe('2026-10-06')
    expect(sinceForDays(7, businessToday(at))).toBe('2026-09-30')
    expect(exportFilename('prod', at)).toBe('report-mark-flags-prod-20261006.json')
    expect(formatDate(at, { hour: '2-digit', minute: '2-digit', hour12: false })).toBe('23:30')
    expect(timeZoneLabel()).toBe('紐約')
    act(() => setLocale('en'))
    expect(timeZoneLabel()).toBe('New York')
  })

  it('profile 沒給地名時用 IANA 名稱；英文沒給沿用中文', () => {
    setTimeZone({ name: 'Europe/London' })
    expect(timeZoneLabel()).toBe('Europe/London')
    setTimeZone({ name: 'Asia/Hong_Kong', label: '香港' })
    act(() => setLocale('en'))
    expect(timeZoneLabel()).toBe('香港')
  })

  it('瀏覽器認不得的名稱退回預設（不拋、不讓整頁的日期壞掉）', () => {
    setTimeZone({ name: 'Mars/Olympus', label: '火星' })
    expect(getTimeZone()).toBe(DEFAULT_TIME_ZONE)
    expect(timeZoneLabel()).toBe('台北')
  })
})

describe('經 /api/branding 下發', () => {
  it('branding 的 timezone 寫進 i18n；setBranding(null) 還原成預設', () => {
    const raw = brandingResponseSchema.parse({ timezone: { name: 'Asia/Hong_Kong', label: '香港', label_en: 'Hong Kong' } })
    setBranding(fromResponse(raw))
    expect(getTimeZone()).toBe('Asia/Hong_Kong')
    expect(timeZoneLabel()).toBe('香港')
    setBranding(null)
    expect(getTimeZone()).toBe(DEFAULT_TIME_ZONE)
  })

  it('舊版後端沒有 timezone 欄：留在預設', () => {
    setBranding(fromResponse(brandingResponseSchema.parse({ product_name: '艾克米研究平台' })))
    expect(getTimeZone()).toBe(DEFAULT_TIME_ZONE)
  })
})

let mounts = 0

/** 每次掛載拿一個新號碼：號碼變了＝子樹被重掛。 */
function MountCounter() {
  const [id] = useState(() => ++mounts)
  return <span data-testid="stamp">{String(id)}</span>
}

describe('LocaleBoundary', () => {
  it('時區變了才重掛；預設 profile 的值與抓到之前相同，不多重掛一次', () => {
    render(<LocaleBoundary><MountCounter /></LocaleBoundary>)
    const first = screen.getByTestId('stamp').textContent
    act(() => setTimeZone({ name: 'Asia/Taipei', label: '台北', labelEn: 'Taipei' }))
    expect(screen.getByTestId('stamp').textContent).toBe(first)
    act(() => setTimeZone({ name: 'Europe/London', label: '倫敦' }))
    expect(screen.getByTestId('stamp').textContent).not.toBe(first)
  })
})
