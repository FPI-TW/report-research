import { describe, it, expect } from 'vitest'
import {
  readingDocSchema,
  readingTextSchema,
  similarResponseSchema,
  takeawaySchema,
  signalSchema,
} from './readingSchemas'

const HASH = 'a'.repeat(64)

const minimalDoc = {
  report_id: 'r1',
  file_hash: HASH,
  file_name: 'a.pdf',
}

describe('readingDocSchema', () => {
  it('最小回應：可選欄位皆有預設，不炸', () => {
    const d = readingDocSchema.parse(minimalDoc)
    expect(d.instrument_types).toEqual([])
    expect(d.stock_targets).toEqual([])
    expect(d.takeaways).toEqual([])
    expect(d.signals).toEqual([])
    // 讀者視角狀態的保守預設：沒說就是沒有
    expect(d.text_state).toBe('missing')
    expect(d.signals_state).toBe('none')
    expect(d.has_file).toBe(false)
    expect(d.is_pdf).toBe(false)
    expect(d.text_chars).toBe(0)
  })

  // zod 預設 strip 未宣告鍵：漏宣告 title 不會噴錯，只會讓頁首靜默退回檔名
  it('title 留在解析結果，缺席時為 undefined', () => {
    expect(readingDocSchema.parse({ ...minimalDoc, title: '內部標題' }).title).toBe('內部標題')
    expect(readingDocSchema.parse(minimalDoc).title).toBeUndefined()
  })

  it('nullable 欄位吃 null 也吃缺席', () => {
    const d = readingDocSchema.parse({
      ...minimalDoc,
      market: null, source: null, source_display: null,
      report_date: null, report_type: null, summary: null, text_sha256: null,
    })
    expect(d.market).toBeNull()
    expect(d.summary).toBeNull()
    expect(d.text_sha256).toBeNull()
  })

  it('完整態：狀態列舉與訊號解析正確', () => {
    const d = readingDocSchema.parse({
      ...minimalDoc,
      market: 'TW',
      source: 'daiwa', source_display: '大和',
      report_date: '2026-07-14', summary: '摘要',
      has_file: true, is_pdf: true,
      text_state: 'ok', text_chars: 1200, text_sha256: 'b'.repeat(64),
      takeaways: [{ ordinal: 1, claim: '重申買進', quote: '視為首選', quote_start: 10, quote_end: 14, anchor_method: 'exact' }],
      signals_state: 'available',
      signals: [{
        instrument_code: '8046', market: 'TW',
        rating_normalized: 'buy', target_price: 2444, target_currency: 'TWD',
        thesis: [{ key: 'outlook', stance: 'positive', summary: '轉佳' }],
      }],
    })
    expect(d.text_state).toBe('ok')
    expect(d.signals_state).toBe('available')
    expect(d.signals[0].rating_normalized).toBe('buy')
    expect(d.signals[0].thesis[0].key).toBe('outlook')
    // 後端未給時仍需為空陣列，元件才能安全 .map
    expect(d.signals[0].eps_estimates).toEqual([])
  })

  it('未知的 text_state / signals_state 一律拒收（契約漂移要吵出來）', () => {
    expect(() => readingDocSchema.parse({ ...minimalDoc, text_state: 'partial' })).toThrow()
    expect(() => readingDocSchema.parse({ ...minimalDoc, signals_state: 'pending' })).toThrow()
  })

  it('缺 report_id / file_hash / file_name 一律拒收', () => {
    expect(() => readingDocSchema.parse({ file_hash: HASH, file_name: 'a.pdf' })).toThrow()
    expect(() => readingDocSchema.parse({ report_id: 'r1', file_name: 'a.pdf' })).toThrow()
    expect(() => readingDocSchema.parse({ report_id: 'r1', file_hash: HASH })).toThrow()
  })
})

describe('takeawaySchema', () => {
  it('錨不到時 quote_start/quote_end/anchor_method 可為 null', () => {
    const t = takeawaySchema.parse({ ordinal: 3, claim: '主張' })
    expect(t.quote_start).toBeUndefined()
    expect(t.anchor_method).toBeUndefined()
  })
  it('anchor_method 僅收受控詞彙', () => {
    expect(takeawaySchema.parse({ ordinal: 1, claim: 'c', anchor_method: 'normalized' }).anchor_method).toBe('normalized')
    expect(() => takeawaySchema.parse({ ordinal: 1, claim: 'c', anchor_method: 'fuzzy' })).toThrow()
  })
})

describe('signalSchema', () => {
  it('rating_normalized 預設 unknown', () => {
    expect(signalSchema.parse({ instrument_code: '2330', market: 'TW' }).rating_normalized).toBe('unknown')
  })
  it('eps 的 fiscal_year 是字串（與 radar 的 int 不同，不可混用）', () => {
    const s = signalSchema.parse({
      instrument_code: '2330', market: 'TW',
      eps_estimates: [{ fiscal_year: '2026', value: 45.5, currency: 'TWD' }],
    })
    expect(s.eps_estimates[0].fiscal_year).toBe('2026')
  })
  it('thesis 的 key 僅收四維', () => {
    expect(() => signalSchema.parse({
      instrument_code: '2330', market: 'TW', thesis: [{ key: 'momentum' }],
    })).toThrow()
  })
})

describe('readingTextSchema', () => {
  const minimalText = { file_hash: HASH, text: 'x', text_sha256: 'b'.repeat(64), text_chars: 1 }

  it('truncated 預設 false', () => {
    expect(readingTextSchema.parse(minimalText).truncated).toBe(false)
  })

  // 沒帶 ?chunk、chunk 不存在、錨不到、offset 落在截斷範圍外 → 後端都回 null
  it('命中 offset 可缺席或為 null（錨不到不是錯誤）', () => {
    expect(readingTextSchema.parse(minimalText).chunk_start).toBeUndefined()
    const nulled = readingTextSchema.parse({ ...minimalText, chunk_start: null, chunk_end: null })
    expect(nulled.chunk_start).toBeNull()
    expect(nulled.chunk_end).toBeNull()
  })

  it('錨定成功時帶回字元區間', () => {
    const t = readingTextSchema.parse({ ...minimalText, chunk_start: 2, chunk_end: 6 })
    expect([t.chunk_start, t.chunk_end]).toEqual([2, 6])
  })
})

describe('similarResponseSchema', () => {
  it('items 缺席時為空陣列', () => {
    expect(similarResponseSchema.parse({ file_hash: HASH }).items).toEqual([])
  })
  it('相符段數為必填（「9/12 段相符」不能沒有數字）', () => {
    expect(() => similarResponseSchema.parse({
      file_hash: HASH,
      items: [{ file_hash: 'b'.repeat(64), file_name: 'x.pdf' }],
    })).toThrow()
  })
  it('nullable 的券商/日期/摘要可缺席', () => {
    const s = similarResponseSchema.parse({
      file_hash: HASH,
      items: [{ file_hash: 'b'.repeat(64), file_name: 'x.pdf', matched_probes: 9, total_probes: 12 }],
    })
    expect(s.items[0].matched_probes).toBe(9)
    expect(s.items[0].source).toBeUndefined()
    expect(s.items[0].title).toBeUndefined()
  })
})
