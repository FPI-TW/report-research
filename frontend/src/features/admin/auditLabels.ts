import type { AuditEntry } from '../../lib/adminSchemas'
import { t } from '@/i18n'

/** 稽核 action → 中文。未知的 action（後端日後新增）原樣顯示代碼，不讓整列消失。 */
const ACTION_LABELS: Record<string, string> = {
  get 'user.create'() { return t('admin.auditLabels.createAccount') },
  get 'user.sso_link'() { return t('admin.auditLabels.bindCompanyAccount') },
  get 'user.set_role'() { return t('admin.auditLabels.changeRole') },
  get 'user.enable'() { return t('admin.auditLabels.enableAccount') },
  get 'user.disable'() { return t('admin.auditLabels.disableAccount') },
  get 'user.reset_password'() { return t('admin.auditLabels.resetPassword') },
  get 'user.force_logout'() { return t('admin.auditLabels.forceSignOut') },
  get 'user.set_privileges'() { return t('admin.auditLabels.adjustPermissions') },
  get 'user.totp_enable'() { return t('admin.auditLabels.turnOnTwoStepVerification') },
  get 'user.totp_disable'() { return t('admin.auditLabels.turnOffTwoStepVerification') },
  get 'user.totp_reset'() { return t('admin.auditLabels.resetTwoStepVerification') },
  get 'user.delete_requested'() { return t('admin.auditLabels.requestAccountDeletion') },
  get 'user.delete_cancelled'() { return t('admin.auditLabels.cancelAccountDeletion') },
  get 'user.delete_executed'() { return t('admin.auditLabels.executeAccountDeletion') },
  get 'user.delete_replayed'() { return t('admin.auditLabels.replayDeletionAfterRestore') },
  get 'review.update'() { return t('admin.auditLabels.handleReviewItem') },
  get 'qa_content.read'() { return t('admin.auditLabels.viewQAContent') },
  get 'report.hide'() { return t('admin.auditLabels.hideReport') },
  get 'report.restore'() { return t('admin.auditLabels.restoreReport') },
  get 'report.bulk_visibility'() { return t('admin.auditLabels.batchHideRestoreReports') },
  get 'user.bulk_action'() { return t('admin.auditLabels.batchAccountAction') },
  get 'data.export'() { return t('admin.auditLabels.exportCsv') },
  get 'session.elevate'() { return t('admin.auditLabels.reVerifyElevation') },
  get 'session.elevate_failed'() { return t('admin.auditLabels.reVerificationFailed') },
  get 'ops.restart'() { return t('admin.auditLabels.restartService') },
  get 'ops.run'() { return t('admin.auditLabels.runBatchNow') },
  // Admin v2（Wave 0 預先放好；稽核 action 名稱是穩定介面，各 lane 照用）
  get 'session.admin_revoke'() { return t('admin.auditLabels.revokeSession') },
  get 'flag.update'() { return t('admin.auditLabels.changeFeatureFlag') },
  get 'quota.update'() { return t('admin.auditLabels.adjustQuota') },
  get 'api_client.create'() { return t('admin.auditLabels.createApiClient') },
  get 'api_client.update'() { return t('admin.auditLabels.changeApiClientSettings') },
  get 'api_client.entitlements'() { return t('admin.auditLabels.changeApiClientAccessScope') },
  get 'api_client.rotate'() { return t('admin.auditLabels.rotateApiClientKey') },
  // 研報上傳：前五個是管理員在上傳頁的動作（`upload_intake.py`、`upload_review.py`），後四個是上傳 worker 自己寫的
  // （`upload_worker.py`，沒有操作者）。收檔／發布／退回的說法與使用分析（`analytics/analyticsFormat.ts`）一致。
  get 'upload.create'() { return t('admin.auditLabels.uploadReceived') },
  get 'upload.publish'() { return t('admin.auditLabels.uploadPublished') },
  get 'upload.reject'() { return t('admin.auditLabels.uploadRejected') },
  get 'upload.unreject'() { return t('admin.auditLabels.uploadRejectionUndone') },
  get 'upload.retry'() { return t('admin.auditLabels.uploadRetried') },
  get 'upload.blocked'() { return t('admin.auditLabels.uploadBlocked') },
  get 'upload.infected'() { return t('admin.auditLabels.malwareDetectedInUpload') },
  get 'upload.purge'() { return t('admin.auditLabels.purgeRejectedUpload') },
  get 'upload.evidence_purged'() { return t('admin.auditLabels.purgeRetainedFileOfBlocked') },
}

/**
 * 登入與安全事件（`research.auth_event`，不是稽核紀錄）→ 中文。詞彙的唯一定義是後端
 * `app/services/accounts.py` 的 `AUTH_EVENT_TYPES`；這裡沒有守門測試，未知的事件原樣顯示代碼。
 */
