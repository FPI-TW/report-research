import type { RatingNorm } from '../../lib/radarSchemas'

/** 五級評等的中文（與券商共識頁、閱讀頁同一組字）。unknown 不顯示徽章。 */
export const RATING_LABEL: Record<RatingNorm, string> = {
  buy: '買進',
  overweight: '加碼',
  neutral: '中立',
  underweight: '減碼',
  sell: '賣出',
  unknown: '未提供',
}

function ymd(iso: string | null | undefined): [string, string, string] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  return m ? [m[1], m[2], m[3]] : null
}

/** 清單用短日期：今年只寫「10-27」，跨年才寫完整日期，免得去年的變動看起來像今年。 */
export function shortDate(iso: string | null | undefined, now: Date = new Date()): string {
  const d = ymd(iso)
  if (!d) return ''
  return Number(d[0]) === now.getFullYear() ? `${d[1]}-${d[2]}` : `${d[0]}-${d[1]}-${d[2]}`
}

/** 完整日期（YYYY-MM-DD）；缺值回空字串。 */
export function fullDate(iso: string | null | undefined): string {
  const d = ymd(iso)
  return d ? d.join('-') : ''
}

export function isSameLocalDay(iso: string | null | undefined, now: Date = new Date()): boolean {
  const d = ymd(iso)
  return !!d && Number(d[0]) === now.getFullYear() && Number(d[1]) === now.getMonth() + 1
    && Number(d[2]) === now.getDate()
}

/** 「更多最新入庫」標題旁的月份（2026 年 10 月）。 */
export function monthOf(iso: string | null | undefined): string {
  const d = ymd(iso)
  return d ? `${d[0]} 年 ${Number(d[1])} 月` : ''
}

// 簡報條目結尾的券商代碼（「…領漲（yuanta）。」）：首頁小卡只放重點，代碼對讀者沒有意義。
const TRAILING_BROKER = /\s*[（(][A-Za-z][A-Za-z0-9_ ]*[)）]\s*([。．.]?)\s*$/

function cleanBullet(line: string): string {
  return line
    .replace(/^\s*[-*•]\s+/, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(TRAILING_BROKER, '$1')
    .trim()
}

/**
 * 從簡報 markdown 取前幾條重點：優先「今日重點」段的條列，沒有這一段才退回全文第一批條列。
 * 簡報的段落結構由批次的 prompt 決定（app/services/brief.py），這裡只認條列，不認其他格式。
 */
export function briefHighlights(markdown: string, limit = 4): string[] {
  const lines = markdown.split('\n')
  const start = lines.findIndex(l => /^#{1,6}\s*今日重點/.test(l.trim()))
  const scope = start >= 0 ? lines.slice(start + 1) : lines
  const out: string[] = []
  for (const raw of scope) {
    const line = raw.trim()
    if (start >= 0 && /^#{1,6}\s/.test(line)) break
    if (!/^[-*•]\s+/.test(line)) continue
    const text = cleanBullet(line)
    if (text) out.push(text)
    if (out.length >= limit) break
  }
  return out
}

/** 研究助理預填的提問（沿用閱讀頁「就這篇提問」的寫法）。 */
export function askHref(title: string): string {
  return `/assistant?q=${encodeURIComponent(`關於《${title}》：`)}`
}
