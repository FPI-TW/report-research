import { describe, it, expect } from 'vitest'
import { buildQuery } from './api'

describe('buildQuery', () => {
  it('略過 undefined/全部，bool→字串化', () => {
    const qs = buildQuery({
      q: 'AI',
      market: '全部',
      relates_stock: true,
      sort: 'relevance',
      limit: 50,
      offset: 0,
      instrument_type: undefined,
    })
    const p = new URLSearchParams(qs)
    expect(p.get('q')).toBe('AI')
    expect(p.has('market')).toBe(false) // 「全部」略過
    expect(p.get('relates_stock')).toBe('true')
    expect(p.get('sort')).toBe('relevance')
    expect(p.get('limit')).toBe('50')
    expect(p.get('offset')).toBe('0')
    expect(p.has('instrument_type')).toBe(false)
  })
})