const AUTH_EVENT_LABELS: Record<string, string> = {
  get 'login.success'() { return t('admin.auditLabels.signInSucceeded') },
  get 'login.failure'() { return t('admin.auditLabels.signInFailed') },
  get 'login.totp_failure'() { return t('admin.auditLabels.twoStepVerificationFailed') },
  get 'login.locked'() { return t('admin.auditLabels.signInRateLimited') },
  get 'login.insecure'() { return t('admin.auditLabels.nonHttpsSignInRefused') },
  get logout() { return t('admin.auditLabels.signOut') },
  get 'elevate.success'() { return t('admin.auditLabels.reVerificationSucceeded') },
  get 'elevate.failure'() { return t('admin.auditLabels.reVerificationFailed') },
  get 'login.sso_failure'() { return t('admin.auditLabels.companyAccountSignInFailed') },
}
const AUTH_REASON_LABELS: Record<string, string> = {
  get password() { return t('admin.auditLabels.password') }, get totp() { return t('admin.auditLabels.twoStepVerification') }, get unknown_user() { return t('admin.auditLabels.accountDoesnTExist') }, get bad_password() { return t('admin.auditLabels.wrongPassword') }, get disabled() { return t('admin.auditLabels.accountDisabled') },
  get bad_code() { return t('admin.auditLabels.wrongVerificationCode') }, get expired_challenge() { return t('admin.auditLabels.verificationTimedOut') },
  get sso() { return t('admin.auditLabels.companyAccount') }, get sso_only() { return t('admin.auditLabels.companyAccountOnly') }, get state() { return t('admin.auditLabels.signInFlowExpired') }, get idp_error() { return t('admin.auditLabels.rejectedByIdp') }, get token() { return t('admin.auditLabels.invalidIdToken') },
  get claims() { return t('admin.auditLabels.missingRequiredInformation') }, get domain() { return t('admin.auditLabels.domainNotAllowed') }, get username_conflict() { return t('admin.auditLabels.usernameAlreadyTaken') }, get invalid_username() { return t('admin.auditLabels.invalidUsername') },
  get break_glass() { return t('admin.auditLabels.conflictsWithBreakGlassAdmin') }, get sso_stale_auth() { return t('admin.auditLabels.idpDidNotReAuthenticate') }, get sso_identity() { return t('admin.auditLabels.companyAccountMismatch') },
  get sso_session() { return t('admin.auditLabels.signInStateExpired') },
}

export function authEventLabel(event: string, reason?: string | null): string {
  const base = AUTH_EVENT_LABELS[event] ?? event
  return reason ? t('admin.auditLabels.text', { base, v: AUTH_REASON_LABELS[reason] ?? reason }) : base
}

const ROLE_LABELS: Record<string, string> = { get admin() { return t('admin.auditLabels.admin') }, get user() { return t('admin.auditLabels.regularUser') } }
const REVIEW_STATUS: Record<string, string> = { get open() { return t('admin.auditLabels.pending') }, get resolved() { return t('admin.auditLabels.handled') }, get dismissed() { return t('admin.auditLabels.skipped') } }
const REVIEW_KIND: Record<string, string> = { get faithfulness() { return t('admin.auditLabels.lowFaithfulness') }, get feedback() { return t('admin.auditLabels.thumbsDown') }, get extraction() { return t('admin.auditLabels.extractionQuality') } }
const VERIFICATION: Record<string, string> = { get untested() { return t('admin.auditLabels.untested') }, get passed() { return t('admin.auditLabels.passed') }, get failed() { return t('admin.auditLabels.failed') } }
const BULK_USER_VERBS: Record<string, string> = { get disable() { return t('admin.auditLabels.disable') }, get enable() { return t('admin.auditLabels.enable') }, get logout() { return t('admin.auditLabels.forceSignOut') } }
const EXPORT_KINDS: Record<string, string> = {
  get audit() { return t('admin.auditLabels.auditLog') }, get users() { return t('admin.auditLabels.accountList') }, get reports() { return t('admin.auditLabels.reportList') }, get incidents() { return t('admin.auditLabels.incidentList') }, get jobs() { return t('admin.auditLabels.scheduledJobs') },
}

// API 用戶端設定欄位 → 中文（`api_client.update` 的 detail.before／after 鍵；enabled 與 note 另外處理）。
const API_CLIENT_FIELDS: Record<string, string> = { get scopes() { return t('admin.auditLabels.endpoints') }, get rate_limit_per_min() { return t('admin.auditLabels.rateLimitPerMinute') }, get daily_quota() { return t('admin.auditLabels.dailyQuota') } }

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

