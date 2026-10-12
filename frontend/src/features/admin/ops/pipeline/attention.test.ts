import { expect, test } from 'vitest'
import { attentionLevel, fmtMinute, pipelineAttention } from './attention'
import { progressFixture } from './pipelineTestKit'

test('一切正常 → 沒有項目、結論 ok', () => {
  const items = pipelineAttention(progressFixture())
  expect(items).toEqual([])
  expect(attentionLevel(items)).toBe('ok')
})

test('近 24 小時有排程失敗 → bad，時間只到分', () => {
  const p = progressFixture({
    unit_failures: { count_24h: 2, count_7d: 3, latest: '2026-10-07T12:03:44+08:00', recent: [] },
  })
  const items = pipelineAttention(p)
  expect(items).toEqual([
    { level: 'bad', text: '近 24 小時有 2 次排程失敗，最近一次 2026-10-07 12:03', target: 'schedule' },
  ])
  expect(attentionLevel(items)).toBe('bad')
})

test('近 7 日有、近 24 小時無 → 不算（時間窗計數，否則燈號上線第一天就永遠亮著）', () => {
  const p = progressFixture({ unit_failures: { count_24h: 0, count_7d: 5, latest: '2026-10-03T21:00:04+08:00', recent: [] } })
  expect(pipelineAttention(p)).toEqual([])
})

test('fail-open > 0 → warn；低於門檻與抽取需複核常態非零，不進結論', () => {
  const base = progressFixture()
  const p = progressFixture({
    evaluation: { min_score: 0.9, qa: { ...base.evaluation!.qa!, degraded: 4, below_min: 30 } },
    extraction: { ...base.extraction!, needs_review: 999, pages_failed: 50 },
  })
  const items = pipelineAttention(p)
  expect(items.map(i => [i.level, i.target])).toEqual([['warn', 'quality']])
  expect(items[0].text).toContain('4 筆 fail-open')
  expect(attentionLevel(items)).toBe('warn')
})

test('bad 與 warn 同時存在 → 結論是 bad', () => {
  const base = progressFixture()
  const p = progressFixture({
    unit_failures: { count_24h: 1, count_7d: 1, latest: null, recent: [] },
    evaluation: { min_score: 0.9, qa: { ...base.evaluation!.qa!, degraded: 1 } },
  })
  const items = pipelineAttention(p)
  expect(items.map(i => i.level)).toEqual(['bad', 'warn'])
  expect(items[0].text).toBe('近 24 小時有 1 次排程失敗')
  expect(attentionLevel(items)).toBe('bad')
})

test('舊後端缺 unit_failures／evaluation → 不判、不拋錯', () => {
  const p = progressFixture({ unit_failures: undefined, evaluation: undefined })
  expect(pipelineAttention(p)).toEqual([])
  expect(pipelineAttention(progressFixture({ evaluation: { min_score: 0.9, qa: null } }))).toEqual([])
})

test('fmtMinute：ISO 帶時區、空白分隔都只取到分；認不得的原樣；空值 —', () => {
  expect(fmtMinute('2026-10-07T12:03:44+08:00')).toBe('2026-10-07 12:03')
  expect(fmtMinute('2026-10-07 12:41:17')).toBe('2026-10-07 12:41')
  expect(fmtMinute('昨天')).toBe('昨天')
  expect(fmtMinute(null)).toBe('—')
  expect(fmtMinute(undefined)).toBe('—')
})
