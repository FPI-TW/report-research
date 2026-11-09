import { t } from '@/i18n'

interface MarketMeta { label: string; color: string }

/** 市場語意色：降彩度重校（oklch 同層亮度/彩度），配合淡底深字 chip 使用。 */
const MARKETS: Record<string, MarketMeta> = {
  TW: { get label() { return t('lib.meta.taiwan') }, color: '#237a46' },
  US: { get label() { return t('lib.meta.us') }, color: '#2e5fa3' },
  HK: { get label() { return t('lib.meta.hongKong') }, color: '#b26a0b' },
  CN: { get label() { return t('lib.meta.china') }, color: '#b03a30' },
  FX: { get label() { return t('lib.meta.fx') }, color: '#0e7d74' },
  WTX: { get label() { return t('lib.meta.taiexFutures') }, color: '#6e48a8' },
  MACRO: { get label() { return t('lib.meta.macro') }, color: '#a03052' },
  GLOBAL: { get label() { return t('lib.meta.global') }, color: '#4a4fa0' },
  CRYPTO: { get label() { return t('lib.meta.crypto') }, color: '#7d6234' },
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
// profile 的顯示名稱覆寫（`lib/marketsConfig.ts` 抓到 `/api/markets` 時寫入；放在這裡是為了不讓兩個模組互相 import）。
let labelOverrides: Readonly<Record<string, string>> = {}

export function setMarketLabelOverrides(next: Readonly<Record<string, string>>): void {
  labelOverrides = next
}

/** 市場顯示名稱：profile 覆寫 → 標準名稱 → 代碼本身。 */
export function marketLabel(code: string): string {
  return labelOverrides[code] ?? MARKETS[code]?.label ?? code
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
  get equity() { return t('lib.meta.equity') }, get index() { return t('lib.meta.index') }, get futures() { return t('lib.meta.futures') }, get options() { return t('lib.meta.options') }, etf: 'ETF',
  get bond() { return t('lib.meta.bonds') }, get fx() { return t('lib.meta.fx') }, get commodity() { return t('lib.meta.commodities') }, get crypto() { return t('lib.meta.crypto') },
}
// 報告類型：多數標註已是中文，僅少數英文碼需對照；其餘回傳原字串
const REPORT_TYPE_LABEL: Record<string, string> = {
  get memo() { return t('lib.meta.memo') }, get snapshot() { return t('lib.meta.snapshot') },
}

export function instrumentLabel(code: string): string {
  return INSTRUMENT_LABEL[code] ?? code
}
export function reportTypeLabel(value: string): string {
  return REPORT_TYPE_LABEL[value] ?? value
}
