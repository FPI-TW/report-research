import type { RatingNorm, StanceFilter, Window } from '../../lib/radarSchemas'

/** 立場三桶的前端鍵名。API 參數那層用後端拼法，對接點只有 `BUCKET_TO_STANCE`。 */
export type Bucket = 'bull' | 'neu' | 'bear'

export const RATING_DISPLAY: Record<RatingNorm, string> = {
  buy: '買進',
  overweight: '加碼',
  neutral: '中立',
  underweight: '減碼',
  sell: '賣出',
  unknown: '未評等',
}

/** 評等 → 三桶（偏多／中立／偏空）。 */
export const RATING_BUCKET: Record<RatingNorm, Bucket> = {
  buy: 'bull',
  overweight: 'bull',
  neutral: 'neu',
  underweight: 'bear',
  sell: 'bear',
  unknown: 'neu',
}

/** 三桶的顯示字樣。與後端 `radar/scale.py` 的 `BUCKET_DISPLAY` 逐字相同。 */
export const BUCKET_DISPLAY: Record<Bucket, string> = {
  bull: '偏多',
  neu: '中立',
  bear: '偏空',
}

/** 三桶 → API 的 `stance` 參數值。兩套拼法只在這一處對接。 */
export const BUCKET_TO_STANCE: Record<Bucket, StanceFilter> = {
  bull: 'bullish',
  neu: 'neutral',
  bear: 'bearish',
}

/** 期間分段（交接包：30／90／180 天／全部），值與 API 的 `window` 逐字相同。 */
export const WINDOW_OPTIONS: ReadonlyArray<{ value: Window; label: string }> = [
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
  { value: '180', label: '180 天' },
  { value: 'all', label: '全部' },
]

function fmtNum(n: number, digits: number): string {
  return n.toLocaleString('zh-TW', { minimumFractionDigits: 0, maximumFractionDigits: digits })
}

/** 不帶幣別前綴的數值（幣別另外印在旁邊）：≥100 取整數並加千分位，其餘最多兩位小數。 */
export function fmtValue(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return fmtNum(n, Math.abs(n) >= 100 ? 0 : 2)
}

/** 帶正負號的百分比（一位小數）；負號用 U+2212，與原型一致。 */
export function fmtSignedPct(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return ''
  const sign = pct > 0 ? '+' : pct < 0 ? '−' : ''
  return `${sign}${Math.abs(pct).toFixed(1)}%`
}

/**
 * 清單與詳情用的短日期（交接包的「10-27」）：今年 → `MM-DD`，其他年份 → `YYYY-MM-DD`。
 *
 * 只印月日在跨年資料上會讓「2025-10-27」與「2026-10-27」長得一樣，所以只有今年才省年份。
 * 缺值或格式不符回 `—`（後端 `report_date` 為 NULL 時給空字串）。
 */
export function fmtShortDate(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return '—'
  return Number(m[1]) === now.getFullYear() ? `${m[2]}-${m[3]}` : `${m[1]}-${m[2]}-${m[3]}`
}

/** EPS 口徑的年度標籤（交接包的「FY2027」）；期間不是全年時接在後面（`FY2027 1H`）。 */
export function epsFyLabel(fiscalYear?: number | null, period?: string | null): string {
  const span = period?.trim() ?? ''
  const fy = fiscalYear != null ? `FY${fiscalYear}` : ''
  return [fy, span.toUpperCase() === 'FY' ? '' : span].filter(Boolean).join(' ')
}
