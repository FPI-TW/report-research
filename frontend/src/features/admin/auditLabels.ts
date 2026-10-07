import type { AuditEntry } from '../../lib/adminSchemas'

/** 稽核 action → 中文。未知的 action（後端日後新增）原樣顯示代碼，不讓整列消失。 */
const ACTION_LABELS: Record<string, string> = {
  'user.create': '建立帳號',
  'user.set_role': '變更角色',
  'user.enable': '啟用帳號',
  'user.disable': '停用帳號',
  'user.reset_password': '重設密碼',
  'user.force_logout': '強制登出',
  'user.set_privileges': '調整權限',
  'user.totp_enable': '開啟兩步驟驗證',
  'user.totp_disable': '關閉兩步驟驗證',
  'user.totp_reset': '重設兩步驟驗證',
  'user.delete_requested': '提出刪除帳號',
  'user.delete_cancelled': '取消刪除帳號',
  'user.delete_executed': '執行刪除帳號',
  'user.delete_replayed': '重放刪除（還原後）',
  'review.update': '處理待複核',
  'qa_content.read': '查看問答內容',
  'report.hide': '隱藏研報',
  'report.restore': '恢復研報',
  'report.bulk_visibility': '批次隱藏／恢復研報',
  'user.bulk_action': '批次帳號操作',
  'data.export': '匯出 CSV',
}

const ROLE_LABELS: Record<string, string> = { admin: '管理員', user: '一般使用者' }
const REVIEW_STATUS: Record<string, string> = { open: '待處理', resolved: '已處理', dismissed: '略過' }
const REVIEW_KIND: Record<string, string> = { faithfulness: '忠實度低分', feedback: '倒讚', extraction: '抽取品質' }
const VERIFICATION: Record<string, string> = { untested: '未驗證', passed: '通過', failed: '未通過' }
const BULK_USER_VERBS: Record<string, string> = { disable: '停用', enable: '啟用', logout: '強制登出' }
const EXPORT_KINDS: Record<string, string> = {
  audit: '操作紀錄', users: '帳號清單', reports: '研報清單', incidents: '事件清單', jobs: '排程工作',
}

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

/** 操作者：actor 為 null 是 CLI（scripts/create_admin.py），不是「不知道是誰」。 */
export function actorLabel(entry: AuditEntry): string {
  if (entry.actor_username) return entry.actor_username
  if (entry.actor_user_id == null) return '指令列（CLI）'
  return '（已不存在的帳號）'
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
  if (typeof d.revoked_sessions === 'number' && d.revoked_sessions > 0) parts.push(`登出 ${d.revoked_sessions} 個 session`)
  if (entry.action === 'review.update') {
    if (str(d.kind)) parts.push(REVIEW_KIND[str(d.kind)!] ?? str(d.kind)!)
    if (str(d.status)) parts.push(REVIEW_STATUS[str(d.status)!] ?? str(d.status)!)
    if (str(d.verification)) parts.push(VERIFICATION[str(d.verification)!] ?? str(d.verification)!)
  }
  if (entry.action === 'user.set_privileges') {
    const flag = d.is_super && typeof d.is_super === 'object' ? d.is_super as { from?: unknown; to?: unknown } : null
    if (flag && typeof flag.from === 'boolean' && typeof flag.to === 'boolean' && flag.from !== flag.to) {
      parts.push(flag.to ? '設為 super admin' : '取消 super admin')
    }
    const scopes = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
    if (scopes(d.scopes_added).length) parts.push(`授予 ${scopes(d.scopes_added).join('、')}`)
    if (scopes(d.scopes_removed).length) parts.push(`收回 ${scopes(d.scopes_removed).join('、')}`)
  }
  // 研報隱藏／恢復：detail 只有檔名與「有沒有寫原因」，原因全文刻意不進稽核。
  if (entry.action === 'report.hide' || entry.action === 'report.restore') {
    if (str(d.file_name)) parts.push(str(d.file_name)!)
    else if (entry.target_id) parts.push(`研報 ${entry.target_id.slice(0, 12)}`)
    if (entry.action === 'report.hide' && d.has_reason === true) parts.push('已填原因')
  }
  if (entry.action === 'qa_content.read' && Array.isArray(d.kinds)) {
    const kinds = d.kinds.map(k => (typeof k === 'string' ? REVIEW_KIND[k] ?? k : '')).filter(Boolean)
    if (kinds.length) parts.push(kinds.join('、'))
  }
  // CSV 匯出：detail 只有種類、篩選條件、筆數與是否達上限（不含內容）。
  if (entry.action === 'data.export') {
    const kind = str(d.kind) ?? entry.target_id
    if (kind) parts.push(EXPORT_KINDS[kind] ?? kind)
    if (typeof d.row_count === 'number') parts.push(`${d.row_count} 筆${d.truncated === true ? '（達上限）' : ''}`)
  }
  // 批次摘要：逐筆的變更各有自己的一列，這裡只說是哪個動作、變更幾筆、略過幾筆。
  const isBulk = entry.action === 'report.bulk_visibility' || entry.action === 'user.bulk_action'
  if (isBulk) {
    const verb = entry.action === 'report.bulk_visibility'
      ? (d.hidden === true ? '隱藏' : '恢復')
      : BULK_USER_VERBS[str(d.action) ?? ''] ?? str(d.action) ?? ''
    if (verb) parts.push(verb)
    if (typeof d.changed === 'number') parts.push(`變更 ${d.changed} 筆`)
    const skipped = d.skipped && typeof d.skipped === 'object'
      ? Object.values(d.skipped as Record<string, unknown>).reduce<number>((n, v) => n + (typeof v === 'number' ? v : 0), 0)
      : 0
    if (skipped) parts.push(`略過 ${skipped} 筆`)
  }
  const isReport = entry.action === 'report.hide' || entry.action === 'report.restore'
  if (!who && entry.target_id && entry.action !== 'review.update' && !isReport && !isBulk && entry.action !== 'data.export') parts.unshift(`${entry.target_type} ${entry.target_id}`)
  const via = str(d.via)
  if (via === 'web_bulk') {
    if (!isBulk) parts.push('批次操作')
  } else if (via && via !== 'web') parts.push(via.startsWith('cli') ? '經指令列' : `經 ${via}`)
  return parts.join('・') || '—'
}

/** ISO 時間 → `YYYY-MM-DD HH:mm`（本地時區）；null 顯示「—」。 */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
