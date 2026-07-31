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

test('reached 含 evaluating 時插入於檢索研報之後且為 active', () => {
  // agentic 的評估補查必然發生在第一輪檢索之後，故排在檢索研報之後
  const steps = stagesToSteps(['understanding', 'retrieved', 'evaluating'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '評估補查', '閱讀整理', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'active', 'pending', 'pending'])
})

test('agentic 全序列（含網路補充）排序正確', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'evaluating', 'reading', 'searching_web', 'generating'], true)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '評估補查', '閱讀整理', '網路補充', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'done', 'done', 'done', 'active'])
})

test('retrieved 補 count 那筆落在 evaluating 之後也不會讓進度倒退', () => {
  // 後端事件序：retrieved（檢索前推進）→ evaluating（agentic）→ retrieved（補 count）。
  // 若取「最後一筆」而非「最遠一步」，評估補查會從 active 退回 pending。
  const steps = stagesToSteps(['understanding', 'retrieved', 'evaluating', 'retrieved'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '評估補查', '閱讀整理', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'active', 'pending', 'pending'])
})
