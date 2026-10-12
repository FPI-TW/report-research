import { authEventLabel } from '../auditLabels'

/** 告警狀態（`/healthz/security` 與 `/api/admin/security/alerts` 的 state）→ 中文。 */
export const STATE_LABELS: Record<string, string> = {
  ok: '正常',
  unknown: '判斷不出來',
  audit_chain_broken: '稽核鏈驗證失敗',
  elevate_failures: '權限提升失敗過多',
  account_failures: '有帳號連續登入失敗',
  login_failures: '全站登入失敗過多',
}

export const CATEGORY_LABELS: Record<string, string> = {
  privilege: '權限與角色',
  elevation: '權限提升',
  session: 'session 與停用',
  credential: '密碼與兩步驟驗證',
  deletion: '刪除帳號',
  ops: '維運操作',
  data_access: '問答原文與匯出',
  config: '旗標與配額',
}

export const ANCHOR_LABELS: Record<string, string> = {
  ok: '相符',
  tamper: '與先前錨點不符',
  error: '執行失敗',
  stale: '超過 48 小時沒有新的錨定',
  missing: '還沒有錨定紀錄',
  corrupt: '狀態檔讀不懂',
}

export const LIVE_LABELS: Record<string, string> = { ok: '完整', broken: '斷裂', error: '驗證失敗（DB 不可用？）' }

/** 事件類型選單（順序即顯示順序）。值與後端 `accounts.AUTH_EVENT_TYPES` 一致（產生的 client 型別守住）。 */
export const EVENT_OPTIONS = [
  'login.success', 'login.failure', 'login.totp_failure', 'login.locked', 'login.insecure',
  'logout', 'elevate.success', 'elevate.failure',
] as const

/** `bad_credentials` 是權限提升給了驗證碼卻失敗（服務層分不出是密碼還是驗證碼錯）。其餘沿用共用標籤。 */
export function eventLabel(event: string, reason?: string | null): string {
  if (reason === 'bad_credentials') return authEventLabel(event, null) + '（密碼或驗證碼錯誤）'
  return authEventLabel(event, reason)
}

/** 稽核或事件是告警狀態（需要人看）嗎。 */
export function isAlertState(state: string): boolean {
  return state !== 'ok' && state !== 'unknown'
}

/** UA 太長：表格只顯示前段，完整字串放 title。 */
export function shortUa(ua: string | null | undefined, max = 48): string {
  if (!ua) return '—'
  return ua.length > max ? `${ua.slice(0, max)}…` : ua
}

export function pct(part: number, total: number): string {
  if (total <= 0) return '—'
  return `${Math.round((part / total) * 100)}%`
}
