import { test, expect } from 'vitest'
import { highlightSegments } from './highlight'

test('無 terms 回單一未標記 segment', () => {
  expect(highlightSegments('hello', [])).toEqual([{ text: 'hello', mark: false }])
})

test('單一 term 切出標記段', () => {
  expect(highlightSegments('AI server', ['AI'])).toEqual([
    { text: 'AI', mark: true },
    { text: ' server', mark: false },
  ])
})

test('大小寫不敏感', () => {
  const segs = highlightSegments('ai SERVER', ['ai', 'server'])
  expect(segs.filter((s) => s.mark).map((s) => s.text)).toEqual(['ai', 'SERVER'])
})

test('CJK 命中', () => {
  const segs = highlightSegments('台積電法說', ['台積'])
  expect(segs.some((s) => s.mark && s.text === '台積')).toBe(true)
})

test('regex 特殊字元被跳脫（不當 regex 解讀）', () => {
  const segs = highlightSegments('a.b a+b', ['a.b'])
  // 只命中字面 'a.b'，不把 . 當任意字元
  expect(segs.filter((s) => s.mark).map((s) => s.text)).toEqual(['a.b'])
})

test('無命中時回單一未標記 segment', () => {
  expect(highlightSegments('hello', ['xyz'])).toEqual([{ text: 'hello', mark: false }])
})

test('不產生空字串 segment', () => {
  const segs = highlightSegments('AIAI', ['AI'])
  expect(segs.every((s) => s.text.length > 0)).toBe(true)
})
