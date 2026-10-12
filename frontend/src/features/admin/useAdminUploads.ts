import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { redirectToLogin } from '../../lib/api'
import {
  adminApi, adminRawUploads, AdminUploadSchema,
  type AdminUpload, type AdminUploadDetail, type AdminUploadFile, type AdminUploadPreview, type AdminUploadScanner,
} from '../../lib/generated/adminApi'
import { REPORTS_KEY } from './useAdminReports'
import { AUDIT_KEY } from './useAdmin'
import { fmtBytes, isInFlight, uploadFailure, type UploadFailure, type UploadTab } from './uploadLabels'

/**
 * 研報上傳的資料層：收檔（手寫 XHR）、清單／詳情／預覽／原檔的查詢、審核動作。
 *
 * - **收檔用 XHR 而不是 fetch**：fetch 至今拿不到「上傳」進度（只有下載進度），25 MB 的檔案沒有進度條
 *   會像當掉。產生的 client 只給網址、Content-Type 與回應 schema（`adminRawUploads.createUpload`），送檔在這裡。
 * - **CSRF**：後端 `web/csrf.py` 只比對 `Origin` 與 `Host`，沒有 token。同源 XHR 的 POST 由瀏覽器自動帶
 *   `Origin`（程式也設不了）、session cookie 同源預設就會帶，所以跟 `lib/api.ts` 的 fetch（`credentials:
 *   'same-origin'`）一樣不必另加 header；唯一要自己設的是 `Content-Type: application/pdf`。
 * - **大小上限**：預設 25 MiB（= 後端 `UPLOAD_MAX_BYTES` 預設值）。後端沒有查上限的端點，所以只要收到一次
 *   413 `upload_too_large`，就改用回應裡的 `max_bytes` 預檢之後的檔案。前端預檢只是省流量，擋人的是後端。
 */

export const UPLOADS_KEY = ['admin', 'uploads'] as const
/** 有處理中的項目時的重抓間隔。worker 每 5 分鐘一輪，掃描與入庫的狀態變化以秒到分鐘計，5 秒足夠即時。 */
export const POLL_MS = 5_000
/** 預覽還在等標題／摘要／摘錄時的重抓間隔（批次另外跑，分鐘級）。 */
export const PREVIEW_POLL_MS = 15_000
/** 每個狀態最多取幾筆（清單端點上限 200）。上傳量是每人每日 30 份的量級，分頁籤內不再分頁。 */
export const TAB_LIMIT = 200
export const DEFAULT_MAX_BYTES = 25 * 1024 * 1024

// ── 收檔 ────────────────────────────────────────────────────────────────

/** 前端預檢：副檔名 .pdf、非空、不超過上限。通過回 null，否則回給人看的原因。 */
export function precheck(file: { name: string; size: number }, maxBytes: number): string | null {
  if (!/\.pdf$/i.test(file.name.trim())) return '只接受 PDF 檔（副檔名 .pdf）。'
  if (file.size <= 0) return '檔案是空的。'
  if (file.size > maxBytes) return `檔案 ${fmtBytes(file.size)}，超過上限 ${fmtBytes(maxBytes)}。`
  return null
}

export class UploadRequestError extends Error {
  status: number
  code?: string
  failure: UploadFailure
  constructor(status: number, code: string | undefined, failure: UploadFailure) {
    super(failure.message)
    this.name = 'UploadRequestError'
    this.status = status
    this.code = code
    this.failure = failure
  }
}

export class UploadAbortedError extends Error {
  constructor() {
    super('已取消上傳')
    this.name = 'UploadAbortedError'
  }
}

function parseJson(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text)
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
  } catch {
    return null // 非 JSON（代理層錯誤頁）：只剩狀態碼可說
  }
}

/** 送一份 PDF 到 `POST /api/admin/uploads`（raw body）。成功回 202 的上傳紀錄；失敗拋 `UploadRequestError`。 */
export function uploadPdf(
  file: File,
  opts: { onProgress?: (loaded: number, total: number) => void; signal?: AbortSignal } = {},
): Promise<AdminUpload> {
  const spec = adminRawUploads.createUpload
  return new Promise((resolve, reject) => {
    if (opts.signal?.aborted) { reject(new UploadAbortedError()); return }
    const xhr = new XMLHttpRequest()
    const onAbortSignal = () => xhr.abort()
    opts.signal?.addEventListener('abort', onAbortSignal)
    const done = () => opts.signal?.removeEventListener('abort', onAbortSignal)
    xhr.open(spec.method, spec.url({
      filename: file.name,
      last_modified: Number.isFinite(file.lastModified) && file.lastModified > 0 ? Math.floor(file.lastModified) : undefined,
    }))
    xhr.setRequestHeader('Content-Type', spec.contentType)
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total)
    }
    xhr.onload = () => {
      done()
      const body = parseJson(xhr.responseText)
      if (xhr.status === 401) {
        redirectToLogin()
        reject(new UploadRequestError(401, undefined, { message: '登入已過期，請重新登入。' }))
        return
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        const parsed = AdminUploadSchema.safeParse(body)
        if (parsed.success) resolve(parsed.data)
        else reject(new UploadRequestError(xhr.status, undefined, { message: '伺服器回應格式不符，請重新整理後確認是否已上傳。' }))
        return
      }
      const code = typeof body?.code === 'string' ? body.code : undefined
      reject(new UploadRequestError(xhr.status, code, uploadFailure(xhr.status, body)))
    }
    xhr.onerror = () => { done(); reject(new UploadRequestError(0, undefined, uploadFailure(0, null))) }
    xhr.onabort = () => { done(); reject(new UploadAbortedError()) }
    xhr.send(file)
  })
}

