import { ApiError } from '../../lib/api'
import type { AdminUpload } from '../../lib/generated/adminApi'

/**
 * 研報上傳的狀態、分頁籤、失敗類別與錯誤碼 → 中文。
 *
 * 狀態與失敗類別的詞彙以後端 `app/services/uploads.py` 為準（`STATES`、`FAILURE_KINDS`、
 * `RETRYABLE_FAILURE_KINDS`）；這裡只負責翻譯與分組。未知的值原樣顯示，不擋畫面——
 * 另一條 lane 會調整 worker 與重試規則，前端不該因為多一個字串就壞掉。
 */

export type UploadState = AdminUpload['state']

export const STATE_LABEL: Record<UploadState, string> = {
  quarantined: '隔離中（待掃描）',
  scanning: '掃描中',
  clean: '掃描通過（待處理）',
  processing: '處理中',
  draft: '待審草稿',
  failed: '處理失敗',
  infected: '偵測到惡意程式',
  blocked: '已攔截（無法完成掃描）',
  published: '已發布',
  rejected: '已退回',
  duplicate: '重複',
}

export function stateLabel(state: string | null | undefined): string {
  if (!state) return '—'
  return (STATE_LABEL as Record<string, string>)[state] ?? state
}

/** 還在佔用掃描與入庫產能、會自己往前走的狀態（= 後端 `IN_FLIGHT_STATES`）。有這些才需要輪詢。 */
export const IN_FLIGHT_STATES: readonly UploadState[] = ['quarantined', 'scanning', 'clean', 'processing']

export function isInFlight(state: string): boolean {
  return (IN_FLIGHT_STATES as readonly string[]).includes(state)
}

/** 可被退回的狀態（= 後端 `REJECTABLE_STATES`）；scanning／processing 中退回會 409 `upload_busy`。 */
export const REJECTABLE_STATES: readonly UploadState[] = ['draft', 'quarantined', 'clean', 'failed']

/** failed 之中可重試的類別（= 後端 `RETRYABLE_FAILURE_KINDS`）。後端仍會再判一次。 */
export const RETRYABLE_FAILURE_KINDS: readonly string[] = ['tag_failed', 'ingest_error', 'extract_timeout']

export type UploadTabKey = 'processing' | 'draft' | 'failed' | 'blocked' | 'published' | 'rejected' | 'duplicate'

export interface UploadTab { key: UploadTabKey; label: string; states: readonly UploadState[] }

/** 清單的分頁籤；每個分頁籤對應一組狀態（清單端點一次只收一種 state，分頁籤內逐一查再合併）。 */
export const UPLOAD_TABS: readonly UploadTab[] = [
  { key: 'processing', label: '處理中', states: IN_FLIGHT_STATES },
  { key: 'draft', label: '待審草稿', states: ['draft'] },
  { key: 'failed', label: '失敗', states: ['failed'] },
  { key: 'blocked', label: '已攔截', states: ['infected', 'blocked'] },
  { key: 'published', label: '已發布', states: ['published'] },
  { key: 'rejected', label: '已退回', states: ['rejected'] },
  { key: 'duplicate', label: '重複', states: ['duplicate'] },
]

export function tabOf(key: string | null | undefined): UploadTab {
  return UPLOAD_TABS.find(t => t.key === key) ?? UPLOAD_TABS[0]
}

export const FAILURE_LABEL: Record<string, string> = {
  extract_error: '抽取文字失敗',
  extract_timeout: '抽取文字逾時',
  scanned: '掃描影像 PDF，抽不出文字',
  admin_file: '檔名判定為行政文件',
  not_research: '判定不是研究報告或沒有市場',
  active_content: '含主動內容（JavaScript、嵌入檔等）',
  encrypted: '加密的 PDF',
  too_many_pages: '頁數超過上限',
  tag_failed: '標註失敗',
  tag_blocked: '標註被內容審查擋下',
  tag_truncated: '標註回應被截斷',
  ingest_error: '入庫失敗',
  hash_mismatch: '檔案內容與紀錄不符',
  llm_breaker: 'LLM 暫停中，延後處理',
}

export function failureLabel(kind: string | null | undefined): string {
  if (!kind) return '—'
  return FAILURE_LABEL[kind] ?? kind
}

export function isRetryableFailure(kind: string | null | undefined): boolean {
  return !!kind && RETRYABLE_FAILURE_KINDS.includes(kind)
}

const MIB = 1024 * 1024

/** 位元組 → 「1.4 MB」（MiB 計，與後端 `UPLOAD_MAX_BYTES` 的 25 MiB 同一套單位）。 */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (n < 1024) return `${n} B`
  if (n < MIB) return `${(n / 1024).toFixed(1)} KB`
  const mb = n / MIB
  return `${mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)} MB`
}

