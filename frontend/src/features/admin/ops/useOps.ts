import { useQuery } from '@tanstack/react-query'
import {
  adminApi, type DataHealthResponse, type IncidentDetail, type IncidentItem, type IncidentListResponse, type JobItem,
  type JobListResponse, type LlmUsageResponse, type ObservationListResponse, type OpsDependencyGraph,
  type OpsLogsResponse,
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

/** 依賴圖（catalog 的依賴＋各節點狀態，後端算好受影響的下游）。與服務清單同樣 30 秒自動重抓。 */
export function useOpsDependencies() {
  return useQuery<OpsDependencyGraph>({
    queryKey: [...OPS_KEY, 'dependencies'],
    queryFn: () => adminApi.getOpsDependencies(),
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

export const INCIDENTS_PAGE_SIZE = 50

/**
 * 事件清單（P5 狀態轉換的 DB 投影，loader 每 5 分鐘匯入；後端預設最近 30 天）。不經維運代理。
 * 空字串＝不篩選；60 秒自動重抓。
 */
export function useOpsIncidents(status: IncidentItem['status'] | '', component: string, offset: number) {
  return useQuery<IncidentListResponse>({
    queryKey: [...OPS_KEY, 'incidents', status, component, offset],
    queryFn: () => adminApi.listIncidents({
      status: status || undefined, component: component || undefined, limit: INCIDENTS_PAGE_SIZE, offset,
    }),
    refetchInterval: 60_000,
    retry: false,
  })
}

/** 單一事件與它的狀態轉換（含 journal 片段）。不自動重抓。 */
export function useOpsIncident(incidentId: string) {
  return useQuery<IncidentDetail>({
    queryKey: [...OPS_KEY, 'incident', incidentId],
    queryFn: () => adminApi.getIncident(incidentId),
    enabled: incidentId !== '',
    retry: false,
  })
}

/**
 * 資料健康（批次新鮮度即時判讀＋稽核與 R2 對帳的最後一次結果）。後端整份快取 60 秒，這裡不自動重抓
 * （稽核每日、對帳每週才更新一次）；頁面有「重新整理」。
 */
export function useDataHealth() {
  return useQuery<DataHealthResponse>({
    queryKey: [...OPS_KEY, 'data-health'],
    queryFn: () => adminApi.getDataHealth(),
    retry: false,
  })
}

/** 最近 `days` 天的批次 LLM 用量。`since` 在 queryFn 裡才算，query key 只帶天數（免得每次 render 換 key）。 */
export function useLlmUsage(days: number) {
  return useQuery<LlmUsageResponse>({
    queryKey: [...OPS_KEY, 'llm-usage', days],
    queryFn: () => adminApi.getLlmUsage({ since: new Date(Date.now() - days * 86_400_000).toISOString() }),
    retry: false,
  })
}
