import { ingestRateText, rateText } from './eta'

test('rate 為 null 顯示計算中', () => {
  expect(rateText(100, null, '摘要')).toBe('速率 計算中…')
})

test('rate < 0.05 顯示 0.0 不給 ETA', () => {
  expect(rateText(100, 0.04, '摘要')).toBe('速率 0.0 摘要/分')
})

test('remaining<=0 顯示已完成', () => {
  expect(rateText(0, 1.2, '標註')).toBe('速率 1.2 標註/分 · 已完成')
})

test('ETA 分鐘（<90 分）', () => {
  expect(rateText(60, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~30 分')
})

test('ETA 小時（>=90 分）', () => {
  expect(rateText(200, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~1.7 時')
})

test('ingestRateText：rpm null → 計算中', () => {
  expect(ingestRateText(null, null)).toBe('速率 計算中…')
})

test('ingestRateText：cps null → 只顯示速率', () => {
  expect(ingestRateText(3, null)).toBe('速率 3.0 篇/分')
})

test('ingestRateText：兩段', () => {
  expect(ingestRateText(3, 1.5)).toBe('速率 3.0 篇/分 · 1.5 片段/秒')
})
