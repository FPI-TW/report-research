import { useCallback, useSyncExternalStore } from 'react'
import { notifyManager, useQuery, useQueryClient, type Query } from '@tanstack/react-query'
import { auditPageSchema, type AuditPage } from '../../../lib/adminSchemas'
import { requestJSON } from '../../../lib/api'
import {
  adminApi, type AnalyticsOverviewResponse, type OpsDependencyGraph, type TotpAdoptionResponse,
} from '../../../lib/generated/adminApi'
import { sinceForDays } from '../analytics/analyticsFormat'
import { ANALYTICS_KEY } from '../analytics/useAnalytics'
import { OPS_KEY } from '../ops/useOps'
import { SECURITY_KEY } from '../security/useSecurity'
import { AUDIT_KEY } from '../useAdmin'
import { UPLOADS_KEY } from '../useAdminUploads'

/*
 * 總覽自己的資料來源（燈號那一組在 `useAdminSignals`，這裡不重複）。每個來源只在有對應 scope 時才查；
 * 輪詢一律不比導覽燈號密（一分鐘），失敗不重試——區塊自己顯示「讀不到」。
 * 依賴圖、2FA 採用率與所在頁面共用 query key：同一份快取。
 */
const MINUTE = 60_000

export const RECENT_AUDIT_SIZE = 6
/** 用量趨勢的天數（含今天）。分析 API 最細只到「日」，沒有每小時的粒度。 */
export const USAGE_DAYS = 14

/** 依賴圖：總覽只用其中的外部節點（狀態磚與服務卡下方的外部依賴）。 */
export function useOverviewDependencies(enabled: boolean) {
  return useQuery<OpsDependencyGraph>({
    queryKey: [...OPS_KEY, 'dependencies'],
    queryFn: () => adminApi.getOpsDependencies(),
    enabled, refetchInterval: MINUTE, retry: false,
  })
}

/** 上傳處理失敗的筆數（只要 total，所以 limit 1）。 */
export function useFailedUploads(enabled: boolean) {
  return useQuery({
    queryKey: [...UPLOADS_KEY, 'count', 'failed'],
    queryFn: () => adminApi.listUploads({ state: 'failed', limit: 1 }),
    enabled, refetchInterval: MINUTE, staleTime: MINUTE, retry: false,
    select: d => d.total,
  })
}

export function useOverviewTotp(enabled: boolean) {
  return useQuery<TotpAdoptionResponse>({
    queryKey: [...SECURITY_KEY, 'totp-adoption'],
    queryFn: () => adminApi.securityTotpAdoption(),
    enabled, refetchInterval: 5 * MINUTE, retry: false,
  })
}

/** 近 USAGE_DAYS 天的用量彙總（只有總量，不設 k 門檻的那一組）。彙總資料不自動重抓，與使用分析頁相同。 */
export function useOverviewUsage(enabled: boolean) {
  const since = sinceForDays(USAGE_DAYS)
  return useQuery<AnalyticsOverviewResponse>({
    queryKey: [...ANALYTICS_KEY, 'overview', { since }],
    queryFn: () => adminApi.getAnalyticsOverview({ since }),
    enabled, staleTime: MINUTE, retry: false,
  })
}

/** 最近幾筆管理操作。掛在 AUDIT_KEY 底下：各頁的寫入成功後重抓稽核紀錄時，這裡一起更新。 */
export function useRecentAudit(enabled: boolean) {
  return useQuery<AuditPage>({
    queryKey: [...AUDIT_KEY, 'recent', RECENT_AUDIT_SIZE],
    queryFn: () => requestJSON(`/api/admin/audit?limit=${RECENT_AUDIT_SIZE}&offset=0`, auditPageSchema, { cache: 'no-store' }),
    enabled, refetchInterval: MINUTE, retry: false,
  })
}

// 「這一頁用到的 query」＝目前有元件掛著的 query（總覽各區塊＋外殼的導覽燈號，兩者本來就共用快取）。
// 登入身分（/api/me）不算：它不是這一頁的資料，重抓只會讓整個外殼閃一下。
const onPage = (q: Query) => q.isActive() && q.queryKey[0] !== 'me'

/**
 * 頁首的「更新於」與「重新整理」。更新時間取這一頁所有 query 裡最近一次成功取得資料的時間；
 * 重新整理把它們全部重取一次（失敗的來源照樣由各區塊自己顯示）。
 */
export function useOverviewRefresh(): { updatedAt: number; refresh: () => Promise<void> } {
  const client = useQueryClient()
  const cache = client.getQueryCache()
  const updatedAt = useSyncExternalStore(
    useCallback((onChange: () => void) => cache.subscribe(notifyManager.batchCalls(onChange)), [cache]),
    () => cache.findAll({ predicate: onPage }).reduce((max, q) => Math.max(max, q.state.dataUpdatedAt), 0),
  )
  const refresh = useCallback(() => client.refetchQueries({ predicate: onPage }), [client])
  return { updatedAt, refresh }
}
