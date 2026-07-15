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

test('未 reached evaluating 時不顯示評估補查（既有序列零變化）', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'reading', 'generating'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '閱讀整理', '生成回答'])
})

test('reached 含 evaluating 時插入於檢索研報之前且為 active', () => {
  const steps = stagesToSteps(['understanding', 'evaluating'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '評估補查', '檢索研報', '閱讀整理', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'active', 'pending', 'pending', 'pending'])
})

test('agentic 全序列（含網路補充）排序正確', () => {
  const steps = stagesToSteps(['understanding', 'evaluating', 'retrieved', 'reading', 'searching_web', 'generating'], true)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '評估補查', '檢索研報', '閱讀整理', '網路補充', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'done', 'done', 'done', 'active'])
})
