import { expect, test } from 'vitest'
import { dailyAxis, dailyCalls, dayIn } from './usageSeries'

test('dayIn：依回應帶的時區切日，不看瀏覽器的時區', () => {
  expect(dayIn('2026-10-05T15:59:59Z', 'Asia/Taipei')).toBe('2026-10-05')
  expect(dayIn('2026-10-05T16:00:00Z', 'Asia/Taipei')).toBe('2026-10-06')
  expect(dayIn('2026-10-05T16:00:00Z', 'UTC')).toBe('2026-10-05')
  expect(dayIn('不是時間', 'Asia/Taipei')).toBeNull()
  expect(dayIn('2026-10-05T16:00:00Z', 'Not/AZone')).toBeNull()
})

test('dailyCalls：區間內一天一根，後端沒列的日子補 0', () => {
  const bars = dailyCalls([{ day: '2026-10-02', calls: 4 }, { day: '2026-10-04', calls: 9 }], '2026-10-01', '2026-10-04')
  expect(bars).toEqual([
    { value: 0, label: '2026-10-01' }, { value: 4, label: '2026-10-02' },
    { value: 0, label: '2026-10-03' }, { value: 9, label: '2026-10-04' },
  ])
})

test('dailyCalls：跨月、跨年照日曆走', () => {
  expect(dailyCalls([], '2026-12-30', '2027-01-02').map(b => b.label))
    .toEqual(['2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02'])
  expect(dailyCalls([], '2028-02-28', '2028-03-01')).toHaveLength(3)
})

test('dailyCalls：knownFrom 之前沒讀到的日子留空（null），就算那天有別的來源的數字也不畫', () => {
  const bars = dailyCalls([{ day: '2026-10-01', calls: 2 }, { day: '2026-10-03', calls: 5 }], '2026-10-01', '2026-10-04', '2026-10-03')
  expect(bars.map(b => b.value)).toEqual([null, null, 5, 0])
})

test('dailyCalls：區間不合法時回空陣列', () => {
  expect(dailyCalls([], '2026-10-05', '2026-10-01')).toEqual([])
  expect(dailyCalls([], '壞掉', '2026-10-01')).toEqual([])
})

test('dailyAxis：起、中、迄；不到三天就全列', () => {
  expect(dailyAxis(dailyCalls([], '2026-09-06', '2026-10-06'))).toEqual(['09/06', '09/21', '10/06'])
  expect(dailyAxis(dailyCalls([], '2026-10-05', '2026-10-06'))).toEqual(['10/05', '10/06'])
  expect(dailyAxis([])).toEqual([])
})
