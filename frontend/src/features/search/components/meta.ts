/*
 * 廷豐智能研報 React — 詞表常數 + label/color 純查表
 * Port of web/static/app/meta.js（無 DOM、無 state，相依樹的葉節點）
 */

// 市場標籤對齊 findb 代碼；顯示中文名 + 配色
export const MARKET_META: Record<string, { label: string; color: string }> = {
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

export const mLabel = (c: string): string => MARKET_META[c]?.label ?? c ?? '—'
export const mColor = (c: string): string => MARKET_META[c]?.color ?? '#8e8e93'

// 商品類型詞表：中文 label + 配色（對齊 tagging.py INSTRUMENT_DISPLAY）
export const INSTRUMENT_META: Record<string, { label: string; color: string }> = {
  equity: { label: '股票', color: '#0a84ff' },
  index: { label: '指數', color: '#5e5ce6' },
  futures: { label: '期貨', color: '#ff9f0a' },
  options: { label: '選擇權', color: '#bf5af2' },
  etf: { label: 'ETF', color: '#30d158' },
  bond: { label: '債券', color: '#0bb8c4' },
  fx: { label: '外匯', color: '#00c7be' },
  commodity: { label: '原物料', color: '#ac8e68' },
  crypto: { label: '加密', color: '#e0a400' },
}

export const iLabel = (c: string): string => INSTRUMENT_META[c]?.label ?? c ?? '—'
export const iColor = (c: string): string => INSTRUMENT_META[c]?.color ?? '#8e8e93'

// 報告類型詞表（英文 key → 中文顯示；其餘原樣回傳）
const REPORT_TYPE_LABEL: Record<string, string> = { snapshot: '快照', memo: '備忘' }
export const tLabel = (t: string): string => REPORT_TYPE_LABEL[t] ?? t ?? '—'

export function fmtDate(d: string | null | undefined): string | null {
  return d ? d.replace(/-/g, '/') : null
}

// 排序選項中文標籤
export const SORT_LABELS: Record<string, string> = {
  relevance: '相關度',
  date_desc: '日期新→舊',
  date_asc: '日期舊→新',
}
