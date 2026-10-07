import type { AnalyticsCell, AnalyticsSpan } from '../../../lib/generated/adminApi'

/** 範圍選項（天數，含今天）。後端上限 731 天。 */
export const RANGES = [
  { days: 7, label: '最近 7 天' },
  { days: 30, label: '最近 30 天' },
  { days: 90, label: '最近 90 天' },
  { days: 180, label: '最近 180 天' },
  { days: 365, label: '最近 365 天' },
] as const

export const fmtInt = (n: number | null | undefined): string => (n == null ? '—' : n.toLocaleString('zh-TW'))

/** 受 k 門檻抑制的格子顯示「<k」（不是 0：後端刻意不給數值）。 */
export function cellText(c: AnalyticsCell, minUsers: number): string {
  return c.suppressed ? `<${minUsers}` : fmtInt(c.value)
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} 秒`
}

export function fmtScore(v: number | null | undefined): string {
  return v == null ? '—' : v.toFixed(3)
}

/** 台北時間的今天（YYYY-MM-DD）。後端也以台北日曆日切日。 */
export function taipeiToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(now)
}

export function addDays(iso: string, delta: number): string {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + delta)
  return d.toISOString().slice(0, 10)
}

/** 最近 N 天（含今天）的 since；until 交給後端預設（今天）。 */
export function sinceForDays(days: number, today: string = taipeiToday()): string {
  return addDays(today, -(days - 1))
}

export function shortDay(iso: string): string {
  return iso.slice(5).replace('-', '/')
}

export function spanText(span: AnalyticsSpan): string {
  const how = span.source === 'live' ? '即時查詢' : '每晚彙總'
  return `${span.since} ～ ${span.until}：${how}`
}

export const ROUTE_TITLES: Record<string, string> = {
  path: '路由類別',
  decided_by: '判定者',
  llm_model: '回答模型',
  llm_error: 'LLM 錯誤種類',
}

// 與 app/services/scope_router.py 的五類、answer.py 的 decided_by 詞彙一致；沒列到的原樣顯示。
const VALUE_LABELS: Record<string, Record<string, string>> = {
  path: {
    corpus_qa: '語料問答', overview: '總覽', time_sensitive: '時效性', off_topic: '離題', advice_risk: '投資建議風險',
  },
  decided_by: { precheck: '詞表預判', overview: '總覽判定', llm: 'LLM 分類', fail_open: '降級（fail-open）', unknown: '不明' },
}

export function routeValueLabel(name: string, key: string): string {
  return VALUE_LABELS[name]?.[key] ?? key
}

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  'review.update': '待複核處理',
  'qa_content.read': '問答原文調閱',
  'upload.create': '上傳收檔',
  'upload.publish': '上傳發布',
  'upload.reject': '上傳退回',
  'report.hide': '研報隱藏',
  'report.restore': '研報恢復',
}
