/**
 * 每日簡報的日期工具。簡報日期是後端的 ISO 日期字串（YYYY-MM-DD，台北日期），
 * 這裡一律以「日曆日」處理，不經過 Date 的時區換算（`new Date('2026-10-05')` 會被當成 UTC 午夜）。
 */

export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'] as const
export const WEEKDAY_HEADERS = WEEKDAYS

export interface DayParts {
  y: number
  m: number
  d: number
}

export function partsOf(iso: string): DayParts {
  const [y, m, d] = iso.split('-').map(Number)
  return { y, m, d }
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

export function isoOf(y: number, m: number, d: number): string {
  return `${y}-${pad(m)}-${pad(d)}`
}

/** 本機的今天（使用者在台灣，與簡報日期同一個時區）。 */
export function todayIso(now: Date = new Date()): string {
  return isoOf(now.getFullYear(), now.getMonth() + 1, now.getDate())
}

function weekdayOf({ y, m, d }: DayParts): number {
  return new Date(y, m - 1, d).getDay()
}

/** 「10 月 28 日」 */
export function dayTitle(iso: string): string {
  const { m, d } = partsOf(iso)
  return `${m} 月 ${d} 日`
}

/** 「2026 年 星期三」 */
export function yearWeekday(iso: string): string {
  const p = partsOf(iso)
  return `${p.y} 年 星期${WEEKDAYS[weekdayOf(p)]}`
}

/** 來源研報的日期：「10-27」（同一份簡報的研報多半同年，省掉年份）。 */
export function monthDay(iso: string | null | undefined): string {
  if (!iso || !ISO_DATE.test(iso)) return ''
  return iso.slice(5)
}

export interface MonthRef {
  y: number
  m: number
}

export function monthOf(iso: string): MonthRef {
  const { y, m } = partsOf(iso)
  return { y, m }
}

export function monthIndex({ y, m }: MonthRef): number {
  return y * 12 + (m - 1)
}

export function addMonths({ y, m }: MonthRef, delta: number): MonthRef {
  const idx = y * 12 + (m - 1) + delta
  return { y: Math.floor(idx / 12), m: (idx % 12) + 1 }
}

export interface CalendarCell {
  iso: string
  day: number
}

/** 一個月的日期格：前面補空格對齊星期（週日起算）。 */
export function monthCells({ y, m }: MonthRef): { blanks: number; days: CalendarCell[] } {
  const blanks = new Date(y, m - 1, 1).getDay()
  const count = new Date(y, m, 0).getDate()
  const days: CalendarCell[] = []
  for (let d = 1; d <= count; d++) days.push({ iso: isoOf(y, m, d), day: d })
  return { blanks, days }
}

/** 合併兩份日期清單：去重、新→舊。 */
export function mergeDates(...lists: readonly (readonly string[] | undefined)[]): string[] {
  const set = new Set<string>()
  for (const list of lists) for (const d of list ?? []) if (ISO_DATE.test(d)) set.add(d)
  return [...set].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0))
}

/** 在新→舊的日期清單裡找前一份（較舊）與後一份（較新）。目前日期不在清單裡時依大小插入位置判斷。 */
export function neighbours(dates: readonly string[], current: string): { older: string | null; newer: string | null } {
  let newer: string | null = null
  let older: string | null = null
  for (const d of dates) {
    if (d > current) newer = d
    else if (d < current) {
      older = d
      break
    }
  }
  return { older, newer }
}
