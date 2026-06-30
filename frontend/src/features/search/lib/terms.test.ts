import { buildTerms } from './terms'

test('buildTerms: splits on whitespace, dedupes (first-seen order)', () => {
  expect(buildTerms('AI 伺服器 AI')).toEqual(['AI', '伺服器'])
})
test('buildTerms: empty string returns empty array', () => {
  expect(buildTerms('')).toEqual([])
})
test('buildTerms: whitespace-only string returns empty array', () => {
  expect(buildTerms('   ')).toEqual([])
})
test('buildTerms: single term returns single-element array', () => {
  expect(buildTerms('台積電')).toEqual(['台積電'])
})
test('buildTerms: multiple distinct terms preserved in order', () => {
  expect(buildTerms('A B C')).toEqual(['A', 'B', 'C'])
})
