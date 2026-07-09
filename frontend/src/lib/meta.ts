interface MarketMeta { label: string; color: string }

/** 市場語意色：降彩度重校（oklch 同層亮度/彩度），配合淡底深字 chip 使用。 */
const MARKETS: Record<string, MarketMeta> = {
  TW: { label: '台股', color: '#237a46' },
  US: { label: '美股', color: '#2e5fa3' },
  HK: { label: '港股', color: '#b26a0b' },
  CN: { label: '陸股', color: '#b03a30' },
  FX: { label: '外匯', color: '#0e7d74' },
  WTX: { label: '台指期', color: '#6e48a8' },
  MACRO: { label: '總經', color: '#a03052' },
  GLOBAL: { label: '全球', color: '#4a4fa0' },
  CRYPTO: { label: '加密', color: '#7d6234' },
}

const PTYPE: Record<string, string> = {
  股票: '#2e5fa3', 指數: '#4a4fa0', 期貨: '#b26a0b', 選擇權: '#6e48a8',
  ETF: '#237a46', 債券: '#0e7d74', 外匯: '#0e7d74', 原物料: '#7d6234', 加密: '#8a5a0f',
}

const FALLBACK = '#75808a'

export const MARKET_ORDER = ['TW', 'US', 'HK', 'CN', 'WTX', 'FX', 'MACRO', 'GLOBAL', 'CRYPTO'] as const

export function marketColor(code: string): string {
  return MARKETS[code]?.color ?? FALLBACK
}
export function marketLabel(code: string): string {
  return MARKETS[code]?.label ?? code
}
/** 市場 chip 統一「淡底深字」：底色為市場色 10% 混白。 */
export function marketTint(code: string): { background: string; color: string } {
  const c = marketColor(code)
  return { background: `color-mix(in srgb, ${c} 10%, white)`, color: c }
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