export type QueueStatus = 'queued' | 'uploading' | 'done' | 'error' | 'skipped' | 'cancelled'

export interface QueueItem {
  id: string
  name: string
  size: number
  status: QueueStatus
  /** 0–1；只有 uploading／done 有意義 */
  progress: number
  message?: string
  /** 成功時是新紀錄；409 進行中重複時是既有那一筆 */
  uploadId?: string
}

let seq = 0
const nextId = () => `q${Date.now().toString(36)}_${seq++}`

/**
 * 多檔上傳佇列：預檢不過的直接標成 skipped；其餘**逐一**送出（一次只傳一份，避免同時佔滿頻寬、
 * 也讓配額與重複檢查按順序生效）。離開頁面時中止進行中的上傳並清掉排隊中的檔案。
 */
export function useUploadQueue(opts: { onUploaded?: (u: AdminUpload) => void } = {}) {
  const [items, setItems] = useState<QueueItem[]>([])
  const [maxBytes, setMaxBytes] = useState(DEFAULT_MAX_BYTES)
  const maxBytesRef = useRef(DEFAULT_MAX_BYTES)
  const pendingRef = useRef<{ id: string; file: File }[]>([])
  const runningRef = useRef(false)
  const abortRef = useRef<{ id: string; ctl: AbortController } | null>(null)
  const aliveRef = useRef(true)
  const onUploadedRef = useRef(opts.onUploaded)
  useEffect(() => { onUploadedRef.current = opts.onUploaded })

  const patch = useCallback((id: string, p: Partial<QueueItem>) => {
    if (!aliveRef.current) return
    setItems(prev => prev.map(it => (it.id === id ? { ...it, ...p } : it)))
  }, [])

  const run = useCallback(async () => {
    if (runningRef.current) return
    runningRef.current = true
    try {
      for (let next = pendingRef.current.shift(); next && aliveRef.current; next = pendingRef.current.shift()) {
        const { id, file } = next
        // 排隊期間若學到更小的上限（前一份 413），送出前再預檢一次。
        const late = precheck(file, maxBytesRef.current)
        if (late) { patch(id, { status: 'skipped', message: late }); continue }
        const ctl = new AbortController()
        abortRef.current = { id, ctl }
        patch(id, { status: 'uploading', progress: 0 })
        try {
          const row = await uploadPdf(file, {
            signal: ctl.signal,
            onProgress: (loaded, total) => patch(id, { progress: total > 0 ? loaded / total : 0 }),
          })
          patch(id, { status: 'done', progress: 1, uploadId: row.upload_id, message: '已送進隔離區，等待掃描。' })
          onUploadedRef.current?.(row)
        } catch (err) {
          if (err instanceof UploadAbortedError) {
            patch(id, { status: 'cancelled', message: '已取消上傳。' })
          } else if (err instanceof UploadRequestError) {
            const learned = err.failure.maxBytes
            if (learned && learned > 0) { maxBytesRef.current = learned; if (aliveRef.current) setMaxBytes(learned) }
            patch(id, { status: 'error', message: err.failure.message, uploadId: err.failure.uploadId })
          } else {
            patch(id, { status: 'error', message: err instanceof Error ? err.message : '上傳失敗' })
          }
        } finally {
          abortRef.current = null
        }
      }
    } finally {
      runningRef.current = false
    }
  }, [patch])

  const addFiles = useCallback((files: Iterable<File>) => {
    const added: QueueItem[] = []
    for (const file of files) {
      const id = nextId()
      const problem = precheck(file, maxBytesRef.current)
      added.push({ id, name: file.name, size: file.size, status: problem ? 'skipped' : 'queued', progress: 0, message: problem ?? undefined })
      if (!problem) pendingRef.current.push({ id, file })
    }
    if (added.length === 0) return
    setItems(prev => [...prev, ...added])
    void run()
  }, [run])

  const cancel = useCallback((id: string) => {
    if (abortRef.current?.id === id) { abortRef.current.ctl.abort(); return }
    const before = pendingRef.current.length
    pendingRef.current = pendingRef.current.filter(p => p.id !== id)
    if (pendingRef.current.length !== before) patch(id, { status: 'cancelled', message: '已取消上傳。' })
  }, [patch])

  /** 清掉已結束（成功、失敗、略過、取消）的列；排隊與上傳中的留著。 */
  const clearFinished = useCallback(() => {
    setItems(prev => prev.filter(it => it.status === 'queued' || it.status === 'uploading'))
  }, [])

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      pendingRef.current = []
      abortRef.current?.ctl.abort()
    }
  }, [])

  const busy = items.some(it => it.status === 'queued' || it.status === 'uploading')
  return { items, maxBytes, busy, addFiles, cancel, clearFinished }
}

