import { describe, it, expect } from 'vitest'
import { sortOptions, normalizeSort } from './sortForMode'

describe('sortForMode', () => {
  it('search 有 3 選項含 relevance；browse 只有 2（無 relevance）', () => {
    expect(sortOptions('search').map(o => o.value)).toEqual(['relevance', 'date_desc', 'date_asc'])
    expect(sortOptions('browse').map(o => o.value)).toEqual(['date_desc', 'date_asc'])
  })
  it('normalizeSort: browse 下 relevance 非法→回退 date_desc', () => {
    expect(normalizeSort('browse', 'relevance')).toBe('date_desc')
  })
  it('normalizeSort: 合法值原樣', () => {
    expect(normalizeSort('search', 'relevance')).toBe('relevance')
    expect(normalizeSort('browse', 'date_asc')).toBe('date_asc')
  })
})
