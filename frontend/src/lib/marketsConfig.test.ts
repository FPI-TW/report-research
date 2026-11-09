import { afterEach, describe, expect, it, vi } from 'vitest'
import { MARKET_ORDER, marketLabel } from './meta'
import { DEFAULT_MARKETS, fromResponse, loadMarkets, marketsConfig, marketsResponseSchema, setMarketsConfig } from './marketsConfig'

afterEach(() => {
  vi.unstubAllGlobals()
  setMarketsConfig(null)
})

describe('市場子集', () => {
  it('預設＝全部標準市場、標準名稱', () => {
    expect(marketsConfig()).toBe(DEFAULT_MARKETS)
    expect(DEFAULT_MARKETS.enabled).toEqual(MARKET_ORDER)
    expect(marketLabel('TW')).toBe('台股')
  })

  it('依 items 的顯示順序，名稱覆寫進 marketLabel', () => {
    const cfg = fromResponse(marketsResponseSchema.parse({
      markets: ['TW', 'US'],
      items: [{ code: 'US', label: '美國股市' }, { code: 'TW', label: '台股' }],
      labels: { HK: '香港股市' },
    }))
    expect(cfg.enabled).toEqual(['US', 'TW'])
    setMarketsConfig(cfg)
    expect(marketLabel('US')).toBe('美國股市')
    expect(marketLabel('HK')).toBe('香港股市')  // 未啟用的市場也照樣有名稱
    setMarketsConfig(null)
    expect(marketLabel('US')).toBe('美股')
  })

  it('不認得的代碼丟掉；全部丟光就退回全部標準市場', () => {
    expect(fromResponse({ items: [{ code: 'JP', label: '日股' }] }).enabled).toEqual(MARKET_ORDER)
    expect(fromResponse({ markets: ['TW', 'JP'] }).enabled).toEqual(['TW'])
  })

  it('抓不到時留在預設，不拋也不導向登入頁', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('x', { status: 401 })))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(await loadMarkets()).toBe(DEFAULT_MARKETS)
    warn.mockRestore()
  })
})
