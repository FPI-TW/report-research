import { test, expect } from 'vitest'
import { buildTerms } from './terms'

test('英數整詞（length>=2）收錄、去重', () => {
  expect(buildTerms('AI server AI')).toEqual(['server', 'AI'])  // 長→短
})

test('單一英數字元（length<2）不收', () => {
  expect(buildTerms('a server')).toEqual(['server'])
})

test('CJK 單字 token 收單字', () => {
  expect(buildTerms('台')).toEqual(['台'])
})

test('CJK 多字 token 收所有相鄰 2-gram', () => {
  // '台積電' -> '台積','積電'（length 2，依長度排序穩定，原序保留）
  expect(buildTerms('台積電')).toEqual(['台積', '積電'])
})

test('混合 CJK 與英數', () => {
  const t = buildTerms('AI 散熱')
  expect(t).toContain('AI')
  expect(t).toContain('散熱')
})

test('空字串回空陣列', () => {
  expect(buildTerms('')).toEqual([])
  expect(buildTerms('   ')).toEqual([])
})

test('依長度由長到短排序（高亮最長優先）', () => {
  const t = buildTerms('半導體 AI')
  // '半導','導體'(2) 與 'AI'(2) 皆 length 2；長度相同維持穩定
  expect(t.every((x) => x.length === 2)).toBe(true)
})
