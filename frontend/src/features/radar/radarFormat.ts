import type { CSSProperties } from 'react'
import type { Direction, RatingNorm } from '../../lib/radarSchemas'

export const RATING_DISPLAY: Record<RatingNorm, string> = {
  buy: '買進',
  overweight: '加碼',
  neutral: '中立',
  underweight: '減碼',
  sell: '賣出',
  unknown: '未評等',
}

/** 評等 → 三桶語意（立場詞著色：偏多/中立/偏空）。 */
export const RATING_BUCKET: Record<RatingNorm, 'bull' | 'neu' | 'bear'> = {
  buy: 'bull',
  overweight: 'bull',
  neutral: 'neu',
  underweight: 'bear',
  sell: 'bear',
  unknown: 'neu',
}

/** 市場徽章底色：以 --badge 帶入市場語意色（未知市場退回 fallback）。 */
export function marketVar(market: string): CSSProperties {
  return { '--badge': `var(--mkt-${market}, var(--mkt-fallback))` } as CSSProperties
}

export const WINDOW_OPTIONS = [
  { value: '30' as const, label: '30 天' },
  { value: '90' as const, label: '90 天' },
  { value: '180' as const, label: '180 天' },
  { value: 'all' as const, label: '全部' },
]

export const WINDOW_LABEL: Record<string, string> = {
  '30': '近 30 天',
  '90': '近 90 天',
  '180': '近 180 天',
  all: '全部歷程',
}

const CURRENCY_PREFIX: Record<string, string> = {
  TWD: 'NT$',
  USD: 'US$',
  HKD: 'HK$',
  CNY: 'CN¥',
  JPY: '¥',
}

/** 幣別 → 顯示前綴；未知則原樣 + 空白。 */
export function currencyPrefix(currency: string | null | undefined): string {
  if (!currency) return ''
  return CURRENCY_PREFIX[currency] ?? `${currency} `
}

/** 數值格式：tabular 友善，缺值回 — */
export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return '—'
  return n.toLocaleString('zh-TW', {
    minimumFractionDigits: Number.isInteger(n) ? 0 : Math.min(digits, 2),
    maximumFractionDigits: digits,
  })
}

export function fmtPrice(
  value: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (value == null) return '—'
  const prefix = currencyPrefix(currency)
  return `${prefix}${fmtNum(value, value >= 100 ? 0 : 2)}`.trim()
}

const EPS_UNIT_DISPLAY: Record<string, string> = {
  per_share: '每股',
}

/** EPS 數值與比較群組 metadata；幣別只取自 EPS，不接受目標價 fallback。 */
export function fmtEps(
  value: number | null | undefined,
  currency: string | null | undefined,
  fiscalYear?: number | null,
  period?: string | null,
  unit?: string | null,
): string {
  if (value == null) return '—'
  const parts = [
    fmtPrice(value, currency),
    fiscalYear != null ? `FY${fiscalYear}` : null,
    period || null,
    unit ? (EPS_UNIT_DISPLAY[unit] ?? unit) : null,
  ]
  return parts.filter((part): part is string => Boolean(part)).join(' · ')
}

export function fmtPct(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return ''
  return `${Math.abs(pct).toFixed(1)}%`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  // 接受 YYYY-MM-DD 或 ISO datetime
  const d = iso.slice(0, 10)
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d)
  if (!m) return iso
  return `${m[1]}/${m[2]}/${m[3]}`
}

export function directionVerb(dir: Direction, kind: 'rating' | 'number' = 'number'): string {
  if (kind === 'rating') {
    if (dir === 'up') return '上調'
    if (dir === 'down') return '下調'
    if (dir === 'flat') return '持平'
  } else {
    if (dir === 'up') return '上修'
    if (dir === 'down') return '下修'
    if (dir === 'flat') return '持平'
  }
  if (dir === 'incomparable') return '不可比較'
  return ''
}

export function directionIconName(
  dir: Direction,
): 'trendUp' | 'trendDown' | 'trendFlat' | 'notComparable' | null {
  if (dir === 'up') return 'trendUp'
  if (dir === 'down') return 'trendDown'
  if (dir === 'flat') return 'trendFlat'
  if (dir === 'incomparable') return 'notComparable'
  return null
}

export function dash(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  return String(v)
}
