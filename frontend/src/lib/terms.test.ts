import { describe, it, expect } from 'vitest'
import { queryTerms } from './terms'

describe('queryTerms', () => {
  it('CJK：>1 字切 2-gram、單字保留', () => {
    expect(queryTerms('台積電')).toEqual(['台積', '積電'])
    expect(queryTerms('台')).toEqual(['台'])
  })
  it('非 CJK：需 ≥2 字，單字元丟棄', () => {
    expect(queryTerms('AI')).toEqual(['AI'])
    expect(queryTerms('a')).toEqual([])
  })
  it('多 token 去重、依長度降序', () => {
    expect(queryTerms('台積電 AI')).toEqual(['台積', '積電', 'AI'])
  })
})
