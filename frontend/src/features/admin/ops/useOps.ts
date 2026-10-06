import { useQuery } from '@tanstack/react-query'
import {
  adminApi, type OpsLogsResponse, type OpsServiceDetail, type OpsServiceListResponse,
} from '../../../lib/generated/adminApi'

export const OPS_KEY = ['admin', 'ops'] as const

/** 服務清單與狀態（維運代理一次 systemctl show＋docker inspect）。30 秒自動重抓。 */
export function useOpsServices() {
  return useQuery<OpsServiceListResponse>({
    queryKey: [...OPS_KEY, 'services'],
    queryFn: () => adminApi.listOpsServices(),
    refetchInterval: 30_000,
    retry: false,
  })
}

export function useOpsService(name: string) {
  return useQuery<OpsServiceDetail>({
    queryKey: [...OPS_KEY, 'service', name],
    queryFn: () => adminApi.getOpsService(name),
    enabled: name !== '',
    retry: false,
  })
}

/** 最近日誌；不自動重抓（日誌頁有「重新整理」）。 */
export function useOpsLogs(name: string, since: string, lines: number) {
  return useQuery<OpsLogsResponse>({
    queryKey: [...OPS_KEY, 'logs', name, since, lines],
    queryFn: () => adminApi.getOpsServiceLogs(name, { since, lines }),
    enabled: name !== '',
    retry: false,
  })
}
