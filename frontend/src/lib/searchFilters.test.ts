import { describe, it, expect } from 'vitest'
import {
  defaultState, modeOf, parseParams, buildParams, toApiParams,
  activeAdvancedCount, hasAnyFilter, clearFilters, browseAll,
} from './searchFilters'

describe('searchFilters', () => {
  it('modeOf: q 有值→search，空白→browse', () => {
    expect(modeOf({ q: 'AI' })).toBe('search')
    expect(modeOf({ q: '   ' })).toBe('browse')
  })
  it('parseParams reads url', () => {
    const s = parseParams('?q=AI&market=TW&instrument_type=股票&relates_stock=1&sort=date_asc&view=table')
    expect(s).toMatchObject({ q: 'AI', market: 'TW', instrument_type: '股票',
      relates_stock: true, sort: 'date_asc', view: 'table' })
  })
  it('parseParams: 無 sort 時 search 預設 relevance、browse 預設 date_desc', () => {
    expect(parseParams('?q=AI').sort).toBe('relevance')
    expect(parseParams('').sort).toBe('date_desc')
  })
  it('buildParams omits defaults (ALL/空/false/預設 sort/cards)', () => {
    expect(buildParams(defaultState()).toString()).toBe('')
    const p = buildParams({ ...defaultState(), q: 'AI', market: 'TW', view: 'table', sort: 'relevance' as const })
    expect(p.get('q')).toBe('AI'); expect(p.get('market')).toBe('TW')
    expect(p.get('view')).toBe('table'); expect(p.has('sort')).toBe(false)
  })
  it('toApiParams: search 帶 q；relates 只在 true 時帶；market ALL 不帶；含 limit/offset', () => {
    const p = toApiParams({ ...defaultState(), q: 'AI', sort: 'relevance', relates_stock: true },
      { limit: 50, offset: 0 })
    expect(p.get('q')).toBe('AI'); expect(p.get('relates_stock')).toBe('true')
    expect(p.has('market')).toBe(false); expect(p.get('limit')).toBe('50')
  })
  it('toApiParams: browse 不帶 q', () => {
    expect(toApiParams(defaultState(), { limit: 50, offset: 0 }).has('q')).toBe(false)
  })
  it('activeAdvancedCount + hasAnyFilter', () => {
    const s = { ...defaultState(), report_type: '個股報告', relates_futures: true }
    expect(activeAdvancedCount(s)).toBe(2)
    expect(hasAnyFilter(s)).toBe(true)
    expect(hasAnyFilter({ ...defaultState(), market: 'US' })).toBe(true)
    expect(hasAnyFilter(defaultState())).toBe(false)
  })
  it('clearFilters keeps q, browseAll clears q', () => {
    const s = { ...defaultState(), q: 'AI', market: 'TW', relates_stock: true, sort: 'relevance' as const }
    expect(clearFilters(s)).toMatchObject({ q: 'AI', market: 'ALL', relates_stock: false })
    expect(browseAll(s)).toMatchObject({ q: '', market: 'ALL', sort: 'date_desc' })
  })
})
