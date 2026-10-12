import type { QuotaItem, QuotaUserRow } from '../../../lib/generated/adminApi'
import { KIND_LABELS, KINDS, LIMIT_MAX } from './quotaLabels'
import type { QuotaKind, QuotaMode } from './useQuota'

export interface QuotaChange { kind: QuotaKind; mode: QuotaMode; daily_limit: number | null; reason: string | null }

export type Draft = Record<QuotaKind, { mode: QuotaMode; limit: string }>
type Current = Pick<QuotaItem, 'mode' | 'limit' | 'reason'>

const NO_OVERRIDE: Current = { mode: 'default', limit: null, reason: null }
export const currentOf = (user: QuotaUserRow, kind: QuotaKind): Current => user.items.find(i => i.kind === kind) ?? NO_OVERRIDE

export function draftOf(user: QuotaUserRow): Draft {
  const entry = (kind: QuotaKind) => {
    const cur = currentOf(user, kind)
    return { mode: cur.mode, limit: cur.mode === 'limit' && cur.limit != null ? String(cur.limit) : '' }
  }
  return { ask: entry('ask'), export: entry('export'), upload: entry('upload') }
}

/**
 * 表單 → 要送出的覆寫。後端是一個類別一筆 PUT，所以只送真的有變的類別，備註寫在這次送出的類別上。
 * 上限都沒動、只填了備註時，更新這個帳號現有覆寫的備註（否則改備註就得先把上限改掉再改回來）。
 * 比對的對象是畫面上最新的資料：前一筆成功、後一筆失敗時，重按儲存只會送還沒成功的那幾筆。
 */
export function planChanges(user: QuotaUserRow, draft: Draft, note: string): { changes: QuotaChange[]; error: string | null } {
  const reason = note.trim() || null
  const changed: QuotaChange[] = []
  const noteOnly: QuotaChange[] = []
  for (const kind of KINDS) {
    const cur = currentOf(user, kind)
    const d = draft[kind]
    let limit: number | null = null
    if (d.mode === 'limit') {
      limit = Number(d.limit)
      if (d.limit.trim() === '' || !Number.isInteger(limit) || limit < 0 || limit > LIMIT_MAX) {
        return { changes: [], error: `${KIND_LABELS[kind]}的每日上限必須是 0–${LIMIT_MAX} 的整數` }
      }
    }
    const change: QuotaChange = { kind, mode: d.mode, daily_limit: limit, reason: d.mode === 'default' ? null : reason }
    if (d.mode !== cur.mode || (d.mode === 'limit' && limit !== cur.limit)) changed.push(change)
    else if (d.mode !== 'default' && reason && reason !== (cur.reason ?? null)) noteOnly.push(change)
  }
  return { changes: changed.length ? changed : noteOnly, error: null }
}
