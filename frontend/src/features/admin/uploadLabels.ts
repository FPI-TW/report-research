import { ApiError } from '../../lib/api'
import type { AdminUpload } from '../../lib/generated/adminApi'
import { t } from '@/i18n'

/**
 * 研報上傳的狀態、分頁籤、失敗類別與錯誤碼 → 中文。
 *
 * 狀態與失敗類別的詞彙以後端 `app/services/uploads.py` 為準（`STATES`、`FAILURE_KINDS`、
 * `RETRYABLE_FAILURE_KINDS`）；這裡只負責翻譯與分組。未知的值原樣顯示，不擋畫面——
 * 另一條 lane 會調整 worker 與重試規則，前端不該因為多一個字串就壞掉。
 */

export type UploadState = AdminUpload['state']

export const STATE_LABEL: Record<UploadState, string> = {
  get quarantined() { return t('admin.uploadLabels.quarantinedAwaitingScan') },
  get scanning() { return t('admin.uploadLabels.scanning') },
  get clean() { return t('admin.uploadLabels.scanPassedAwaitingProcessing') },
  get processing() { return t('admin.uploadLabels.processing') },
  get draft() { return t('admin.uploadLabels.draftAwaitingReview') },
  get failed() { return t('admin.uploadLabels.processingFailed') },
  get infected() { return t('admin.uploadLabels.malwareDetected') },
  get blocked() { return t('admin.uploadLabels.blockedScanCouldnTComplete') },
  get published() { return t('admin.uploadLabels.published') },
  get rejected() { return t('admin.uploadLabels.rejected') },
  get duplicate() { return t('admin.uploadLabels.duplicate') },
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
  { key: 'processing', get label() { return t('admin.uploadLabels.processing') }, states: IN_FLIGHT_STATES },
  { key: 'draft', get label() { return t('admin.uploadLabels.draftAwaitingReview') }, states: ['draft'] },
  { key: 'failed', get label() { return t('admin.uploadLabels.failed') }, states: ['failed'] },
  { key: 'blocked', get label() { return t('admin.uploadLabels.blocked') }, states: ['infected', 'blocked'] },
  { key: 'published', get label() { return t('admin.uploadLabels.published') }, states: ['published'] },
  { key: 'rejected', get label() { return t('admin.uploadLabels.rejected') }, states: ['rejected'] },
  { key: 'duplicate', get label() { return t('admin.uploadLabels.duplicate') }, states: ['duplicate'] },
]

export function tabOf(key: string | null | undefined): UploadTab {
  return UPLOAD_TABS.find(item => item.key === key) ?? UPLOAD_TABS[0]
}

export const FAILURE_LABEL: Record<string, string> = {
  get extract_error() { return t('admin.uploadLabels.textExtractionFailed') },
  get extract_timeout() { return t('admin.uploadLabels.textExtractionTimedOut') },
  get scanned() { return t('admin.uploadLabels.scannedImagePdfNoText') },
  get admin_file() { return t('admin.uploadLabels.fileNameIndicatesAnAdministrative') },
  get not_research() { return t('admin.uploadLabels.judgedNotAResearchReport') },
  get active_content() { return t('admin.uploadLabels.containsActiveContentJavascriptEmbedded') },
  get encrypted() { return t('admin.uploadLabels.encryptedPdf') },
  get too_many_pages() { return t('admin.uploadLabels.tooManyPages') },
  get tag_failed() { return t('admin.uploadLabels.taggingFailed') },
  get tag_blocked() { return t('admin.uploadLabels.taggingBlockedByContentModeration') },
  get tag_truncated() { return t('admin.uploadLabels.taggingResponseTruncated') },
  get ingest_error() { return t('admin.uploadLabels.ingestionFailed') },
  get hash_mismatch() { return t('admin.uploadLabels.fileContentDoesnTMatch') },
  get scan_heuristic() { return t('admin.uploadLabels.blockedByScanningRulesE') },
  get llm_breaker() { return t('admin.uploadLabels.llmPausedProcessingDeferred') },
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
  if (s < 60) return t('admin.uploadLabels.s', { s })
  const mins = Math.floor(s / 60)
  if (mins < 60) return t('admin.uploadLabels.min', { mins })
  const h = Math.floor(mins / 60)
  if (h < 24) return t('admin.uploadLabels.hMin', { h, v: mins % 60 })
  return t('admin.uploadLabels.daysH', { floor: Math.floor(h / 24), v: h % 24 })
}

/** 退回寬限期的剩餘時間；已過期回 null。 */
export function graceRemaining(purgeAfter: string | null | undefined, now: number): string | null {
  if (!purgeAfter) return null
  const ms = new Date(purgeAfter).getTime() - now
  if (Number.isNaN(ms) || ms <= 0) return null
  const mins = Math.ceil(ms / 60_000)
  const h = Math.floor(mins / 60)
  return h > 0 ? t('admin.uploadLabels.hMin', { h, v: mins % 60 }) : t('admin.uploadLabels.min2', { mins })
}

