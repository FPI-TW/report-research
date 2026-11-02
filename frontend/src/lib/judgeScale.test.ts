import { expect, test } from 'vitest'
import { isNewScale, newScaleText } from './judgeScale'

// 「新量尺」的判準不綁供應商：現行 judge 有值、窗期內還有其他判定尺的列就算。換 judge 時不必改這裡。
test.each([
  ['gpt-6-luna', 6, true],
  ['some-future-judge', 1, true],
  ['gpt-6-luna', 0, false],
  ['gpt-6-luna', undefined, false],
  ['', 6, false],
  [undefined, 6, false],
] as const)('isNewScale(judge=%s, other=%s) → %s', (judge_model, other_judge_checked, want) => {
  expect(isNewScale({ judge_model, judge_since: '2026-11-02', other_judge_checked })).toBe(want)
})

test('isNewScale：沒有量尺資料 → false', () => {
  expect(isNewScale(null)).toBe(false)
  expect(isNewScale(undefined)).toBe(false)
})

test('newScaleText 印 judge 模型；新尺尚無查核時不編日期', () => {
  const base = { judge_model: 'gpt-6-luna', other_judge_checked: 3 }
  expect(newScaleText({ ...base, judge_since: '2026-11-02' })).toBe('新量尺（自 2026-11-02 起，gpt-6-luna）')
  expect(newScaleText({ ...base, judge_since: null })).toBe('新量尺（尚無查核，gpt-6-luna）')
})
