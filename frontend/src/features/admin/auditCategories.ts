/**
 * 操作紀錄頁的分類篩選：稽核 action（`<領域>.<動作>`）→ 四個分類。分類只看 action 的前綴，
 * 後端日後新增的 action 不必回來改這裡（沒對到的一律歸「安全與設定」）。
 *
 * 這是前端自己的分組：`GET /api/admin/audit` 目前不支援依分類查詢，頁面只能篩已經載入的那一頁。
 * 動作的中文標籤在 `auditLabels.ts`，這裡不重複。
 */
export type AuditCategory = 'acct' | 'content' | 'ops' | 'sec'

export const AUDIT_CATEGORY_LABELS: Record<AuditCategory, string> = {
  acct: '帳號',
  content: '內容',
  ops: '維運',
  sec: '安全與設定',
}

export const AUDIT_CATEGORIES = Object.keys(AUDIT_CATEGORY_LABELS) as AuditCategory[]

const PREFIXES: readonly [string, AuditCategory][] = [
  ['user.', 'acct'],
  ['review.', 'content'], ['report.', 'content'], ['qa_content.', 'content'], ['upload.', 'content'],
  ['ops.', 'ops'],
]

/** session、旗標、配額、API 用戶端、匯出與沒見過的 action 都算「安全與設定」。 */
export function auditCategory(action: string): AuditCategory {
  return PREFIXES.find(([prefix]) => action.startsWith(prefix))?.[1] ?? 'sec'
}
