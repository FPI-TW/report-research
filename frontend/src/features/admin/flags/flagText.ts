import type {
  FlagExportOverride, FlagImportChange, FlagItem, FlagOverrideView, FlagUpdateRequest,
} from '../../../lib/generated/adminApi'

/** 功能開關頁的文字（純函式，測試直接驗）。後端語意在 app/services/feature_flags.py。 */

export const ROLE_LABELS: Record<'admin' | 'user', string> = { admin: '管理員', user: '一般使用者' }

export const EFFECTIVE_LABELS: Record<FlagItem['effective'], string> = {
  on: '對所有人開啟',
  off: '對所有人關閉',
  scoped: '只對部分使用者開啟',
}

/** 覆寫的一句話摘要；null＝沒有覆寫（用 registry 預設）。 */
export function overrideSummary(o: FlagOverrideView | null | undefined, defaultOn: boolean): string {
  if (!o) return `沒有覆寫（預設${defaultOn ? '開啟' : '關閉'}）`
  if (!o.enabled) return '全站關閉'
  const roles = o.allow_roles ?? null
  const users = o.allow_users ?? null
  if (roles == null && users == null) return '全站開啟'
  const parts: string[] = []
  if (roles != null) parts.push(roles.map((r) => ROLE_LABELS[r]).join('、'))
  if (users != null) parts.push(users.map((u) => u.username ?? `（已刪除的帳號 ${u.id.slice(0, 8)}）`).join('、'))
  return `限定開啟：${parts.join('；')}`
}

/** 匯入預覽裡的前後值（使用者是帳號名稱）。 */
export function exportOverrideSummary(o: FlagExportOverride | null | undefined): string {
  if (!o) return '沒有覆寫'
  if (!o.enabled) return '全站關閉'
  const roles = o.allow_roles ?? null
  const users = o.allow_users ?? null
  if (roles == null && users == null) return '全站開啟'
  const parts: string[] = []
  if (roles != null) parts.push(`角色 ${roles.join('、')}`)
  if (users != null) parts.push(`使用者 ${users.join('、')}`)
  return `限定開啟：${parts.join('；')}`
}

export const IMPORT_ACTION_LABELS: Record<FlagImportChange['action'], string> = {
  create: '新增覆寫',
  update: '更新覆寫',
  delete: '刪除覆寫（回到預設）',
  unchanged: '不變',
}

/** 匯出檔名：report-mark-flags-<環境>-<YYYYMMDD>.json（台北時間的日期）。 */
export function exportFilename(environment: string | null | undefined, now: Date = new Date()): string {
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' })
    .format(now).replaceAll('-', '')
  const env = (environment ?? '').replace(/[^a-z0-9_-]/gi, '') || 'unknown'
  return `report-mark-flags-${env}-${day}.json`
}

export type FlagMode = 'on' | 'off' | 'scoped'
export type FlagRole = 'admin' | 'user'

/** 把表單狀態轉成 PUT 的 body；限定模式下兩個維度都沒勾回 null（呼叫端提示，不送出）。 */
export function buildUpdate(mode: FlagMode, roles: FlagRole[], users: string[], note: string): FlagUpdateRequest | null {
  const trimmed = note.trim() || null
  if (mode === 'on') return { enabled: true, allow_roles: null, allow_users: null, note: trimmed }
  if (mode === 'off') return { enabled: false, allow_roles: null, allow_users: null, note: trimmed }
  if (roles.length === 0 && users.length === 0) return null
  return {
    enabled: true,
    allow_roles: roles.length ? roles : null,
    allow_users: users.length ? users : null,
    note: trimmed,
  }
}