/** 秒數 → 「3 分鐘」「2 小時 5 分」「1 天 3 小時」。 */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} 秒`
  const mins = Math.floor(s / 60)
  if (mins < 60) return `${mins} 分鐘`
  const h = Math.floor(mins / 60)
  if (h < 24) return `${h} 小時 ${mins % 60} 分`
  return `${Math.floor(h / 24)} 天 ${h % 24} 小時`
}

/** 退回寬限期的剩餘時間；已過期回 null。 */
export function graceRemaining(purgeAfter: string | null | undefined, now: number): string | null {
  if (!purgeAfter) return null
  const ms = new Date(purgeAfter).getTime() - now
  if (Number.isNaN(ms) || ms <= 0) return null
  const mins = Math.ceil(ms / 60_000)
  const h = Math.floor(mins / 60)
  return h > 0 ? `${h} 小時 ${mins % 60} 分` : `${mins} 分`
}

// ── 收檔錯誤（POST /api/admin/uploads）──────────────────────────────────

const CORPUS_STATUS: Record<string, string> = {
  hidden: '目前已隱藏',
  draft: '是待審草稿',
  published: '已發布',
}

export interface UploadFailure {
  message: string
  /** 409 `upload_duplicate` 且已有進行中的上傳時，那一筆的 upload_id（畫面給連結）。 */
  uploadId?: string
  /** 413 回應帶的實際上限；呼叫端據此更新之後的預檢。 */
  maxBytes?: number
}

type ErrorBody = Record<string, unknown>

const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined)
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)

/** 收檔端點的錯誤回應 → 給人看的說明。body 是後端統一錯誤格式 `{detail, code, ...extra}`，拿不到時為 null。 */
export function uploadFailure(status: number, body: ErrorBody | null): UploadFailure {
  const code = str(body?.code)
  const detail = str(body?.detail)
  switch (code) {
    case 'uploads_disabled':
      return { message: '上傳功能尚未開啟，請聯絡系統管理員。' }
    case 'upload_too_large': {
      const maxBytes = num(body?.max_bytes)
      return { message: `檔案超過上限 ${maxBytes ? fmtBytes(maxBytes) : ''}`.trim() + '。', maxBytes }
    }
    case 'upload_not_pdf':
      return { message: `不是有效的 PDF：${detail ?? '伺服器檢查檔案內容未通過'}。請確認檔案完整、沒有損毀。` }
    case 'invalid_filename':
      return { message: `檔名不合法：${detail ?? '請改用一般檔名後再上傳'}` }
    case 'upload_duplicate': {
      const status_ = str(body?.status)
      if (body?.existing === 'upload') {
        return {
          message: `這份檔案已在上傳流程中（${stateLabel(status_)}），不需要重新上傳。`,
          uploadId: str(body?.upload_id),
        }
      }
      const where = status_ ? CORPUS_STATUS[status_] : undefined
      return { message: `語料庫已有這份研報${where ? `（${where}）` : ''}，不需要重新上傳。` }
    }
    case 'upload_known_infected':
      return { message: '這份檔案先前已被判定含有惡意程式，拒收。' }
    case 'upload_quota_exceeded': {
      const limit = num(body?.limit)
      const used = num(body?.used)
      if (body?.quota === 'in_flight') {
        return { message: `全站處理中的上傳已達上限${limit != null ? `（${limit} 份）` : ''}，請等處理完再上傳。` }
      }
      return {
        message: `已達每人每日上傳上限${limit != null ? `（${used ?? limit}／${limit} 份）` : ''}，請明天再上傳。`,
      }
    }
    case 'quarantine_unavailable':
      return { message: '隔離區暫時無法使用（磁碟空間不足或目錄異常），檔案沒有保留，請稍後再試或聯絡系統管理員。' }
  }
  if (status === 413) return { message: '檔案超過伺服器允許的大小。' } // 代理層（nginx）回的 413 不是 JSON
  if (status === 403) return { message: detail ?? '需要「研報管理」權限。' }
  if (status === 0) return { message: '網路連線中斷，上傳沒有完成，請重試。' }
  if (detail) return { message: detail }
  return { message: status >= 500 ? `伺服器暫時無法處理（HTTP ${status}），請稍後再試。` : `上傳失敗（HTTP ${status}）` }
}

// ── 審核動作錯誤（publish／reject／unreject／retry）────────────────────

const REVIEW_CODE_MESSAGE: Record<string, string> = {
  upload_state_conflict: '這筆上傳的狀態已經改變，這個動作目前不適用；畫面已重新整理。',
  upload_busy: '系統正在掃描或處理這份檔案，請等處理完再退回。',
  upload_published_use_hide: '已發布的研報不能退回；如需下架，請到「研報管理」隱藏。',
  upload_reject_expired: '寬限期已過或檔案已清除，無法撤銷退回。',
  upload_not_retryable: '這類失敗重試也不會成功，不能重試。',
  upload_active_conflict: '同一份檔案已有另一筆進行中的上傳，請先處理那一筆。',
  upload_report_missing: '語料裡找不到這份研報（資料不一致），請聯絡系統管理員。',
  invalid_reason: '退回必須填寫原因，最多 500 字。',
  validation_error: '退回必須填寫原因，最多 500 字。',
  not_found: '找不到這筆上傳紀錄（可能已被清除）。',
}

/** 審核動作的錯誤 → 中文說明。認得的錯誤碼用固定說法，其餘顯示後端的 detail。 */
export function reviewErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code && REVIEW_CODE_MESSAGE[err.code]) return REVIEW_CODE_MESSAGE[err.code]
    if (err.status === 403) return '需要「研報管理」權限。'
    return err.message || `操作失敗（HTTP ${err.status}）`
  }
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}
