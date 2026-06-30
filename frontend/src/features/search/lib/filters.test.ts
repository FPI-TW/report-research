import { describe, it, expect } from 'vitest'
import { DEFAULT_FILTERS, filtersToSearchParams, searchParamsToFilters } from './filters'

describe('filters', () => {
  const allow = { markets: ['TW', 'US'], instruments: ['個股'], types: ['法說會'] }

  it('filters round-trip 略過預設值', () => {
    const sp = filtersToSearchParams({
      ...DEFAULT_FILTERS,
      q: 'AI',
      market: 'TW',
      stock: true,
      sort: 'relevance',
    })
    expect(sp.get('q')).toBe('AI')
    expect(sp.get('market')).toBe('TW')
    expect(sp.get('stock')).toBe('1')
    expect(sp.has('instrument')).toBe(false)
  })

  it('searchParamsToFilters 白名單擋非法市場', () => {
    const f = searchParamsToFilters(new URLSearchParams('market=ZZ&q=AI'), allow)
    expect(f.market).toBe('全部') // 非白名單 → 全部
    expect(f.q).toBe('AI')
    expect(f.sort).toBe('relevance') // 有 q → SEARCH 預設
  })

  it('無 q 時 sort 預設 date_desc', () => {
    const f = searchParamsToFilters(new URLSearchParams(''), allow)
    expect(f.sort).toBe('date_desc')
  })
})
