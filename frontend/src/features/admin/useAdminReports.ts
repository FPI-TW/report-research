import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adminApi, type AdminReportListResponse } from '../../lib/generated/adminApi'
import { AUDIT_KEY } from './useAdmin'

export const REPORTS_KEY = ['admin', 'reports'] as const
export const REPORTS_PAGE_SIZE = 50

/** 隱藏篩選：全部／只看顯示中／只看已隱藏（對應後端 `hidden` 省略／false／true）。 */
export type HiddenFilter = 'all' | 'visible' | 'hidden'

export function useAdminReports(params: { q: string; hidden: HiddenFilter; offset: number }) {
  return useQuery<AdminReportListResponse>({
    queryKey: [...REPORTS_KEY, params],
    queryFn: () => adminApi.listReports({
      q: params.q.trim() || undefined,
      hidden: params.hidden === 'all' ? undefined : params.hidden === 'hidden',
      limit: REPORTS_PAGE_SIZE,
      offset: params.offset,
    }),
    placeholderData: keepPreviousData,
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
