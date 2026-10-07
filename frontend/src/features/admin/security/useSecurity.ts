import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  adminApi,
  type AuditChainStateResponse,
  type AuthEventListResponse,
  type HighRiskResponse,
  type SecurityAlertsResponse,
  type SecuritySessionListResponse,
  type SuspiciousIpResponse,
  type TotpAdoptionResponse,
} from '../../../lib/generated/adminApi'
import { AUDIT_KEY } from '../useAdmin'

export const SECURITY_KEY = ['admin', 'security'] as const
export const EVENTS_PAGE_SIZE = 50
export const TIMELINE_PAGE_SIZE = 50

export type AuthEventType = NonNullable<NonNullable<Parameters<typeof adminApi.securityEvents>[0]>['event']>
export type HighRiskCategory = NonNullable<NonNullable<Parameters<typeof adminApi.securityHighRisk>[0]>['category']>

/** 安全頁的資料都是唯讀聚合；告警狀態每分鐘自動更新一次（管理面維持 polling，不做 SSE）。 */
export function useSecurityAlerts() {
  return useQuery<SecurityAlertsResponse>({
    queryKey: [...SECURITY_KEY, 'alerts'],
    queryFn: () => adminApi.securityAlerts(),
    refetchInterval: 60_000,
    retry: false,
  })
}

export function useAuditChainState() {
  return useQuery<AuditChainStateResponse>({
    queryKey: [...SECURITY_KEY, 'audit-chain'],
    queryFn: () => adminApi.securityAuditChain(),
    retry: false,
  })
}

export function useTotpAdoption() {
  return useQuery<TotpAdoptionResponse>({
    queryKey: [...SECURITY_KEY, 'totp-adoption'],
    queryFn: () => adminApi.securityTotpAdoption(),
    retry: false,
  })
}

export function useAuthEvents(params: { event: AuthEventType | ''; ip: string; beforeId: number | null }) {
  return useQuery<AuthEventListResponse>({
    queryKey: [...SECURITY_KEY, 'events', params],
    queryFn: () => adminApi.securityEvents({
      event: params.event || undefined,
      ip: params.ip.trim() || undefined,
      before_id: params.beforeId ?? undefined,
      limit: EVENTS_PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export function useSuspiciousIps(hours: number) {
  return useQuery<SuspiciousIpResponse>({
    queryKey: [...SECURITY_KEY, 'suspicious-ips', hours],
    queryFn: () => adminApi.securitySuspiciousIps({ hours }),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

export function useHighRisk(params: { days: number; category: HighRiskCategory | ''; beforeId: number | null }) {
  return useQuery<HighRiskResponse>({
    queryKey: [...SECURITY_KEY, 'high-risk', params],
    queryFn: () => adminApi.securityHighRisk({
      days: params.days,
      category: params.category || undefined,
      before_id: params.beforeId ?? undefined,
      limit: TIMELINE_PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/** session 總覽（後端另要 accounts.manage；呼叫端沒有該 scope 時傳 enabled=false，不送請求）。 */
export function useSecuritySessions(activeOnly: boolean, enabled: boolean) {
  return useQuery<SecuritySessionListResponse>({
    queryKey: [...SECURITY_KEY, 'sessions', activeOnly],
    queryFn: () => adminApi.securitySessions({ active_only: activeOnly }),
    enabled,
    retry: false,
  })
}

/**
 * 撤銷單一 session（後端要 accounts.manage＋已提升）。呼叫端以 `useElevationGate().guard` 包住 `mutateAsync`：
 * 收到 `elevation_required` 時彈出驗證框、驗證後自動重試。成功後重抓 session 清單、高風險時間線與操作紀錄
 * （撤銷與稽核 `session.admin_revoke` 同一筆交易）。
 */
export function useRevokeSession() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (sessionId: string) => adminApi.securityRevokeSession(sessionId),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: [...SECURITY_KEY, 'sessions'] }),
        client.invalidateQueries({ queryKey: [...SECURITY_KEY, 'high-risk'] }),
        client.invalidateQueries({ queryKey: AUDIT_KEY }),
      ])
    },
  })
}
