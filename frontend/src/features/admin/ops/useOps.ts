import { useQuery } from '@tanstack/react-query'
import {
  adminApi, type JobItem, type JobListResponse, type ObservationListResponse, type OpsLogsResponse,
  type OpsServiceDetail, type OpsServiceListResponse,
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

export const JOBS_PAGE_SIZE = 50

/**
 * 排程工作的執行紀錄（DB 投影，loader 每 5 分鐘匯入；後端預設最近 7 天）。不經維運代理。
 * `state` 空字串＝全部；60 秒自動重抓（資料本身最多 5 分鐘才更新一次）。
 */
export function useOpsJobs(state: JobItem['state'] | '', offset: number) {
  return useQuery<JobListResponse>({
    queryKey: [...OPS_KEY, 'jobs', state, offset],
    queryFn: () => adminApi.listJobs({ state: state || undefined, limit: JOBS_PAGE_SIZE, offset }),
    refetchInterval: 60_000,
    retry: false,
  })
}

/** 主機觀測（scope=host，最近 1 小時，DB 投影）。一小時約 1200 筆，上限 5000 綽綽有餘。 */
export function useHostObservations() {
  return useQuery<ObservationListResponse>({
    queryKey: [...OPS_KEY, 'observations', 'host'],
    queryFn: () => adminApi.listObservations({ scope: 'host', limit: 5000 }),
    refetchInterval: 60_000,
    retry: false,
  })
}
