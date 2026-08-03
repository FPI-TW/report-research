import type { ReadingDoc, ReadingRatingNorm, ThesisKey } from '../../lib/readingSchemas'

/** file_hash 為 sha256 十六進位；非 64-hex 前端先擋一次（不打 API）。 */
const HASH_RE = /^[0-9a-f]{64}$/i
export function isValidHash(hash: string | null | undefined): boolean {
  return typeof hash === 'string' && HASH_RE.test(hash)
}

/**
 * 閱讀頁連結。
 *
 * 刻意不帶任何 query：閱讀頁已無文字檢視，命中的 chunk_index 沒有消費端，
 * 帶著它只會在網址列留下一個沒有作用的參數（本 repo 最容易誤導下一個人的那種殘留）。
 */
export function reportHref(fileHash: string): string {
  return `/report/${fileHash}`
}

export const RATING_DISPLAY: Record<ReadingRatingNorm, string> = {
  buy: '買進',
  overweight: '加碼',
  neutral: '中立',
  underweight: '減碼',
  sell: '賣出',
  unknown: '未評等',
}

/** 評等 → 三桶語意（著色用）。 */
export const RATING_BUCKET: Record<ReadingRatingNorm, 'bull' | 'neu' | 'bear'> = {
  buy: 'bull',
  overweight: 'bull',
  neutral: 'neu',
  underweight: 'bear',
  sell: 'bear',
  unknown: 'neu',
}

export const THESIS_LABEL: Record<ThesisKey, string> = {
  outlook: '展望',
  catalyst: '催化',
  risk: '風險',
  valuation: '估值',
}

/** 四維論點的顯示順序（對齊後端 signal_extract.THESIS_DIMENSIONS）。 */
export const THESIS_ORDER: ThesisKey[] = ['outlook', 'catalyst', 'risk', 'valuation']

/**
 * stance 受控詞彙 → 中文顯示，逐字鏡像後端 signal_extract.STANCE_CONSTRUCTIVENESS 的維度特定詞彙。
 *
 * 注意：這是「單篇報告的靜態立場」，不是雷達的「跨報告變化」。故用「正面/中性/負面」
 * 而非「轉強/轉弱」——單篇沒有前次可比，說「轉強」會是無中生有的比較敘述。
 * 風險維度以「升高＝建設性下降」表達，避免 positive/negative 對風險的符號歧義。
 */
const STANCE_DISPLAY: Record<ThesisKey, Record<string, string>> = {
  outlook: { positive: '正面', neutral: '中性', negative: '負面' },
  catalyst: { positive: '正面', neutral: '中性', negative: '負面' },
  valuation: { attractive: '偏低', fair: '合理', stretched: '偏高' },
  risk: { easing: '趨緩', stable: '持平', rising: '升高' },
}

/** 建設性序位（+1/0/−1）→ 色調桶，鏡像後端同名對照表。 */
const STANCE_TONE: Record<ThesisKey, Record<string, 'pos' | 'neu' | 'neg'>> = {
  outlook: { positive: 'pos', neutral: 'neu', negative: 'neg' },
  catalyst: { positive: 'pos', neutral: 'neu', negative: 'neg' },
  valuation: { attractive: 'pos', fair: 'neu', stretched: 'neg' },
  risk: { easing: 'pos', stable: 'neu', rising: 'neg' },
}

/** 未映射的 stance 原樣顯示（不猜、不丟），色調退回中性。 */
export function stanceDisplay(key: ThesisKey, stance: string | null | undefined): string | null {
  if (!stance) return null
  return STANCE_DISPLAY[key]?.[stance] ?? stance
}

export function stanceTone(key: ThesisKey, stance: string | null | undefined): 'pos' | 'neu' | 'neg' {
  if (!stance) return 'neu'
  return STANCE_TONE[key]?.[stance] ?? 'neu'
}

/**
 * 報頭 meta 組字串：券商 · 日期。任一為 null 時不留多餘的分隔點。
 * source_display 優先於 source（顯示名 vs 內部代號）。
 */
export function metaParts(doc: Pick<ReadingDoc, 'source' | 'source_display' | 'report_date'>): string[] {
  const broker = doc.source_display || doc.source || null
  const date = doc.report_date ? doc.report_date.slice(0, 10) : null
  return [broker, date].filter((s): s is string => Boolean(s))
}

/** 相似研報卡的 meta：券商 · 日期（同樣不留空分隔）。 */
export function similarMeta(item: {
  source?: string | null
  source_display?: string | null
  report_date?: string | null
}): string {
  const broker = item.source_display || item.source || null
  const date = item.report_date ? item.report_date.slice(0, 10) : null
  return [broker, date].filter(Boolean).join(' · ')
}

/** 設計稿拍板的相似度說法：「9/12 段相符」。 */
export function matchedText(matched: number, total: number): string {
  return `${matched}/${total} 段相符`
}

/** 相似度條寬度百分比；total 為 0 時回 0（不除以零）。 */
export function matchedPct(matched: number, total: number): number {
  if (!total) return 0
  return Math.max(0, Math.min(100, Math.round((matched / total) * 100)))
}

const CURRENCY_PREFIX: Record<string, string> = {
  TWD: 'NT$',
  USD: 'US$',
  HKD: 'HK$',
  CNY: 'CN¥',
  JPY: '¥',
}

/** 目標價數值（不含幣別；幣別走 fig 副標）。 */
export function fmtTargetPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-TW', {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  })
}

/** 目標價副標：幣別 · 期間（缺值不留空分隔）。 */
export function targetSub(
  currency: string | null | undefined,
  horizon: string | null | undefined,
): string {
  return [currency, horizon].filter(Boolean).join(' · ')
}

export function currencyPrefix(currency: string | null | undefined): string {
  if (!currency) return ''
  return CURRENCY_PREFIX[currency] ?? `${currency} `
}
