import { useQuery } from '@tanstack/react-query'
import {
  adminApi, type AnalyticsOperationsResponse, type AnalyticsOverviewResponse, type AnalyticsQualityResponse,
  type AnalyticsRoutesResponse, type AnalyticsTopResponse,
} from '../../../lib/generated/adminApi'

export const ANALYTICS_KEY = ['admin', 'analytics'] as const
export const TOP_LIMIT = 15

/** 範圍：台北日期（含兩端）；until 省略＝後端的今天。 */
export interface AnalyticsQuery {
  since: string
  until?: string
}

// 彙總資料一天才變一次（即時段也只是幾分鐘的差）：不自動重抓，後端另有 60 秒快取。
const common = { retry: false, staleTime: 60_000 } as const

export function useAnalyticsOverview(q: AnalyticsQuery) {
  return useQuery<AnalyticsOverviewResponse>({
    queryKey: [...ANALYTICS_KEY, 'overview', q],
    queryFn: () => adminApi.getAnalyticsOverview(q),
    ...common,
  })
}

export function useAnalyticsTop(q: AnalyticsQuery) {
  return useQuery<AnalyticsTopResponse>({
    queryKey: [...ANALYTICS_KEY, 'top', q],
    queryFn: () => adminApi.getAnalyticsTop({ ...q, limit: TOP_LIMIT }),
    ...common,
  })
}

export function useAnalyticsRoutes(q: AnalyticsQuery) {
  return useQuery<AnalyticsRoutesResponse>({
    queryKey: [...ANALYTICS_KEY, 'routes', q],
    queryFn: () => adminApi.getAnalyticsRoutes(q),
    ...common,
  })
}

export function useAnalyticsQuality(q: AnalyticsQuery) {
  return useQuery<AnalyticsQualityResponse>({
    queryKey: [...ANALYTICS_KEY, 'quality', q],
    queryFn: () => adminApi.getAnalyticsQuality(q),
    ...common,
  })
}

export function useAnalyticsOperations(q: AnalyticsQuery) {
  return useQuery<AnalyticsOperationsResponse>({
    queryKey: [...ANALYTICS_KEY, 'operations', q],
    queryFn: () => adminApi.getAnalyticsOperations(q),
    ...common,
  })
}
