import type { AnalyticsCell, AnalyticsSpan, AnalyticsTopList } from '../../../lib/generated/adminApi'
import { t } from '@/i18n'

/** 範圍選項（天數，含今天；分段切換用，所以值是字串）。後端上限 731 天。 */
export const RANGES = [
  { value: '7', get label() { return t('admin.analytics.analyticsFormat.7Days') } },
  { value: '30', get label() { return t('admin.analytics.analyticsFormat.30Days') } },
  { value: '90', get label() { return t('admin.analytics.analyticsFormat.90Days') } },
  { value: '180', get label() { return t('admin.analytics.analyticsFormat.180Days') } },
  { value: '365', get label() { return t('admin.analytics.analyticsFormat.1Year') } },
] as const
export type RangeDays = (typeof RANGES)[number]['value']

export const fmtInt = (n: number | null | undefined): string => (n == null ? '—' : n.toLocaleString('zh-TW'))

/**
 * 受 k 門檻抑制的格子顯示「<k」（不是 0：後端刻意不給數值）；互補抑制的格子（本身人數夠，但為了不讓總量減其他格
 * 推回唯一被抑制的那一格而一併隱藏）顯示「隱藏」——它不是「<k」，標成 <k 會是錯的資訊。
 */
export function cellText(c: AnalyticsCell, minUsers: number): string {
  if (!c.suppressed) return fmtInt(c.value)
  return c.suppression_reason === 'complementary' ? t('admin.analytics.analyticsFormat.hidden') : `<${minUsers}`
}

export function cellTitle(c: AnalyticsCell, minUsers: number): string {
  if (!c.suppressed) return t('admin.analytics.analyticsFormat.users', { users: fmtInt(c.users) })
  return c.suppression_reason === 'complementary'
    ? t('admin.analytics.analyticsFormat.alsoHiddenSoTheHidden')
    : t('admin.analytics.analyticsFormat.fewerThanUsersNumberNot', { minUsers })
}

/** 清單裡沒列出來的項目（開放詞彙連名稱都不給）的說明；沒有就回 null。 */
export function hiddenNote(list: AnalyticsTopList, minUsers: number): string | null {
  const shown = list.cells.filter(c => c.suppressed).length
  const total = list.suppressed_count + list.complementary_count
  if (total - shown <= 0) return null
  const parts = [t('admin.analytics.analyticsFormat.itemsUnderUsers', { minUsers, suppressed_count: fmtInt(list.suppressed_count) })]
  if (list.complementary_count > 0) parts.push(t('admin.analytics.analyticsFormat.moreHiddenToPreventInference', { complementary_count: fmtInt(list.complementary_count) }))
  return list.cells.length === 0 ? t('admin.analytics.analyticsFormat.noItemsShown', { parts: parts.join(t('admin.analytics.analyticsFormat.text')) }) : t('admin.analytics.analyticsFormat.moreNotShown', { fmtInt: fmtInt(total - shown), parts: parts.join(t('admin.analytics.analyticsFormat.text')) })
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : t('admin.analytics.analyticsFormat.s', { v: (ms / 1000).toFixed(1) })
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
  const how = span.source === 'live' ? t('admin.analytics.analyticsFormat.liveQuery') : t('admin.analytics.analyticsFormat.nightlyRollup')
  return t('admin.analytics.analyticsFormat.text2', { since: span.since, until: span.until, how })
}

export const ROUTE_TITLES: Record<string, string> = {
  get path() { return t('admin.analytics.analyticsFormat.routeCategory') },
  get decided_by() { return t('admin.analytics.analyticsFormat.decidedBy') },
  get llm_model() { return t('admin.analytics.analyticsFormat.answerModel') },
  get llm_error() { return t('admin.analytics.analyticsFormat.llmErrorKind') },
}

// 與 app/services/scope_router.py 的五類、answer.py 的 decided_by 詞彙一致；沒列到的原樣顯示。
const VALUE_LABELS: Record<string, Record<string, string>> = {
  path: {
    get corpus_qa() { return t('admin.analytics.analyticsFormat.corpusQA') }, get overview() { return t('admin.analytics.analyticsFormat.overview') }, get time_sensitive() { return t('admin.analytics.analyticsFormat.timeliness') }, get off_topic() { return t('admin.analytics.analyticsFormat.offTopic') }, get advice_risk() { return t('admin.analytics.analyticsFormat.investmentAdviceRisk') },
  },
  decided_by: { get precheck() { return t('admin.analytics.analyticsFormat.lexiconPreCheck') }, get overview() { return t('admin.analytics.analyticsFormat.overviewDecision') }, get llm() { return t('admin.analytics.analyticsFormat.llmClassification') }, get fail_open() { return t('admin.analytics.analyticsFormat.degradedFailOpen') }, get unknown() { return t('admin.analytics.analyticsFormat.unknown') } },
}

export function routeValueLabel(name: string, key: string): string {
  return VALUE_LABELS[name]?.[key] ?? key
}

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  get 'review.update'() { return t('admin.analytics.analyticsFormat.reviewHandling') },
  get 'qa_content.read'() { return t('admin.analytics.analyticsFormat.qAContentAccess') },
  get 'upload.create'() { return t('admin.analytics.analyticsFormat.uploadReceived') },
  get 'upload.publish'() { return t('admin.analytics.analyticsFormat.uploadPublished') },
  get 'upload.reject'() { return t('admin.analytics.analyticsFormat.uploadRejected') },
  get 'report.hide'() { return t('admin.analytics.analyticsFormat.reportHidden') },
  get 'report.restore'() { return t('admin.analytics.analyticsFormat.reportRestored') },
}
