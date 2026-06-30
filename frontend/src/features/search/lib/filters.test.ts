import { describe, it, expect, test } from 'vitest'
import { DEFAULT_FILTERS, filtersToSearchParams, searchParamsToFilters } from './filters'
import {
  viewStateToParams,
  paramsToViewState,
  DEFAULT_VIEW_STATE,
} from './filters'

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

test('預設 view/group 不輸出參數', () => {
  expect(viewStateToParams({ view: 'group', group: 'month' })).toEqual([])
})

test('table 檢視輸出 view，group 省略（非 group 檢視）', () => {
  expect(viewStateToParams({ view: 'table', group: 'month' })).toEqual([['view', 'table']])
})

test('group 檢視 + market 分組輸出 group', () => {
  expect(viewStateToParams({ view: 'group', group: 'market' })).toEqual([['group', 'market']])
})

test('table 檢視時 group 不輸出（即使非 month）', () => {
  expect(viewStateToParams({ view: 'table', group: 'market' })).toEqual([['view', 'table']])
})

test('paramsToViewState 還原合法值', () => {
  const sp = new URLSearchParams('view=table&group=market')
  // table 檢視 group 仍解析（記憶使用者偏好），但 viewStateToParams 在 table 時不輸出
  expect(paramsToViewState(sp)).toEqual({ view: 'table', group: 'market' })
})

test('paramsToViewState 非法值回預設', () => {
  const sp = new URLSearchParams('view=bogus&group=bogus')
  expect(paramsToViewState(sp)).toEqual(DEFAULT_VIEW_STATE)
})

test('paramsToViewState 空回預設', () => {
  expect(paramsToViewState(new URLSearchParams())).toEqual(DEFAULT_VIEW_STATE)
})