// ── 查詢 ────────────────────────────────────────────────────────────────

export interface UploadTabData {
  items: AdminUpload[]
  total: number
  /** 任一狀態超過 TAB_LIMIT 筆：畫面提示只列最新的部分 */
  truncated: boolean
  scanner: AdminUploadScanner
}

export const tabKey = (tab: UploadTab['key']) => [...UPLOADS_KEY, 'list', tab] as const
export const detailKey = (id: string) => [...UPLOADS_KEY, 'detail', id] as const

/**
 * 一個分頁籤的清單：清單端點一次只收一種 `state`，分頁籤內每種狀態各查一次再合併（上傳新→舊）。
 * 清單裡有處理中的項目才每 `POLL_MS` 重抓；全部走到終態或待審就停（`refetchInterval` 回 false）。
 */
export function useUploadTab(tab: UploadTab) {
  return useQuery<UploadTabData>({
    queryKey: tabKey(tab.key),
    queryFn: async () => {
      const pages = await Promise.all(tab.states.map(state => adminApi.listUploads({ state, limit: TAB_LIMIT })))
      const items = pages.flatMap(p => p.items)
        .sort((a, b) => (a.uploaded_at < b.uploaded_at ? 1 : a.uploaded_at > b.uploaded_at ? -1 : 0))
      return {
        items,
        total: pages.reduce((n, p) => n + p.total, 0),
        truncated: pages.some(p => p.has_more),
        scanner: pages[0].scanner,
      }
    },
    refetchInterval: q => (q.state.data?.items.some(it => isInFlight(it.state)) ? POLL_MS : false),
    retry: false,
  })
}

/** 一筆上傳的詳情；還在處理中就輪詢，走到草稿、失敗或終態後停。 */
export function useUpload(uploadId: string) {
  return useQuery<AdminUploadDetail>({
    queryKey: detailKey(uploadId),
    queryFn: () => adminApi.getUpload(uploadId),
    refetchInterval: q => (q.state.data && isInFlight(q.state.data.state) ? POLL_MS : false),
    retry: false,
  })
}

/** 內容預覽（只限 draft／published）。標題、摘要、摘錄還在產生時慢速輪詢。 */
export function useUploadPreview(uploadId: string, enabled: boolean) {
  return useQuery<AdminUploadPreview>({
    queryKey: [...detailKey(uploadId), 'preview'],
    queryFn: () => adminApi.previewUpload(uploadId),
    enabled,
    refetchInterval: q => {
      const d = q.state.data
      if (!d || d.upload.state !== 'draft') return false
      const pending = d.title_state === 'pending' || d.summary_state === 'pending' || d.takeaways_state === 'pending'
      return pending ? PREVIEW_POLL_MS : false
    },
    retry: false,
  })
}

/** 原檔的短效 presign。網址有效期內不重抓（提早一分鐘過期），404＝此環境沒有可預覽的原檔。 */
export function useUploadFile(uploadId: string, enabled: boolean) {
  return useQuery<AdminUploadFile>({
    queryKey: [...detailKey(uploadId), 'file'],
    queryFn: () => adminApi.getUploadFile(uploadId),
    enabled,
    staleTime: q => Math.max(0, ((q.state.data?.expires_in ?? 0) - 60) * 1000),
    refetchOnWindowFocus: false,
    retry: false,
  })
}

// ── 審核動作 ────────────────────────────────────────────────────────────

export type UploadAction = 'publish' | 'reject' | 'unreject' | 'retry'

/**
 * 發布／退回／撤銷退回／重試。成功**或失敗**都重抓上傳的清單與詳情（409 代表畫面上的狀態已過時），
 * 另外重抓研報清單（發布改了 publication）與操作紀錄（後端同一筆交易寫稽核）。
 */
export function useUploadAction() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ action, uploadId, reason }: { action: UploadAction; uploadId: string; reason?: string }) => {
      switch (action) {
        case 'publish': return adminApi.publishUpload(uploadId)
        case 'reject': return adminApi.rejectUpload(uploadId, { reason: reason ?? '' })
        case 'unreject': return adminApi.unrejectUpload(uploadId)
        case 'retry': return adminApi.retryUpload(uploadId)
      }
    },
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: UPLOADS_KEY }),
        client.invalidateQueries({ queryKey: REPORTS_KEY }),
        client.invalidateQueries({ queryKey: AUDIT_KEY }),
      ])
    },
  })
}
