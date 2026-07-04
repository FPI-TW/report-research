import { expect, test } from 'vitest'
import { stagesToSteps } from './thinkingStages'

test('無網路時四格、最後抵達為 active', () => {
  const steps = stagesToSteps(['understanding', 'retrieved'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '閱讀整理', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'active', 'pending', 'pending'])
})

test('出現 searching_web 才插入網路補充格', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'reading', 'searching_web'], true)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '閱讀整理', '網路補充', '生成回答'])
  expect(steps.find(s => s.name === '網路補充')?.state).toBe('active')
})

test('generating 抵達時全部之前為 done、生成回答 active', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'reading', 'generating'], false)
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'done', 'active'])
})
