import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  adminApi, type QuotaItem, type QuotaOverrideRequest, type QuotaOverview, type QuotaStats,
} from '../../../lib/generated/adminApi'
import { AUDIT_KEY } from '../useAdmin'

export const QUOTA_KEY = ['admin', 'quota'] as const
export type QuotaKind = QuotaItem['kind']
export type QuotaMode = QuotaItem['mode']

/** 每人今日用量、覆寫與影子模式狀態（/api/admin/quota）。一分鐘自動重抓：計數隨問答即時變動。 */
export function useQuotaOverview() {
  return useQuery<QuotaOverview>({
    queryKey: [...QUOTA_KEY, 'overview'],
    queryFn: () => adminApi.getQuotaOverview(),
    refetchInterval: 60_000,
    retry: false,
  })
}

/** 最近 days 天每人每日需求的 P50／P95（/api/admin/quota/stats）。 */
export function useQuotaStats(days: number) {
  return useQuery<QuotaStats>({
    queryKey: [...QUOTA_KEY, 'stats', days],
    queryFn: () => adminApi.getQuotaStats({ days }),
    retry: false,
  })
}

/**
 * 設定／清除個人覆寫。後端要已提升：呼叫端以 `useElevationGate().guard` 包住 `mutateAsync`。
 * 成功後重抓總覽與稽核（同一筆交易寫了 `quota.update`）。
 */
export function useSetQuotaOverride() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, kind, ...body }: { userId: string; kind: QuotaKind } & QuotaOverrideRequest) =>
      adminApi.setQuotaOverride(userId, kind, body),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUOTA_KEY }),
        client.invalidateQueries({ queryKey: AUDIT_KEY }),
      ])
    },
  })
}
