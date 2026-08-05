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

/**
 * 資料缺漏的統一字樣。
 *
 * `—` 與這個字串**不是同義詞**，兩者刻意分開：破折號讀起來像「這一格本來就沒有欄位」
 * （共識卡沒有那一級距、分布條沒有那一段），而券商面板的空格代表的是「這份研報沒有
 * 揭露這個數字」——那是關於研報本身的資訊，說得出口才不會被誤讀成系統沒抓到。
 */
export const NOT_PROVIDED = '未提供'

/** 研報「過期」門檻（天）。標籤由門檻推導，兩者不會各說各話。 */
export const STALE_REPORT_DAYS = 90
export const STALE_REPORT_LABEL = `最新報告已超過 ${STALE_REPORT_DAYS} 天`

/**
 * 期間／口徑不同的統一說法。
 *
 * 後端對這兩種不可比較各給了一份 `incomparable_reason`，而 `prev_value`/`curr_value`
 * 是 `scale.eps_group_label()` 的原始輸出——長得像 `FY2026 · 1H · TWD · per_share`，
 * 把資料庫欄位值（幣別代碼、`per_share`）直接攤給讀者看。再加上 DirectionTag 對
 * `incomparable` 一律印「不可比較」，同一件事在畫面上會出現三次。統一成一句話。
 */
/**
 * 刻意**不寫成**「季度 EPS 因期間或資料口徑不同」。
 *
 * 觸發這句話的群組多半是年度（`period='FY'`）而非季度——生產資料實測 66 個命中案例
 * 中有 21 個完全沒有季度群組。把「季度」寫死等於替資料講了一件它沒說的事，
 * 而那正是「不得虛構缺少的資料」要擋的東西。其餘用字與原始要求逐字相同。
 */
export const EPS_INCOMPARABLE_NOTE = 'EPS 因期間或資料口徑不同，暫不比較。'
export const TARGET_INCOMPARABLE_NOTE = '目標價幣別不同，暫不比較。'

const DAY_MS = 86_400_000

/**
 * 研報距今天數（整日，時區無關：兩端都取當地日曆日再換算 UTC 午夜）。
 *
 * 日期缺漏或格式不符回 `null`——**不知道日期不等於零天**，呼叫端據此選擇不顯示，
 * 而不是把未知渲染成「剛出爐」。後端的 `report_date` 在 NULL 時是空字串（不是 null），
 * 所以這裡以「解析得出來」而非「非 null」為準。
 */
export function daysSinceReport(
  iso: string | null | undefined,
  now: Date = new Date(),
): number | null {
  if (!iso) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return null
  const reported = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
  return Math.floor((today - reported) / DAY_MS)
}

/** 是否超過 `STALE_REPORT_DAYS`。日期未知一律 false（見 `daysSinceReport`）。 */
export function isReportStale(iso: string | null | undefined, now: Date = new Date()): boolean {
  const days = daysSinceReport(iso, now)
  return days != null && days > STALE_REPORT_DAYS
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

/**
 * `unit` 是內部欄位，不直接印給使用者。
 *
 * 後端對缺值一律補 `'per_share'`（`signal_extract._normalize_eps`），所以它既不是
 * 券商說的話、也不是可靠的區辨資訊；能對外講的只有「這是每股數字」，寫成金額後綴
 * 比獨立一段「每股」短、也不會與 FY 標籤搶視線。**其他 unit 值一律不臆測、不外洩**
 * ——舊寫法是 `EPS_UNIT_DISPLAY[unit] ?? unit`，未收錄的值會原樣印出資料庫內容。
 */
const EPS_PER_SHARE_SUFFIX = '／股'

/**
 * EPS 比較群組標籤：`FY2027` ＋ 期間 `FY` → `FY27E`；非 FY 的期間附在後面 → `FY27E 1H`。
 *
 * `E` 取自 estimate——來源欄位本來就是 `report_signal.eps_estimates`，不是這裡推測出來的。
 * 年份縮成兩位是為了讓它在表格裡不搶主值；`fiscalYear` 若已是兩位數就原樣用（後端無
 * 型別約束，只保證是 int）。
 */
export function epsPeriodLabel(
  fiscalYear?: number | null,
  period?: string | null,
): string {
  const fy = fiscalYear == null
    ? ''
    : `FY${fiscalYear >= 100 ? String(fiscalYear).slice(-2) : String(fiscalYear).padStart(2, '0')}E`
  const span = period?.trim() ?? ''
  return [fy, span.toUpperCase() === 'FY' ? '' : span].filter(Boolean).join(' ')
}

/**
 * `fmtEps` 的分段版：主值與比較群組標籤分開，給需要壓視覺層次的地方
 * （券商表格把標籤降成次要小字，金額才是要一眼看到的東西）。
 *
 * `fmtEps` **由這支推導**而非各算一套——先前兩者是平行實作，靠一條「串起來要相等」的
 * 測試守著；由建構保證比由測試守著可靠，那條測試因此改為釘住實際字串。
 */
export function fmtEpsParts(
  value: number | null | undefined,
  currency: string | null | undefined,
  fiscalYear?: number | null,
  period?: string | null,
  unit?: string | null,
): { value: string; meta: string } {
  if (value == null) return { value: NOT_PROVIDED, meta: '' }
  const amount = fmtPrice(value, currency)
  const label = epsPeriodLabel(fiscalYear, period)
  return {
    value: unit === 'per_share' ? `${amount}${EPS_PER_SHARE_SUFFIX}` : amount,
    meta: label ? ` · ${label}` : '',
  }
}

/** EPS 數值與比較群組標籤；幣別只取自 EPS，不接受目標價 fallback。 */
export function fmtEps(
  value: number | null | undefined,
  currency: string | null | undefined,
  fiscalYear?: number | null,
  period?: string | null,
  unit?: string | null,
): string {
  const parts = fmtEpsParts(value, currency, fiscalYear, period, unit)
  return parts.value + parts.meta
}

/**
 * 券商面板專用的缺值處理：回 `未提供` 而非 `—`。
 *
 * 刻意不去改 `fmtPrice`／`fmtDate` 本身——它們還有共識卡、KPI、標的卡三組消費端，
 * 那些地方的破折號語意是對的（見 `NOT_PROVIDED` 的說明）。
 */
export function fmtPriceOrNA(
  value: number | null | undefined,
  currency: string | null | undefined,
): string {
  return value == null ? NOT_PROVIDED : fmtPrice(value, currency)
}

/** 同上。後端 `report_date` 為 NULL 時給的是空字串，所以要擋的是 falsy 不是 null。 */
export function fmtDateOrNA(iso: string | null | undefined): string {
  return iso ? fmtDate(iso) : NOT_PROVIDED
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
