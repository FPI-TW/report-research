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

// ── 邊界值（operator 為 <8 / <0.05 / <90，故等於邊界時走「另一側」）──
test('rateText：rate===0.05 邊界 → 非 <0.05、給 ETA', () => {
  // 0.05 不 < 0.05 → 進 ETA；remaining=1 → mins=20 → ~20 分（rate.toFixed(1)="0.1"）
  expect(rateText(1, 0.05, '標註')).toBe('速率 0.1 標註/分 · 預估剩餘 ~20 分')
})
test('rateText：mins===90 邊界 → 非 <90、用「時」', () => {
  // remaining=180, rate=2 → mins=90 → 90/60=1.5 → ~1.5 時
  expect(rateText(180, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~1.5 時')
})
test('computeRates：elapsedSec===8 邊界 → 非暖機、開始計算', () => {
  // 8 不 < 8 → 計算：reports 8 / 8s * 60 = 60
  const r = computeRates({ reports: 8, chunks: 80, sumDone: 8, tagDone: 8 }, B, 8)
  expect(r.rpm).toBeCloseTo(60)
  expect(r.cps).toBeCloseTo(10)
})
