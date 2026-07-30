import { describe, it, expect } from 'vitest'
import { reportListItemSchema, reportResultSchema, searchResponseSchema, reportFullSchema } from './schemas'

const baseItem = {
  report_id: 'r1', file_hash: 'a'.repeat(64), file_name: 'a.pdf', market: 'TW', source: '元大', summary: null,
  report_date: '2026-06-25', report_type: null, instrument_types: ['股票'],
  relates_stock: true, relates_futures: false, stock_targets: ['2330'], futures_targets: null,
}

describe('schemas', () => {
  it('parses a browse list item', () => {
    expect(reportListItemSchema.parse(baseItem).report_id).toBe('r1')
  })
  // file_hash 是閱讀頁 /report/:hash 的鍵，缺了會靜默壞掉整個檢索→閱讀動線
  it('requires file_hash on a list item', () => {
    const { file_hash: _omitted, ...withoutHash } = baseItem
    expect(() => reportListItemSchema.parse(withoutHash)).toThrow()
  })
  it('parses a search result (superset with passages)', () => {
    const r = reportResultSchema.parse({
      ...baseItem, rank: 1, best_score: 0.83, match_count: 4,
      passages: [{ score: 0.9, chunk_index: 2, content: '片段' }],
    })
    expect(r.best_score).toBe(0.83)
    expect(r.passages[0].content).toBe('片段')
  })
  it('parses a search response envelope', () => {
    const s = searchResponseSchema.parse({
      query: 'AI', market: null, total: 1,
      results: [{ ...baseItem, rank: 1, best_score: 0.5, match_count: 1,
        passages: [{ score: 0.5, chunk_index: 0, content: 'x' }] }],
    })
    expect(s.total).toBe(1)
  })
  // zod 預設 strip 未宣告鍵：漏宣告 title 不會噴錯，只會讓標題靜默消失（P4 前車之鑑）
  it('keeps title on list item / search result / report full', () => {
    expect(reportListItemSchema.parse({ ...baseItem, title: '內部標題' }).title).toBe('內部標題')
    expect(reportResultSchema.parse({
      ...baseItem, title: '內部標題', rank: 1, best_score: 0.5, match_count: 1, passages: [],
    }).title).toBe('內部標題')
    expect(reportFullSchema.parse({
      report_id: 'r1', file_name: 'a.pdf', title: '內部標題', market: null, source: null,
      summary: null, report_date: null, report_type: null, has_file: false,
    }).title).toBe('內部標題')
  })
  it('title 缺席仍可解析（尚未產生標題的報告佔多數）', () => {
    expect(reportListItemSchema.parse(baseItem).title).toBeUndefined()
  })
  // 同一種漏宣告：/api/search 回 lexical_truncated（字面候選被 cap 截斷），schema 沒宣告
  // 就會被 zod 靜默 strip 掉——後端量得到、前端永遠拿不到。
  it('keeps lexical_truncated on the search envelope', () => {
    const s = searchResponseSchema.parse({
      query: 'AI', market: null, total: 0, lexical_truncated: true, results: [],
    })
    expect(s.lexical_truncated).toBe(true)
  })
  it('lexical_truncated 缺席時是 undefined，不折成 false', () => {
    // undefined＝這個後端還沒回報（滾動部署的舊版），false＝回報了且沒截斷。
    const s = searchResponseSchema.parse({ query: 'AI', market: null, total: 0, results: [] })
    expect(s.lexical_truncated).toBeUndefined()
    expect(searchResponseSchema.parse({
      query: 'AI', market: null, total: 0, lexical_truncated: false, results: [],
    }).lexical_truncated).toBe(false)
  })
  it('parses a report full (has_file bool)', () => {
    const f = reportFullSchema.parse({
      report_id: 'r1', file_name: 'a.pdf', market: 'TW', source: '元大',
      summary: null, report_date: null, report_type: null, has_file: true,
    })
    expect(f.has_file).toBe(true)
  })
})
