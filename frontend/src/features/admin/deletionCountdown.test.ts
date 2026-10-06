import { expect, test } from 'vitest'
import { deletionCountdown } from './deletionCountdown'

const NOW = Date.parse('2026-10-06T00:00:00Z')

test('剩餘時間以小時＋分鐘顯示，不足一分鐘進位成一分鐘', () => {
  expect(deletionCountdown('2026-10-07T00:00:00Z', NOW)).toBe('24 小時 0 分後刪除')
  expect(deletionCountdown('2026-10-06T01:30:00Z', NOW)).toBe('1 小時 30 分後刪除')
  expect(deletionCountdown('2026-10-06T00:00:20Z', NOW)).toBe('1 分後刪除')
})

test('已到期或時間無法解析', () => {
  expect(deletionCountdown('2026-10-05T23:59:00Z', NOW)).toBe('即將刪除')
  expect(deletionCountdown('not-a-date', NOW)).toBe('已排程刪除')
})
