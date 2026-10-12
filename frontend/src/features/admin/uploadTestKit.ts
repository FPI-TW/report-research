/**
 * 上傳頁測試共用的假物件（只給 *.test.tsx 匯入，不進產品程式）：上傳紀錄與掃描器摘要的工廠、假 XHR。
 */
import type { AdminUpload, AdminUploadScanner } from '../../lib/generated/adminApi'

export const MiB = 1024 * 1024

let n = 0
export function upload(over: Partial<AdminUpload> = {}): AdminUpload {
  n += 1
  return {
    upload_id: `u-${n}`,
    file_hash: String(n).padStart(64, '0'),
    original_name: `report-${n}.pdf`,
    size_bytes: 1_400_000,
    client_mtime: '2026-10-01T00:00:00Z',
    uploaded_by: 'root',
    uploaded_at: `2026-10-06T0${n % 10}:00:00Z`,
    state: 'quarantined',
    state_changed_at: '2026-10-06T09:00:00Z',
    scan_attempts: 0,
    scan_engine: null,
    scan_signature: null,
    scanned_at: null,
    scan_last_error: null,
    process_attempts: 0,
    failure_kind: null,
    failure_detail: null,
    processed_at: null,
    decided_by: null,
    decided_at: null,
    decision_reason: null,
    purge_after: null,
    purged_at: null,
    ...over,
  }
}

export function scanner(over: Partial<AdminUploadScanner> = {}): AdminUploadScanner {
  return {
    pending: 0, scanning: 0, oldest_pending_at: null, oldest_pending_seconds: null, last_error: null, last_error_at: null,
    ...over,
  }
}

type Handler = ((this: FakeXHR) => void) | null

/** 最小的 XMLHttpRequest 替身：記下 open／header／send，測試再以 progress()／respond() 推進。 */
export class FakeXHR {
  static instances: FakeXHR[] = []
  method = ''
  url = ''
  headers: Record<string, string> = {}
  body: unknown = undefined
  status = 0
  responseText = ''
  aborted = false
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null }
  onload: Handler = null
  onerror: Handler = null
  onabort: Handler = null

  open(method: string, url: string) { this.method = method; this.url = url }
  setRequestHeader(k: string, v: string) { this.headers[k] = v }
  send(body: unknown) { this.body = body; FakeXHR.instances.push(this) }
  abort() { this.aborted = true; this.onabort?.call(this) }

  progress(loaded: number, total: number) { this.upload.onprogress?.({ lengthComputable: true, loaded, total }) }
  respond(status: number, body: unknown) {
    this.status = status
    this.responseText = typeof body === 'string' ? body : JSON.stringify(body)
    this.onload?.call(this)
  }
  fail() { this.onerror?.call(this) }

  static reset() { FakeXHR.instances = [] }
  static last(): FakeXHR { return FakeXHR.instances[FakeXHR.instances.length - 1] }
}

/** 假 PDF 檔；size 可覆寫（不必真的配置 25 MB 記憶體）。 */
export function pdfFile(name = 'a.pdf', opts: { size?: number; lastModified?: number } = {}): File {
  const file = new File(['%PDF-1.7\n%%EOF'], name, { type: 'application/pdf', lastModified: opts.lastModified ?? 1_759_000_000_000 })
  if (opts.size != null) Object.defineProperty(file, 'size', { value: opts.size })
  return file
}
