import { marketColor, marketLabel, ptypeColor, MARKET_ORDER } from './meta'

test('市場色與標籤取自 .dc.html', () => {
  expect(marketColor('TW')).toBe('#34c759')
  expect(marketLabel('TW')).toBe('台股')
  expect(marketColor('WTX')).toBe('#af52de')
  expect(marketLabel('CRYPTO')).toBe('加密')
})

test('未知市場回 fallback 色、原字串標籤', () => {
  expect(marketColor('ZZ')).toBe('#8e8e93')
  expect(marketLabel('ZZ')).toBe('ZZ')
})

test('商品類型色與 fallback', () => {
  expect(ptypeColor('股票')).toBe('#0a84ff')
  expect(ptypeColor('不存在')).toBe('#8e8e93')
})

test('膠囊市場順序', () => {
  expect(MARKET_ORDER).toEqual(['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'])
})
