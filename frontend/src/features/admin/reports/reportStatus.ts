import type { LampTone } from '../../../components/console'
import type { AdminReportItem } from '../../../lib/generated/adminApi'

// 與後端 app/services/visibility.py 的 REASON_MAX_CHARS 相同；這裡只是提早提示，後端仍會再驗一次。
export const REASON_MAX = 500

/** 研報在管理面的三種狀態。草稿＝上傳後尚未發布：一般頁面看不到（閱讀頁 404），隱藏／恢復也不適用（後端回 409）。 */
export type ReportStatus = 'published' | 'draft' | 'hidden'

/** 舊回應沒有 `publication` 欄位時視同已發布。 */
export function statusOf(r: Pick<AdminReportItem, 'hidden' | 'publication'>): ReportStatus {
  if (r.publication === 'draft') return 'draft'
  return r.hidden ? 'hidden' : 'published'
}

export const STATUS_LABEL: Record<ReportStatus, string> = { published: '已發布', draft: '草稿', hidden: '已隱藏' }
export const STATUS_TONE: Record<ReportStatus, LampTone> = { published: 'ok', draft: 'idle', hidden: 'bad' }

/** 草稿在上傳審核處理：沒有 file_hash → upload_id 的查詢端點，所以連到上傳清單的「待審草稿」分頁籤。 */
export const DRAFT_REVIEW_PATH = '/admin/uploads?tab=draft'

/** 錯誤物件 → 給人看的訊息。後端 400／403／404 的 detail 由 requestJSON 放進 message，原樣顯示。 */
export function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/** toast 用的短名稱：標題太長時截到 18 字。 */
export function short(name: string): string {
  return name.length > 18 ? `${name.slice(0, 18)}…` : name
}
