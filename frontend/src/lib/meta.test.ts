import { marketColor, marketLabel, ptypeColor, MARKET_ORDER, instrumentLabel, reportTypeLabel } from './meta'

test('商品類型代碼→中文（鏡像 tagging.py），未知回原字串', () => {
  expect(instrumentLabel('equity')).toBe('股票')
  expect(instrumentLabel('futures')).toBe('期貨')
  expect(instrumentLabel('commodity')).toBe('原物料')
  expect(instrumentLabel('etf')).toBe('ETF')
  expect(instrumentLabel('股票')).toBe('股票')   // 已中文 → 原樣
})

test('報告類型：英文碼→中文，已中文者原樣', () => {
  expect(reportTypeLabel('memo')).toBe('備忘')
  expect(reportTypeLabel('snapshot')).toBe('快照')
  expect(reportTypeLabel('速報')).toBe('速報')
})

test('市場色與標籤（1a 墨青×鎏金重設計降彩度色票）', () => {
  expect(marketColor('TW')).toBe('#237a46')
  expect(marketLabel('TW')).toBe('台股')
  expect(marketColor('WTX')).toBe('#6e48a8')
  expect(marketLabel('CRYPTO')).toBe('加密')
})

test('未知市場回 fallback 色、原字串標籤', () => {
  expect(marketColor('ZZ')).toBe('#75808a')
  expect(marketLabel('ZZ')).toBe('ZZ')
})

test('商品類型色與 fallback', () => {
  expect(ptypeColor('股票')).toBe('#2e5fa3')
  expect(ptypeColor('不存在')).toBe('#75808a')
})

test('膠囊市場順序', () => {
  expect(MARKET_ORDER).toEqual(['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'])
})
