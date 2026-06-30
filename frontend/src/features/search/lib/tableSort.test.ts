import { test, expect } from 'vitest'
import { sortedRows, nextTableSort } from './tableSort'
import type { Row } from './normalize'

const mk = (over: Partial<Row>): Row => ({
  report_id: over.report_id ?? 'x',
  file_name: over.file_name ?? '',
  ...over,
}) as Row

test('key=null 回原序、不變更輸入', () => {
  const rows = [mk({ file_name: 'b' }), mk({ file_name: 'a' })]
  const out = sortedRows(rows, { key: null, dir: 'asc' })
  expect(out.map((r) => r.file_name)).toEqual(['b', 'a'])
  expect(out).not.toBe(rows)
})

test('依 name 升冪（小寫比較）', () => {
  const rows = [mk({ file_name: 'Banana' }), mk({ file_name: 'apple' })]
  expect(sortedRows(rows, { key: 'name', dir: 'asc' }).map((r) => r.file_name)).toEqual([
    'apple',
    'Banana',
  ])
})

test('依 date 降冪', () => {
  const rows = [mk({ report_date: '2026-01-01' }), mk({ report_date: '2026-06-01' })]
  expect(sortedRows(rows, { key: 'date', dir: 'desc' }).map((r) => r.report_date)).toEqual([
    '2026-06-01',
    '2026-01-01',
  ])
})

test('依 score（bestScore）數值升冪、null 視為 0', () => {
  const rows = [mk({ bestScore: 0.9 }), mk({ bestScore: undefined }), mk({ bestScore: 0.5 })]
  expect(sortedRows(rows, { key: 'score', dir: 'asc' }).map((r) => r.bestScore ?? 0)).toEqual([
    0, 0.5, 0.9,
  ])
})

test('依 match（matchCount）降冪', () => {
  const rows = [mk({ matchCount: 1 }), mk({ matchCount: 5 })]
  expect(sortedRows(rows, { key: 'match', dir: 'desc' }).map((r) => r.matchCount)).toEqual([5, 1])
})

test('nextTableSort：同 key 翻 dir', () => {
  expect(nextTableSort({ key: 'name', dir: 'asc' }, 'name')).toEqual({ key: 'name', dir: 'desc' })
})

test('nextTableSort：異 key 設 asc', () => {
  expect(nextTableSort({ key: 'name', dir: 'desc' }, 'date')).toEqual({ key: 'date', dir: 'asc' })
})
