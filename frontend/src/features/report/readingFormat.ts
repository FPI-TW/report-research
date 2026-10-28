import type { ReadingRatingNorm } from '../../lib/readingSchemas'

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

/** 日期只取 YYYY-MM-DD（後端可能帶時間）。缺值回 null，呼叫端整格不渲染。 */
export function fmtDate(iso: string | null | undefined): string | null {
  return iso ? iso.slice(0, 10) : null
}

/** 目標價數值（不含幣別；幣別另外標）。 */
export function fmtTargetPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-TW', {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  })
}

/**
 * 目標價調幅（後端 `pct_change` 已是百分比單位，例 12.5）。取絕對值，方向另由箭頭與
 * 「上調／下修」表達；整數不補小數（8 → 8%），其餘留一位（12.5 → 12.5%）。
 */
export function fmtRevisionPct(pct: number): string {
  return `${Number(Math.abs(pct).toFixed(1))}%`
}
