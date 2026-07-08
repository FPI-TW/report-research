import { describe, it, expect } from 'vitest'
import { sortOptions, normalizeSort } from './sortForMode'

describe('sortForMode', () => {
  it('日期選項已移除：search 只留 relevance；browse 無可選排序', () => {
    expect(sortOptions('search').map(o => o.value)).toEqual(['relevance'])
    expect(sortOptions('browse')).toEqual([])
  })
  it('normalizeSort: browse 一律回 date_desc（無可選項、順序固定新→舊）', () => {
    expect(normalizeSort('browse', 'relevance')).toBe('date_desc')
    expect(normalizeSort('browse', 'date_asc')).toBe('date_desc')
    expect(normalizeSort('browse', 'date_desc')).toBe('date_desc')
  })
  it('normalizeSort: search 一律回 relevance（日期值不再合法）', () => {
    expect(normalizeSort('search', 'relevance')).toBe('relevance')
    expect(normalizeSort('search', 'date_desc')).toBe('relevance')
  })
})
