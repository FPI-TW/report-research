import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adminApi, type AdminReportItem, type AdminReportListResponse } from '../../lib/generated/adminApi'
import { AUDIT_KEY } from './useAdmin'

export const REPORTS_KEY = ['admin', 'reports'] as const
export const REPORTS_PAGE_SIZE = 50

/** 狀態篩選：全部／已發布（沒被隱藏）／草稿（上傳後尚未發布）／已隱藏。 */
export type StatusFilter = 'all' | 'published' | 'draft' | 'hidden'

/** 狀態篩選 → 後端的 `hidden`／`publication`。草稿不能被隱藏，所以「已隱藏」不必再限定發布狀態。 */
export function statusQuery(status: StatusFilter): { hidden?: boolean; publication?: 'draft' | 'published' } {
  switch (status) {
    case 'published': return { hidden: false, publication: 'published' }
    case 'draft': return { publication: 'draft' }
    case 'hidden': return { hidden: true }
    default: return {}
  }
}

export function useAdminReports(params: { q: string; status: StatusFilter; offset: number }) {
  return useQuery<AdminReportListResponse>({
    queryKey: [...REPORTS_KEY, params],
    queryFn: () => adminApi.listReports({
      q: params.q.trim() || undefined,
      ...statusQuery(params.status),
      limit: REPORTS_PAGE_SIZE,
      offset: params.offset,
    }),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

const FILE_HASH = /^[0-9a-f]{64}$/

/**
 * 單筆研報（`GET /api/admin/reports/{file_hash}`）：`?report=` 指的那一篇不在目前這一頁的清單時用
 * （清單的 `q` 不比對 hash）。與清單一樣含隱藏的與草稿；不存在是 404（`ApiError.status`）。
 * 格式不對的 hash 不送出（後端只會回 422）。掛在 `REPORTS_KEY` 底下：隱藏、恢復之後一起重抓。
 */
export function useAdminReport(fileHash: string | null, enabled: boolean) {
  const valid = fileHash != null && FILE_HASH.test(fileHash)
  return useQuery<AdminReportItem>({
    queryKey: [...REPORTS_KEY, 'one', fileHash],
    queryFn: () => adminApi.getReport(fileHash as string),
    enabled: enabled && valid,
    retry: false,
  })
}

export interface ReportCounts { total: number; hidden: number; draft: number }

/**
 * 頁首的三個數字（語料、已隱藏、上傳草稿）。後端沒有彙總端點，各用清單端點取 `total`（`limit=1`）。
 * 掛在 `REPORTS_KEY` 底下：隱藏、恢復、發布之後既有的 invalidate 會順帶重抓。
 */
export function useReportCounts() {
  return useQuery<ReportCounts>({
    queryKey: [...REPORTS_KEY, 'counts'],
    queryFn: async () => {
      const [all, hidden, draft] = await Promise.all([
        adminApi.listReports({ limit: 1 }),
        adminApi.listReports({ hidden: true, limit: 1 }),
        adminApi.listReports({ publication: 'draft', limit: 1 }),
      ])
      return { total: all.total, hidden: hidden.total, draft: draft.total }
    },
    staleTime: 60_000,
    retry: false,
  })
}

/** 隱藏（必填原因）／恢復。成功後重抓清單與稽核：後端在同一筆交易裡改旗標並寫稽核。 */
export function useReportVisibility() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ fileHash, hidden, reason }: { fileHash: string; hidden: boolean; reason?: string }) =>
      adminApi.setReportVisibility(fileHash, hidden ? { hidden, reason } : { hidden }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: REPORTS_KEY }),
        client.invalidateQueries({ queryKey: AUDIT_KEY }),
      ])
    },
  })
}

/** 批次隱藏（必填原因）／恢復；回逐筆結果。成功後同樣重抓清單與稽核。 */
export function useBulkReportVisibility() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ fileHashes, hidden, reason }: { fileHashes: string[]; hidden: boolean; reason?: string }) =>
      adminApi.bulkSetReportVisibility(hidden
        ? { action: 'hide', file_hashes: fileHashes, reason }
        : { action: 'restore', file_hashes: fileHashes }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: REPORTS_KEY }),
        client.invalidateQueries({ queryKey: AUDIT_KEY }),
      ])
    },
  })
}