// ── 收檔錯誤（POST /api/admin/uploads）──────────────────────────────────

const CORPUS_STATUS: Record<string, string> = {
  get hidden() { return t('admin.uploadLabels.currentlyHidden') },
  get draft() { return t('admin.uploadLabels.aDraftAwaitingReview') },
  get published() { return t('admin.uploadLabels.published') },
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
      return { message: t('admin.uploadLabels.uploadingIsnTEnabledYet') }
    case 'upload_too_large': {
      const maxBytes = num(body?.max_bytes)
      return { message: t('admin.uploadLabels.fileExceedsTheLimit', { v: maxBytes ? fmtBytes(maxBytes) : '' }).trim() + t('admin.uploadLabels.text'), maxBytes }
    }
    case 'upload_not_pdf':
      return { message: t('admin.uploadLabels.notAValidPdfCheck', { v: detail ?? t('admin.uploadLabels.theServerSContentCheck') }) }
    case 'invalid_filename':
      return { message: t('admin.uploadLabels.invalidFileName', { v: detail ?? t('admin.uploadLabels.useARegularFileName') }) }
    case 'upload_duplicate': {
      const status_ = str(body?.status)
      if (body?.existing === 'upload') {
        return {
          message: t('admin.uploadLabels.thisFileIsAlreadyIn', { status_: stateLabel(status_) }),
          uploadId: str(body?.upload_id),
        }
      }
      const where = status_ ? CORPUS_STATUS[status_] : undefined
      return { message: t('admin.uploadLabels.theCorpusAlreadyHasThis', { v: where ? t('admin.uploadLabels.text2', { where }) : '' }) }
    }
    case 'upload_known_infected':
      return { message: t('admin.uploadLabels.thisFileWasPreviouslyFound') }
    case 'upload_quota_exceeded': {
      const limit = num(body?.limit)
      const used = num(body?.used)
      if (body?.quota === 'in_flight') {
        return { message: t('admin.uploadLabels.siteWideInProgressUploads', { v: limit != null ? t('admin.uploadLabels.files', { limit }) : '' }) }
      }
      return {
        message: t('admin.uploadLabels.thePerUserDailyUpload', { v: limit != null ? t('admin.uploadLabels.files2', { v: used ?? limit, limit }) : '' }),
      }
    }
    case 'quarantine_unavailable':
      return { message: t('admin.uploadLabels.theQuarantineIsTemporarilyUnavailable') }
  }
  if (status === 413) return { message: t('admin.uploadLabels.theFileExceedsTheSize') } // 代理層（nginx）回的 413 不是 JSON
  if (status === 403) return { message: detail ?? t('admin.uploadLabels.requiresTheReportsPermission') }
  if (status === 0) return { message: t('admin.uploadLabels.theNetworkConnectionDroppedAnd') }
  if (detail) return { message: detail }
  return { message: status >= 500 ? t('admin.uploadLabels.theServerCanTHandle', { status }) : t('admin.uploadLabels.uploadFailedHttp', { status }) }
}

// ── 審核動作錯誤（publish／reject／unreject／retry）────────────────────

const REVIEW_CODE_MESSAGE: Record<string, string> = {
  get upload_state_conflict() { return t('admin.uploadLabels.thisUploadSStateHas') },
  get upload_busy() { return t('admin.uploadLabels.theSystemIsScanningOr') },
  get upload_published_use_hide() { return t('admin.uploadLabels.publishedReportsCanTBe') },
  get upload_reject_expired() { return t('admin.uploadLabels.theGracePeriodHasPassed') },
  get upload_not_retryable() { return t('admin.uploadLabels.retryingWonTFixThis') },
  get upload_active_conflict() { return t('admin.uploadLabels.anotherUploadOfTheSame') },
  get upload_report_missing() { return t('admin.uploadLabels.thisReportIsnTIn') },
  get invalid_reason() { return t('admin.uploadLabels.rejectingRequiresAReasonOf') },
  get validation_error() { return t('admin.uploadLabels.rejectingRequiresAReasonOf') },
  get not_found() { return t('admin.uploadLabels.uploadRecordNotFoundIt') },
}

/** 審核動作的錯誤 → 中文說明。認得的錯誤碼用固定說法，其餘顯示後端的 detail。 */
export function reviewErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code && REVIEW_CODE_MESSAGE[err.code]) return REVIEW_CODE_MESSAGE[err.code]
    if (err.status === 403) return t('admin.uploadLabels.requiresTheReportsPermission')
    return err.message || t('admin.uploadLabels.actionFailedHttp', { status: err.status })
  }
  return err instanceof Error && err.message ? err.message : t('admin.uploadLabels.actionFailedTryAgain')
}
