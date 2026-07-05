import { expect, test } from 'vitest'
import { computeRates, rateText, ingestRateText, fmtInt } from './rate'

const B = { reports: 0, chunks: 0, sum: 0, tag: 0 }

test('rateText：rate null → 計算中', () => {
  expect(rateText(10, null, '摘要')).toBe('速率 計算中…')
})
test('rateText：rate<0.05 → 只顯速率不給 ETA', () => {
  expect(rateText(10, 0.04, '摘要')).toBe('速率 0.0 摘要/分')
})
test('rateText：remaining<=0 → 已完成', () => {
  expect(rateText(0, 2, '標註')).toBe('速率 2.0 標註/分 · 已完成')
})
test('rateText：mins<90 用「分」', () => {
  expect(rateText(20, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~10 分')
})
test('rateText：mins>=90 用「時」', () => {
  expect(rateText(300, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~2.5 時')
})
test('ingestRateText：rpm null → 計算中', () => {
  expect(ingestRateText(null, 1)).toBe('速率 計算中…')
})
test('ingestRateText：cps null → 只速率', () => {
  expect(ingestRateText(3, null)).toBe('速率 3.0 篇/分')
})
test('ingestRateText：雙段', () => {
  expect(ingestRateText(3, 1.5)).toBe('速率 3.0 篇/分 · 1.5 片段/秒')
})
test('computeRates：暖機<8s → 全 null', () => {
  expect(computeRates({ reports: 10, chunks: 100, sumDone: 5, tagDone: 3 }, B, 4)).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})
test('computeRates：正常算速率', () => {
  const r = computeRates({ reports: 60, chunks: 600, sumDone: 30, tagDone: 20 }, B, 60)
  expect(r.rpm).toBeCloseTo(60)
  expect(r.cps).toBeCloseTo(10)
  expect(r.spm).toBeCloseTo(30)
  expect(r.tpm).toBeCloseTo(20)
})
test('computeRates：sumDone/tagDone null → 對應 null', () => {
  const r = computeRates({ reports: 60, chunks: 600, sumDone: null, tagDone: null }, B, 60)
  expect(r.spm).toBeNull()
  expect(r.tpm).toBeNull()
})
test('fmtInt：千分位', () => {
  expect(fmtInt(1234567)).toBe('1,234,567')
})
