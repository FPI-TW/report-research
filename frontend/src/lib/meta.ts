interface MarketMeta { label: string; color: string }

const MARKETS: Record<string, MarketMeta> = {
  TW: { label: '台股', color: '#34c759' },
  US: { label: '美股', color: '#007aff' },
  HK: { label: '港股', color: '#ff9500' },
  CN: { label: '陸股', color: '#ff3b30' },
  FX: { label: '外匯', color: '#00c7be' },
  WTX: { label: '台指期', color: '#af52de' },
  MACRO: { label: '總經', color: '#ff2d55' },
  GLOBAL: { label: '全球', color: '#5856d6' },
  CRYPTO: { label: '加密', color: '#a2845e' },
}

const PTYPE: Record<string, string> = {
  股票: '#0a84ff', 指數: '#5e5ce6', 期貨: '#ff9f0a', 選擇權: '#bf5af2',
  ETF: '#30d158', 債券: '#0bb8c4', 外匯: '#00c7be', 原物料: '#ac8e68', 加密: '#e0a400',
}

const FALLBACK = '#8e8e93'

export const MARKET_ORDER = ['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'] as const

export function marketColor(code: string): string {
  return MARKETS[code]?.color ?? FALLBACK
}
export function marketLabel(code: string): string {
  return MARKETS[code]?.label ?? code
}
export function ptypeColor(name: string): string {
  return PTYPE[name] ?? FALLBACK
}

// 商品類型代碼 → 中文顯示（鏡像 app/services/tagging.py INSTRUMENT_DISPLAY）
const INSTRUMENT_LABEL: Record<string, string> = {
  equity: '股票', index: '指數', futures: '期貨', options: '選擇權', etf: 'ETF',
  bond: '債券', fx: '外匯', commodity: '原物料', crypto: '加密',
}
// 報告類型：多數標註已是中文，僅少數英文碼需對照；其餘回傳原字串
const REPORT_TYPE_LABEL: Record<string, string> = {
  memo: '備忘', snapshot: '快照',
}

export function instrumentLabel(code: string): string {
  return INSTRUMENT_LABEL[code] ?? code
}
export function reportTypeLabel(value: string): string {
  return REPORT_TYPE_LABEL[value] ?? value
}
