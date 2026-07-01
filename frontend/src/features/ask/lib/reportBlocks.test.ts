import { expect, test } from 'vitest'
import { parseReportSegments } from './reportBlocks'

test('純文字→單一 md 段', () => {
  expect(parseReportSegments('## 標題\n內文')).toEqual([{ kind: 'md', text: '## 標題\n內文' }])
})

test('kpi 區塊→kpi 段', () => {
  const md = '前\n```kpi\n{"items":[{"label":"營收","value":"100","dir":"up"}]}\n```\n後'
  const segs = parseReportSegments(md)
  expect(segs[0]).toEqual({ kind: 'md', text: '前' })
  expect(segs[1].kind).toBe('kpi')
  expect(segs[2]).toEqual({ kind: 'md', text: '後' })
})

test('chart 區塊→chart 段', () => {
  const md = '```chart\n{"type":"bar","x":["a"],"series":[{"name":"s","values":[1]}]}\n```'
  const segs = parseReportSegments(md)
  expect(segs[0].kind).toBe('chart')
})

test('壞 kpi JSON→降級為 md 文字段（不拋）', () => {
  const md = '```kpi\n{壞的\n```'
  const segs = parseReportSegments(md)
  expect(segs).toHaveLength(1)
  expect(segs[0].kind).toBe('md')
  expect((segs[0] as { text: string }).text).toContain('```kpi')
})

test('未知 chart type→降級 md', () => {
  const md = '```chart\n{"type":"foo","series":[]}\n```'
  expect(parseReportSegments(md)[0].kind).toBe('md')
})

test('空輸入→[]', () => {
  expect(parseReportSegments('')).toEqual([])
})