/**
 * 操作者：actor 為 null 是 CLI（scripts/create_admin.py）或系統行為，不是「不知道是誰」。
 * 公司帳號（OIDC）首次登入的自動建帳與綁定（detail.via 為 sso_jit／sso）是系統行為。
 */
export function actorLabel(entry: AuditEntry): string {
  if (entry.actor_username) return entry.actor_username
  if (entry.actor_user_id == null && (entry.detail.via === 'sso_jit' || entry.detail.via === 'sso')) {
    return t('admin.auditLabels.systemCompanyAccountSignIn')
  }
  if (entry.actor_user_id == null) return t('admin.auditLabels.commandLineCli')
  return t('admin.auditLabels.deletedAccount')
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/** 一句話說明這筆操作的對象與內容；只讀已知的 detail 鍵，其餘忽略。 */
export function auditSummary(entry: AuditEntry): string {
  const d = entry.detail
  const parts: string[] = []
  const who = str(d.username)
  if (who) parts.push(who)
  if (entry.action === 'user.create' && str(d.role)) parts.push(roleLabel(str(d.role)!))
  if (entry.action === 'user.set_role' && str(d.from) && str(d.to)) {
    parts.push(`${roleLabel(str(d.from)!)} → ${roleLabel(str(d.to)!)}`)
  }
  if (typeof d.revoked_sessions === 'number' && d.revoked_sessions > 0) parts.push(t('admin.auditLabels.signedOutSessions', { revoked_sessions: d.revoked_sessions }))
  if (entry.action === 'review.update') {
    if (str(d.kind)) parts.push(REVIEW_KIND[str(d.kind)!] ?? str(d.kind)!)
    if (str(d.status)) parts.push(REVIEW_STATUS[str(d.status)!] ?? str(d.status)!)
    if (str(d.verification)) parts.push(VERIFICATION[str(d.verification)!] ?? str(d.verification)!)
  }
  if (entry.action === 'user.set_privileges') {
    const flag = d.is_super && typeof d.is_super === 'object' ? d.is_super as { from?: unknown; to?: unknown } : null
    if (flag && typeof flag.from === 'boolean' && typeof flag.to === 'boolean' && flag.from !== flag.to) {
      parts.push(flag.to ? t('admin.auditLabels.madeSuperAdmin') : t('admin.auditLabels.removedSuperAdmin'))
    }
    const scopes = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
    if (scopes(d.scopes_added).length) parts.push(t('admin.auditLabels.granted', { scopes_added: scopes(d.scopes_added).join(t('admin.auditLabels.text2')) }))
    if (scopes(d.scopes_removed).length) parts.push(t('admin.auditLabels.revoked', { scopes_removed: scopes(d.scopes_removed).join(t('admin.auditLabels.text2')) }))
  }
  // 研報隱藏／恢復：detail 只有檔名與「有沒有寫原因」，原因全文刻意不進稽核。
  if (entry.action === 'report.hide' || entry.action === 'report.restore') {
    if (str(d.file_name)) parts.push(str(d.file_name)!)
    else if (entry.target_id) parts.push(t('admin.auditLabels.report', { slice: entry.target_id.slice(0, 12) }))
    if (entry.action === 'report.hide' && d.has_reason === true) parts.push(t('admin.auditLabels.reasonGiven'))
  }
  if (entry.action === 'qa_content.read' && Array.isArray(d.kinds)) {
    const kinds = d.kinds.map(k => (typeof k === 'string' ? REVIEW_KIND[k] ?? k : '')).filter(Boolean)
    if (kinds.length) parts.push(kinds.join(t('admin.auditLabels.text2')))
  }
  // CSV 匯出：detail 只有種類、篩選條件、筆數與是否達上限（不含內容）。
  if (entry.action === 'data.export') {
    const kind = str(d.kind) ?? entry.target_id
    if (kind) parts.push(EXPORT_KINDS[kind] ?? kind)
    if (typeof d.row_count === 'number') parts.push(t('admin.auditLabels.rows', { row_count: d.row_count, v: d.truncated === true ? t('admin.auditLabels.limitReached') : '' }))
  }
  // 批次摘要：逐筆的變更各有自己的一列，這裡只說是哪個動作、變更幾筆、略過幾筆。
  const isBulk = entry.action === 'report.bulk_visibility' || entry.action === 'user.bulk_action'
  if (isBulk) {
    const verb = entry.action === 'report.bulk_visibility'
      ? (d.hidden === true ? t('admin.auditLabels.hide') : t('admin.auditLabels.restore'))
      : BULK_USER_VERBS[str(d.action) ?? ''] ?? str(d.action) ?? ''
    if (verb) parts.push(verb)
    if (typeof d.changed === 'number') parts.push(t('admin.auditLabels.changed', { changed: d.changed }))
    const skipped = d.skipped && typeof d.skipped === 'object'
      ? Object.values(d.skipped as Record<string, unknown>).reduce<number>((n, v) => n + (typeof v === 'number' ? v : 0), 0)
      : 0
    if (skipped) parts.push(t('admin.auditLabels.skipped2', { skipped }))
  }
  // API 用戶端：只講名稱與改了什麼。detail 的金鑰 prefix 刻意不顯示（原始金鑰與 hash 本來就不進稽核）。
  const isApiClient = entry.action.startsWith('api_client.')
  if (isApiClient) parts.push(...apiClientSummary(entry))
  // 研報上傳：每一種 action 的 detail 都帶原始檔名；講檔名，不顯示上傳代號（UUID）。
  const isUpload = entry.action.startsWith('upload.')
  if (isUpload) parts.push(str(d.file_name) ?? (entry.target_id ? t('admin.auditLabels.upload', { slice: entry.target_id.slice(0, 8) }) : t('admin.auditLabels.upload2')))
  const isReport = entry.action === 'report.hide' || entry.action === 'report.restore'
  if (!who && entry.target_id && entry.action !== 'review.update' && !isReport && !isBulk && !isApiClient && !isUpload && entry.action !== 'data.export') parts.unshift(`${entry.target_type} ${entry.target_id}`)
  const via = str(d.via)
  if (via === 'web_bulk') {
    if (!isBulk) parts.push(t('admin.auditLabels.batchAction'))
  } else if (via && via !== 'web') parts.push(via.startsWith('cli') ? t('admin.auditLabels.viaCli') : t('admin.auditLabels.via', { via }))
  return parts.join(t('common.separator')) || '—'
}

const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

function apiClientSummary(entry: AuditEntry): string[] {
  const d = entry.detail
  const parts = [str(d.name) ?? (entry.target_id ? t('admin.auditLabels.apiClient', { target_id: entry.target_id }) : t('admin.auditLabels.apiClient2'))]
  const obj = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
  const markets = (e: unknown) => strList(obj(e).market)
  if (entry.action === 'api_client.create') {
    const scopes = strList(d.scopes)
    parts.push(scopes.length ? t('admin.auditLabels.endpoints2', { scopes: scopes.join(t('admin.auditLabels.text2')) }) : t('admin.auditLabels.noEndpoints'))
    if (markets(d.entitlements).length) parts.push(t('admin.auditLabels.markets', { entitlements: markets(d.entitlements).join(t('admin.auditLabels.text2')) }))
  } else if (entry.action === 'api_client.update') {
    const before = obj(d.before)
    const after = obj(d.after)
    for (const key of Object.keys(after)) {
      if (key === 'enabled') {
        if (typeof after.enabled === 'boolean') parts.push(after.enabled ? t('admin.auditLabels.enable') : t('admin.auditLabels.disable'))
      } else if (key === 'note') {
        parts.push(t('admin.auditLabels.noteChanged'))
      } else {
        const show = (v: unknown) => (Array.isArray(v) ? strList(v).join(t('admin.auditLabels.text2')) || t('admin.auditLabels.none') : String(v ?? '—'))
        parts.push(`${API_CLIENT_FIELDS[key] ?? key} ${show(before[key])} → ${show(after[key])}`)
      }
    }
  } else if (entry.action === 'api_client.entitlements') {
    const was = markets(d.before).join(t('admin.auditLabels.text2'))
    const now = markets(d.after).join(t('admin.auditLabels.text2'))
    if (now && was !== now) parts.push(t('admin.auditLabels.markets2', { v: was || t('admin.auditLabels.none'), now }))
    const dims = ['source', 'report_type', 'instrument_type'] as const
    const changed = dims.filter(k => strList(obj(d.before)[k]).join('\n') !== strList(obj(d.after)[k]).join('\n'))
    const labels: Record<(typeof dims)[number], string> = { source: t('admin.auditLabels.brokers'), report_type: t('admin.auditLabels.reportTypes'), instrument_type: t('admin.auditLabels.assetClasses') }
    if (changed.length) parts.push(t('admin.auditLabels.changed2', { map: changed.map(k => labels[k]).join(t('admin.auditLabels.text2')) }))
  } else if (entry.action === 'api_client.rotate') {
    parts.push(t('admin.auditLabels.keyReplaced'))
  }
  return parts
}

/** ISO 時間 → `YYYY-MM-DD HH:mm`（本地時區）；null 顯示「—」。 */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
