import { describe, expect, it } from 'vitest'
import { ratingChangesSchema } from './librarySchemas'

const item = {
  market: 'TW', market_display: '台股', instrument_code: '8046', instrument_name: '南電',
  broker: 'fubon', broker_display: '富邦', report_date: '2026-10-05',
  rating_from: 'neutral', rating_to: 'buy', direction: 'up',
}

describe('ratingChangesSchema', () => {
  it('解析後端回應，缺公司名與顯示名時仍可用', () => {
    const { instrument_name: _n, broker_display: _b, market_display: _m, ...bare } = item
    const parsed = ratingChangesSchema.parse({ items: [item, bare] })
    expect(parsed.items[0].instrument_name).toBe('南電')
    expect(parsed.items[1].instrument_name).toBeUndefined()
  })

  it('評等與方向是後端的封閉詞彙', () => {
    expect(() => ratingChangesSchema.parse({ items: [{ ...item, rating_to: 'strong_buy' }] })).toThrow()
    expect(() => ratingChangesSchema.parse({ items: [{ ...item, direction: 'sideways' }] })).toThrow()
  })
})
