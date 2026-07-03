import { describe, it, expect } from 'vitest'
import { resultsMetaText, emptyState } from './resultsMeta'
import { defaultState } from './searchFilters'

describe('resultsMetaText', () => {
  it('search + 市場', () => {
    expect(resultsMetaText({ ...defaultState(), q: 'AI', market: 'TW' }, 12))
      .toBe('「AI」 · 台股 — 找到 12 篇研報')
  })
  it('search 無市場（不帶市場片段）', () => {
    expect(resultsMetaText({ ...defaultState(), q: 'AI' }, 3)).toBe('「AI」 — 找到 3 篇研報')
  })
  it('browse 有市場篩選', () => {
    expect(resultsMetaText({ ...defaultState(), market: 'US' }, 8)).toBe('美股 — 8 篇')
  })
  it('browse 有進階篩選但無市場→全部研報', () => {
    expect(resultsMetaText({ ...defaultState(), report_type: '個股' }, 5)).toBe('全部研報 — 5 篇')
  })
  it('browse 皆無篩選', () => {
    expect(resultsMetaText(defaultState(), 100)).toBe('全部研報 — 共 100 篇')
  })
})

describe('emptyState', () => {
  it('search：標題含 q、showBrowseAll、showClear 依 hasFilter', () => {
    const c = emptyState({ ...defaultState(), q: 'xyz' }, true)
    expect(c.title).toBe('找不到「xyz」的相關研報')
    expect(c.showBrowseAll).toBe(true); expect(c.showClear).toBe(true)
  })
  it('browse：無 browseAll、showClear 依 hasFilter', () => {
    const c = emptyState(defaultState(), false)
    expect(c.title).toBe('沒有符合條件的研報')
    expect(c.showBrowseAll).toBe(false); expect(c.showClear).toBe(false)
  })
})
