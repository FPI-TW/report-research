import { NULL_RATE, nextRate, type RateSample } from './rate'

const s = (over: Partial<RateSample> = {}): RateSample => ({
  reports: 0,
  chunks: 0,
  sumDone: 0,
  tagDone: 0,
  ...over,
})

test('首次呼叫建立 base、回 NULL_RATE', () => {
  const r = nextRate(null, NULL_RATE, s({ reports: 100 }), 1000)
  expect(r.rate).toEqual(NULL_RATE)
  expect(r.base).toEqual({ reports: 100, chunks: 0, sum: 0, tag: 0, t: 1000 })
})

test('dt < 8 秒沿用上次速率', () => {
  const base = { reports: 100, chunks: 0, sum: 0, tag: 0, t: 1000 }
  const last = { rpm: 5, cps: 1, spm: 2, tpm: 3 }
  const r = nextRate(base, last, s({ reports: 200 }), 1000 + 5000)
  expect(r.rate).toEqual(last)
})

test('dt >= 8 秒計算每分鐘/每秒速率', () => {
  const base = { reports: 100, chunks: 0, sum: 0, tag: 0, t: 0 }
  // 60 秒後 reports +120 → rpm=120；chunks +600 → cps=10
  const r = nextRate(base, NULL_RATE, s({ reports: 220, chunks: 600, sumDone: 60, tagDone: 30 }), 60000)
  expect(r.rate.rpm).toBeCloseTo(120)
  expect(r.rate.cps).toBeCloseTo(10)
  expect(r.rate.spm).toBeCloseTo(60)
  expect(r.rate.tpm).toBeCloseTo(30)
})

test('sumDone/tagDone 為 null 時對應速率為 null', () => {
  const base = { reports: 0, chunks: 0, sum: 0, tag: 0, t: 0 }
  const r = nextRate(base, NULL_RATE, s({ sumDone: null, tagDone: null }), 60000)
  expect(r.rate.spm).toBeNull()
  expect(r.rate.tpm).toBeNull()
})
