import type {
  FlagExportOverride, FlagImportChange, FlagItem, FlagOverrideView, FlagUpdateRequest,
} from '../../../lib/generated/adminApi'
import { t } from '@/i18n'

/** 功能開關頁的文字（純函式，測試直接驗）。後端語意在 app/services/feature_flags.py。 */

export const ROLE_LABELS: Record<'admin' | 'user', string> = { get admin() { return t('admin.flags.flagText.admins') }, get user() { return t('admin.flags.flagText.regularUsers') } }

export const EFFECTIVE_LABELS: Record<FlagItem['effective'], string> = {
  get on() { return t('admin.flags.flagText.onForEveryone') },
  get off() { return t('admin.flags.flagText.offForEveryone') },
  get scoped() { return t('admin.flags.flagText.onForSomeUsersOnly') },
}

/** 覆寫的一句話摘要；null＝沒有覆寫（用 registry 預設）。 */
export function overrideSummary(o: FlagOverrideView | null | undefined, defaultOn: boolean): string {
  if (!o) return t('admin.flags.flagText.noOverrideDefault', { v: defaultOn ? t('admin.flags.flagText.on') : t('admin.flags.flagText.off') })
  if (!o.enabled) return t('admin.flags.flagText.offForEveryone2')
  const roles = o.allow_roles ?? null
  const users = o.allow_users ?? null
  if (roles == null && users == null) return t('admin.flags.flagText.onForEveryone2')
  const parts: string[] = []
  if (roles != null) parts.push(roles.map((r) => ROLE_LABELS[r]).join(t('admin.flags.flagText.text')))
  if (users != null) parts.push(users.map((u) => u.username ?? t('admin.flags.flagText.deletedAccount', { slice: u.id.slice(0, 8) })).join(t('admin.flags.flagText.text')))
  return t('admin.flags.flagText.onForSelected', { parts: parts.join(t('admin.flags.flagText.text2')) })
}

/** 匯入預覽裡的前後值（使用者是帳號名稱）。 */
export function exportOverrideSummary(o: FlagExportOverride | null | undefined): string {
  if (!o) return t('admin.flags.flagText.noOverride')
  if (!o.enabled) return t('admin.flags.flagText.offForEveryone2')
  const roles = o.allow_roles ?? null
  const users = o.allow_users ?? null
  if (roles == null && users == null) return t('admin.flags.flagText.onForEveryone2')
  const parts: string[] = []
  if (roles != null) parts.push(t('admin.flags.flagText.roles', { roles: roles.join(t('admin.flags.flagText.text')) }))
  if (users != null) parts.push(t('admin.flags.flagText.users', { users: users.join(t('admin.flags.flagText.text')) }))
  return t('admin.flags.flagText.onForSelected', { parts: parts.join(t('admin.flags.flagText.text2')) })
}

export const IMPORT_ACTION_LABELS: Record<FlagImportChange['action'], string> = {
  get create() { return t('admin.flags.flagText.addOverride') },
  get update() { return t('admin.flags.flagText.updateOverride') },
  get delete() { return t('admin.flags.flagText.deleteOverrideBackToDefault') },
  get unchanged() { return t('admin.flags.flagText.unchanged') },
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
