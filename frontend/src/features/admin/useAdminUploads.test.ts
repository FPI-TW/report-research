import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import { graceRemaining, reviewErrorMessage, tabOf, uploadFailure } from './uploadLabels'
import { DEFAULT_MAX_BYTES, UploadAbortedError, UploadRequestError, precheck, uploadPdf } from './useAdminUploads'
import { FakeXHR, MiB, pdfFile, upload } from './uploadTestKit'

beforeEach(() => { FakeXHR.reset(); vi.stubGlobal('XMLHttpRequest', FakeXHR) })
afterEach(() => vi.unstubAllGlobals())

test('預檢：只收 .pdf（不分大小寫）、非空、不超過上限', () => {
  expect(precheck({ name: 'a.PDF', size: 10 }, DEFAULT_MAX_BYTES)).toBeNull()
  expect(precheck({ name: 'a.docx', size: 10 }, DEFAULT_MAX_BYTES)).toMatch('只接受 PDF')
  expect(precheck({ name: 'pdf', size: 10 }, DEFAULT_MAX_BYTES)).toMatch('只接受 PDF')
  expect(precheck({ name: 'a.pdf', size: 0 }, DEFAULT_MAX_BYTES)).toMatch('檔案是空的')
  expect(precheck({ name: 'a.pdf', size: 25 * MiB }, DEFAULT_MAX_BYTES)).toBeNull()
  expect(precheck({ name: 'a.pdf', size: 25 * MiB + 1 }, DEFAULT_MAX_BYTES)).toMatch('超過上限 25 MB')
  expect(precheck({ name: 'a.pdf', size: 11 * MiB }, 10 * MiB)).toMatch('超過上限 10 MB')
})

test('uploadPdf：raw body、Content-Type、檔名與 last_modified 帶在 query，回報上傳進度並解析 202', async () => {
  const file = pdfFile('台積電 2026Q3.pdf', { lastModified: 1_759_000_000_123 })
  const progress = vi.fn()
  const p = uploadPdf(file, { onProgress: progress })
  const xhr = FakeXHR.last()
  expect(xhr.method).toBe('POST')
  const url = new URL(xhr.url, 'http://x')
  expect(url.pathname).toBe('/api/admin/uploads')
  expect(url.searchParams.get('filename')).toBe('台積電 2026Q3.pdf')
  expect(url.searchParams.get('last_modified')).toBe('1759000000123')
  expect(xhr.headers['Content-Type']).toBe('application/pdf')
  expect(xhr.body).toBe(file)
  xhr.progress(50, 200)
  expect(progress).toHaveBeenCalledWith(50, 200)
  const row = upload({ upload_id: 'new-1' })
  xhr.respond(202, row)
  await expect(p).resolves.toMatchObject({ upload_id: 'new-1', state: 'quarantined' })
})

test('uploadPdf：錯誤碼轉成 UploadRequestError（帶中文說明與 413 的 max_bytes）', async () => {
  const p = uploadPdf(pdfFile())
  FakeXHR.last().respond(413, { detail: '檔案超過上限 10 MB', code: 'upload_too_large', max_bytes: 10 * MiB })
  const err = await p.catch(e => e)
  expect(err).toBeInstanceOf(UploadRequestError)
  expect(err.status).toBe(413)
  expect(err.code).toBe('upload_too_large')
  expect(err.failure.maxBytes).toBe(10 * MiB)
  expect(err.message).toBe('檔案超過上限 10 MB。')
})

test('uploadPdf：網路中斷與取消', async () => {
  const p1 = uploadPdf(pdfFile())
  FakeXHR.last().fail()
  await expect(p1).rejects.toThrow('網路連線中斷')
  const ctl = new AbortController()
  const p2 = uploadPdf(pdfFile(), { signal: ctl.signal })
  ctl.abort()
  await expect(p2).rejects.toBeInstanceOf(UploadAbortedError)
  expect(FakeXHR.last().aborted).toBe(true)
})

test('收檔錯誤碼對照', () => {
  expect(uploadFailure(503, { code: 'uploads_disabled', detail: '上傳功能尚未開放' }).message).toBe('上傳功能尚未開啟，請聯絡系統管理員。')
  expect(uploadFailure(415, { code: 'upload_not_pdf', detail: '檔案結尾沒有 %%EOF' }).message).toMatch('不是有效的 PDF：檔案結尾沒有 %%EOF')
  expect(uploadFailure(409, { code: 'upload_duplicate', existing: 'corpus', status: 'published' }).message)
    .toBe('語料庫已有這份研報（已發布），不需要重新上傳。')
  expect(uploadFailure(409, { code: 'upload_duplicate', existing: 'corpus', status: 'hidden' }).message).toMatch('目前已隱藏')
  expect(uploadFailure(409, { code: 'upload_duplicate', existing: 'corpus', status: 'draft' }).message).toMatch('是待審草稿')
  const inFlight = uploadFailure(409, { code: 'upload_duplicate', existing: 'upload', status: 'scanning', upload_id: 'u-9' })
  expect(inFlight.message).toBe('這份檔案已在上傳流程中（掃描中），不需要重新上傳。')
  expect(inFlight.uploadId).toBe('u-9')
  expect(uploadFailure(422, { code: 'upload_known_infected' }).message).toMatch('先前已被判定含有惡意程式')
  expect(uploadFailure(429, { code: 'upload_quota_exceeded', quota: 'daily', limit: 30, used: 30 }).message)
    .toBe('已達每人每日上傳上限（30／30 份），請明天再上傳。')
  expect(uploadFailure(429, { code: 'upload_quota_exceeded', quota: 'in_flight', limit: 50, used: 50 }).message)
    .toMatch('全站處理中的上傳已達上限（50 份）')
  expect(uploadFailure(503, { code: 'quarantine_unavailable' }).message).toMatch('隔離區暫時無法使用')
  expect(uploadFailure(413, null).message).toBe('檔案超過伺服器允許的大小。') // nginx 的 HTML 413
  expect(uploadFailure(502, null).message).toMatch('HTTP 502')
})

test('審核錯誤碼對照（409／422）', () => {
  const msg = (status: number, code: string) => reviewErrorMessage(new ApiError(status, `後端 ${code}`, code))
  expect(msg(409, 'upload_state_conflict')).toMatch('狀態已經改變')
  expect(msg(409, 'upload_busy')).toMatch('正在掃描或處理')
  expect(msg(409, 'upload_published_use_hide')).toMatch('研報管理')
  expect(msg(409, 'upload_reject_expired')).toMatch('寬限期已過')
  expect(msg(409, 'upload_not_retryable')).toMatch('不能重試')
  expect(msg(409, 'upload_active_conflict')).toMatch('另一筆進行中')
  expect(msg(422, 'validation_error')).toMatch('必須填寫原因')
  expect(msg(409, 'something_new')).toBe('後端 something_new') // 不認得的碼顯示後端 detail
})

test('寬限期剩餘時間與分頁籤', () => {
  const now = Date.parse('2026-10-07T00:00:00Z')
  expect(graceRemaining('2026-10-07T23:05:00Z', now)).toBe('23 小時 5 分')
  expect(graceRemaining('2026-10-07T00:10:00Z', now)).toBe('10 分')
  expect(graceRemaining('2026-10-06T00:00:00Z', now)).toBeNull()
  expect(tabOf('draft').states).toEqual(['draft'])
  expect(tabOf('blocked').states).toEqual(['infected', 'blocked'])
  expect(tabOf('nope').key).toBe('processing')
})
