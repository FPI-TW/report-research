import type { QuotaItem } from '../../../lib/generated/adminApi'
import type { QuotaKind } from './useQuota'

export const KIND_LABELS: Record<QuotaKind, string> = { ask: '問答', export: '匯出', upload: '上傳' }
export const KINDS: QuotaKind[] = ['ask', 'export', 'upload']
// 與後端 web/routers/admin_quota.py 的 daily_limit 範圍相同；這裡只是提早提示，後端仍會再驗一次。
export const LIMIT_MAX = 100_000
export const REASON_MAX = 500

export const fmtInt = (n: number) => n.toLocaleString('zh-TW')

export function limitText(item: Pick<QuotaItem, 'limit'>): string {
  return item.limit == null ? '不限' : fmtInt(item.limit)
}

/** 錯誤物件 → 給人看的訊息。後端的 detail 由 requestJSON 放進 message，原樣顯示。 */
export function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}
