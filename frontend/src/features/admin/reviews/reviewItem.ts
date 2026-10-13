import type { LampTone } from '../../../components/console'
import { displayTitle } from '../../../lib/displayTitle'
import type { ReviewItem, ReviewKind, ReviewStatus, ReviewVerification } from '../../../lib/reviewSchemas'
import { reasonText } from '../reviewReasons'

export type StatusFilter = ReviewStatus | 'all'

export const KIND_TABS: readonly { value: ReviewKind; label: string }[] = [
  { value: 'faithfulness', label: '忠實度低分' },
  { value: 'feedback', label: '倒讚' },
  { value: 'extraction', label: '抽取品質' },
]

export const STATUS_LABEL: Record<ReviewStatus, string> = { open: '待處理', resolved: '已處理', dismissed: '略過' }
export const VERIFY_LABEL: Record<ReviewVerification, string> = { untested: '未驗證', passed: '通過', failed: '未通過' }
export const STATUS_TONE: Record<ReviewStatus, LampTone> = { open: 'warn', resolved: 'ok', dismissed: 'idle' }

export const STATUS_FILTERS: readonly { value: StatusFilter; label: string }[] = [
  { value: 'open', label: STATUS_LABEL.open },
  { value: 'resolved', label: STATUS_LABEL.resolved },
  { value: 'dismissed', label: STATUS_LABEL.dismissed },
  { value: 'all', label: '全部' },
]

/** 以現行門檻已不需複核的研報：說出來，不留一格空白。 */
export const CLEARED_TEXT = '現行門檻下已達標'
export const CLEARED_HINT = '以現行門檻已不需複核；重跑該篇回填即會解除標記'

export function fmtDay(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : '—'
}

/** 這一筆在 URL（`?review=`）與儲存端點用的 id：問答是 qa_id，研報是 report_id。 */
export function itemId(kind: ReviewKind, item: ReviewItem): string {
  return (kind === 'extraction' ? item.report_id : item.qa_id) ?? ''
}

/** 問答沒有原文可顯示，以 qa_id 前 8 碼辨識（與稽核紀錄的 target_id 對得上）；研報用顯示標題。 */
export function subjectOf(kind: ReviewKind, item: ReviewItem): string {
  if (kind === 'extraction') return displayTitle(item)
  return `問答 ${(item.qa_id ?? '').slice(0, 8) || '—'}`
}

/** 共用帳號時期的舊提問沒有擁有者（null），標「共用帳號」而不是留白，免得被讀成資料缺漏。 */
export function askerText(item: ReviewItem): string {
  return item.asker_code ? `#${item.asker_code}` : '共用帳號'
}

/** 清單第二行：問答是提問者與日期，研報是券商與被標記的原因（帶量到的值）。 */
export function metaLine(kind: ReviewKind, item: ReviewItem): string {
  if (kind !== 'extraction') return `提問者 ${askerText(item)}・${fmtDay(item.created_at)}`
  const reasons = (item.review_reasons ?? []).map(r => reasonText(item, r))
  return [item.source, reasons.length > 0 ? reasons.join('、') : CLEARED_TEXT].filter(Boolean).join('・')
}

/** 門檻至少印兩位小數（0.9 → 0.90）；設得更細的（0.875）原樣印，不四捨五入成另一個門檻。 */
function fmtThreshold(value: number): string {
  const fixed = value.toFixed(2)
  return Number(fixed) === value ? fixed : String(value)
}

/** 每個分頁的一句說明。門檻取 API 回的 `min_score`，沒有就不寫。 */
export function hintText(kind: ReviewKind, minScore: number | null): string {
  switch (kind) {
    case 'faithfulness':
      return `近 30 天內抽查分數低於門檻的回答，最低分在前；只列現行判定尺量的分數${minScore != null ? `（門檻 ${fmtThreshold(minScore)}）` : ''}。`
    case 'feedback':
      return '近 30 天內使用者按了倒讚的回答。'
    case 'extraction':
      return '抽取品質標為 needs_review 的研報（照樣入庫、可檢索），分數最低在前。'
  }
}
