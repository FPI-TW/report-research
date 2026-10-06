import type { AuditEntry } from '../../lib/adminSchemas'

/** 稽核 action → 中文。未知的 action（後端日後新增）原樣顯示代碼，不讓整列消失。 */
const ACTION_LABELS: Record<string, string> = {
  'user.create': '建立帳號',
  'user.set_role': '變更角色',
  'user.enable': '啟用帳號',
  'user.disable': '停用帳號',
  'user.reset_password': '重設密碼',
  'user.force_logout': '強制登出',
  'review.update': '處理待複核',
  'qa_content.read': '查看問答內容',
}

const ROLE_LABELS: Record<string, string> = { admin: '管理員', user: '一般使用者' }
const REVIEW_STATUS: Record<string, string> = { open: '待處理', resolved: '已處理', dismissed: '略過' }
const REVIEW_KIND: Record<string, string> = { faithfulness: '忠實度低分', feedback: '倒讚', extraction: '抽取品質' }
const VERIFICATION: Record<string, string> = { untested: '未驗證', passed: '通過', failed: '未通過' }

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
  if (entry.action === 'qa_content.read' && Array.isArray(d.kinds)) {
    const kinds = d.kinds.map(k => (typeof k === 'string' ? REVIEW_KIND[k] ?? k : '')).filter(Boolean)
    if (kinds.length) parts.push(kinds.join('、'))
  }
  if (!who && entry.target_id && entry.action !== 'review.update') parts.unshift(`${entry.target_type} ${entry.target_id}`)
  const via = str(d.via)
  if (via && via !== 'web') parts.push(via.startsWith('cli') ? '經指令列' : `經 ${via}`)
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
