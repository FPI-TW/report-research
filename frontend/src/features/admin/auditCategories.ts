/**
 * 操作紀錄頁的分類篩選：稽核 action（`<領域>.<動作>`）→ 四個分類。分類只看 action 的前綴，
 * 後端日後新增的 action 不必回來改這裡（沒對到的一律歸「安全與設定」）。
 *
 * 分組是前端自己的；查詢時把分類換成一組前綴交給後端篩（`GET /api/admin/audit` 的 `prefix`／
 * `exclude_prefix`，見 `auditCategoryQuery`），筆數與翻頁才是篩選後的。
 * 動作的中文標籤在 `auditLabels.ts`，這裡不重複。
 */
export type AuditCategory = 'acct' | 'content' | 'ops' | 'sec'
/** 頁面上的分類篩選：四個分類，或不篩。 */
export type AuditFilter = AuditCategory | 'all'

export const AUDIT_CATEGORY_LABELS: Record<AuditCategory, string> = {
  acct: '帳號',
  content: '內容',
  ops: '維運',
  sec: '安全與設定',
}

export const AUDIT_CATEGORIES = Object.keys(AUDIT_CATEGORY_LABELS) as AuditCategory[]

/** 有明確前綴的三類；「安全與設定」是其餘全部，沒有自己的前綴。 */
const CATEGORY_PREFIXES: Record<Exclude<AuditCategory, 'sec'>, readonly string[]> = {
  acct: ['user.'],
  content: ['review.', 'report.', 'qa_content.', 'upload.'],
  ops: ['ops.'],
}

const PREFIXES = (Object.entries(CATEGORY_PREFIXES) as [Exclude<AuditCategory, 'sec'>, readonly string[]][])
  .flatMap(([category, prefixes]) => prefixes.map(prefix => [prefix, category] as const))

/** session、旗標、配額、API 用戶端、匯出與沒見過的 action 都算「安全與設定」。 */
export function auditCategory(action: string): AuditCategory {
  return PREFIXES.find(([prefix]) => action.startsWith(prefix))?.[1] ?? 'sec'
}

/**
 * 分類 → 後端的前綴篩選。「安全與設定」沒有自己的前綴，是「排除其他三類的前綴」，
 * 所以後端沒見過的新 action 一樣落在這一類，和 `auditCategory` 的歸類一致。
 */
export function auditCategoryQuery(filter: AuditFilter): { prefix?: string[]; exclude_prefix?: string[] } {
  if (filter === 'all') return {}
  if (filter === 'sec') return { exclude_prefix: PREFIXES.map(([prefix]) => prefix) }
  return { prefix: [...CATEGORY_PREFIXES[filter]] }
}
