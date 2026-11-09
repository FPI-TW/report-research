import { authEventLabel } from '../auditLabels'
import { t } from '@/i18n'

/** 告警狀態（`/healthz/security` 與 `/api/admin/security/alerts` 的 state）→ 中文。 */
export const STATE_LABELS: Record<string, string> = {
  get ok() { return t('admin.security.securityLabels.ok') },
  get unknown() { return t('admin.security.securityLabels.undetermined') },
  get audit_chain_broken() { return t('admin.security.securityLabels.auditChainVerificationFailed') },
  get elevate_failures() { return t('admin.security.securityLabels.tooManyElevationFailures') },
  get account_failures() { return t('admin.security.securityLabels.anAccountHasConsecutiveSign') },
  get login_failures() { return t('admin.security.securityLabels.tooManySignInFailures') },
}

export const CATEGORY_LABELS: Record<string, string> = {
  get privilege() { return t('admin.security.securityLabels.permissionsAndRoles') },
  get elevation() { return t('admin.security.securityLabels.elevation') },
  get session() { return t('admin.security.securityLabels.sessionsAndDisabling') },
  get credential() { return t('admin.security.securityLabels.passwordsAndTwoStepVerification') },
  get deletion() { return t('admin.security.securityLabels.accountDeletion') },
  get ops() { return t('admin.security.securityLabels.operationsActions') },
  get data_access() { return t('admin.security.securityLabels.qAContentAndExports') },
  get config() { return t('admin.security.securityLabels.flagsAndQuotas') },
}

export const ANCHOR_LABELS: Record<string, string> = {
  get ok() { return t('admin.security.securityLabels.matches') },
  get tamper() { return t('admin.security.securityLabels.doesnTMatchThePrevious') },
  get error() { return t('admin.security.securityLabels.runFailed') },
  get stale() { return t('admin.security.securityLabels.noNewAnchorForOver') },
  get missing() { return t('admin.security.securityLabels.noAnchorRecordsYet') },
  get corrupt() { return t('admin.security.securityLabels.statusFileUnreadable') },
}

export const LIVE_LABELS: Record<string, string> = { get ok() { return t('admin.security.securityLabels.intact') }, get broken() { return t('admin.security.securityLabels.broken') }, get error() { return t('admin.security.securityLabels.verificationFailedDbUnavailable') } }

/** 事件類型選單（順序即顯示順序）。值與後端 `accounts.AUTH_EVENT_TYPES` 一致（產生的 client 型別守住）。 */
export const EVENT_OPTIONS = [
  'login.success', 'login.failure', 'login.totp_failure', 'login.locked', 'login.insecure',
  'logout', 'elevate.success', 'elevate.failure', 'login.sso_failure',
] as const

/** `bad_credentials` 是權限提升給了驗證碼卻失敗（服務層分不出是密碼還是驗證碼錯）。其餘沿用共用標籤。 */
export function eventLabel(event: string, reason?: string | null): string {
  if (reason === 'bad_credentials') return authEventLabel(event, null) + t('admin.security.securityLabels.wrongPasswordOrCode')
  return authEventLabel(event, reason)
}

/** 事件列的語氣色：失敗＝bad、被擋下的彙總列＝warn，其餘不上色。未知事件不上色。 */
export function eventTone(event: string): 'bad' | 'warn' | undefined {
  if (event === 'login.failure' || event === 'login.totp_failure' || event === 'elevate.failure'
    || event === 'login.sso_failure') return 'bad'
  if (event === 'login.locked' || event === 'login.insecure') return 'warn'
  return undefined
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
