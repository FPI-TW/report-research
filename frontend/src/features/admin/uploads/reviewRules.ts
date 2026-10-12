import type { LampTone } from '../../../components/console'
import type { AdminUpload } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import {
  REJECTABLE_STATES, failureLabel, graceRemaining, isRetryableFailure, type UploadState,
} from '../uploadLabels'

/** 狀態 → 燈號色。草稿是「等人處理」（warn），會自己往前走的用 info，終態且不需處理的用 idle。 */
const STATE_TONE: Record<UploadState, LampTone> = {
  quarantined: 'idle',
  scanning: 'info',
  clean: 'idle',
  processing: 'info',
  draft: 'warn',
  failed: 'bad',
  infected: 'bad',
  blocked: 'bad',
  published: 'ok',
  rejected: 'idle',
  duplicate: 'idle',
}

export function stateTone(state: string): LampTone {
  return (STATE_TONE as Record<string, LampTone>)[state] ?? 'idle'
}

/** 一筆上傳現在能做哪些審核動作。null＝這個狀態沒有這顆按鈕；其餘值說明能不能按與為什麼不能。 */
export interface ReviewOptions {
  publish: boolean
  /** busy＝系統正在掃描或處理，退回會 409 `upload_busy`。 */
  reject: 'ok' | 'busy' | null
  retry: 'ok' | 'not_retryable' | null
  unreject: 'ok' | 'expired' | 'purged' | null
  /** 退回寬限期的剩餘時間（只有 `unreject === 'ok'` 時有值）。 */
  grace: string | null
  /** 這個狀態要讓人知道的一句話（為什麼不能按、還剩多久、接下來去哪裡）。 */
  note: string | null
}

export const BUSY_HINT = '系統正在掃描或處理這份檔案，完成後才能退回。'
export const EXPIRED_HINT = '寬限期已過，等待系統清除，無法撤銷。'

type Target = Pick<AdminUpload, 'state' | 'failure_kind' | 'purge_after' | 'purged_at'>

/**
 * 審核動作的可用條件（清單的列動作與抽屜底部共用）。只是顯示層：`REJECTABLE_STATES`、
 * `RETRYABLE_FAILURE_KINDS` 與寬限期後端都會再判一次，狀態在別處變了就回 409。
 */
export function reviewOptions(upload: Target, now: number): ReviewOptions {
  const { state } = upload
  const opts: ReviewOptions = { publish: state === 'draft', reject: null, retry: null, unreject: null, grace: null, note: null }
  if ((REJECTABLE_STATES as readonly string[]).includes(state)) {
    opts.reject = 'ok'
  } else if (state === 'scanning' || state === 'processing') {
    opts.reject = 'busy'
    opts.note = BUSY_HINT
  }
  if (state === 'failed') {
    opts.retry = isRetryableFailure(upload.failure_kind) ? 'ok' : 'not_retryable'
    if (opts.retry !== 'ok') opts.note = notRetryableHint(upload.failure_kind)
  }
  if (state === 'rejected') {
    if (upload.purged_at) {
      opts.unreject = 'purged'
      opts.note = `已於 ${fmtDateTime(upload.purged_at)} 清除，無法撤銷。`
    } else {
      opts.grace = graceRemaining(upload.purge_after, now)
      opts.unreject = opts.grace ? 'ok' : 'expired'
      opts.note = opts.grace ? `寬限期還剩 ${opts.grace}，期間內可以撤銷退回。` : EXPIRED_HINT
    }
  }
  if (state === 'published') opts.note = '已發布的研報不能退回；如需下架，請到「研報管理」隱藏。'
  if (state === 'infected' || state === 'blocked') opts.note = '被攔截的檔案留在隔離區，不提供下載或預覽，也沒有可執行的動作。'
  if (state === 'duplicate') opts.note = '語料庫已有同一份檔案，這筆上傳不會再處理。'
  return opts
}

export function notRetryableHint(kind: string | null | undefined): string {
  return `「${failureLabel(kind)}」重試也不會成功，不能重試；可以退回。`
}
