import { useQueries, useQuery } from '@tanstack/react-query'
import {
  adminApi, type DataHealthResponse, type IncidentDetail, type IncidentItem, type IncidentListResponse, type JobItem,
  type JobListResponse, type LlmUsageResponse, type ObservationListResponse, type OpsDependencyGraph,
  type OpsLogsResponse, type OpsRemoteHostList,
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

/**
 * 這台監看的來源主機（另一台主機發布到 R2 的信標；不經維運代理）。後端快取 60 秒，這裡每分鐘重抓。
 * 沒設定要監看時 items 是空的。
 */
export function useOpsRemoteHosts() {
  return useQuery<OpsRemoteHostList>({
    queryKey: [...OPS_KEY, 'remote-hosts'],
    queryFn: () => adminApi.listOpsRemoteHosts(),
    refetchInterval: 60_000,
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

/**
 * 單次執行（分享連結用）：後端沒有取單筆的端點，改用清單端點查該 unit 在開始時間前後一小段的紀錄，再用
 * invocation_id 找出那一筆；查得到資料但沒有這一筆時是 null。`locator` 是 null 時不查。
 * 與清單的篩選、分頁無關（另一個 query key）。還在執行中的那一筆比照清單每 60 秒重抓，其餘不輪詢。
 */
export function useOpsJobLookup(invocationId: string, locator: { unit: string; since: string; until: string } | null) {
  return useQuery<JobItem | null>({
    queryKey: [...OPS_KEY, 'job', invocationId, locator?.unit, locator?.since, locator?.until],
    queryFn: async () => {
      if (!locator) return null
      const res = await adminApi.listJobs({ ...locator, limit: JOBS_PAGE_SIZE })
      return res.items.find(j => j.invocation_id === invocationId) ?? null
    },
    enabled: locator != null,
    refetchInterval: query => (query.state.data?.state === 'running' ? 60_000 : false),
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

/** 主機趨勢圖比 1 小時長的範圍（1 小時那一份直接用 `useHostObservations` 的資料，不另外查）。 */
export type HostTrendRange = '24h' | '7d'

const HOST_TREND_MS: Record<HostTrendRange, number> = { '24h': 86_400_000, '7d': 7 * 86_400_000 }
/**
 * 後端依 `since` 距**它的**現在多久選粒度（24 小時內逐筆、7 天內 5 分鐘聚合），剛好壓在邊界上的 `since` 送到時
 * 已經過了邊界，會掉到粗一級。往內縮 5 分鐘，瀏覽器時鐘慢一點也還在邊界內；真的掉下去也只是粒度變粗，
 * 畫面照回應的 `resolution` 標示。
 */
const HOST_TREND_MARGIN_MS = 5 * 60_000

export interface HostSeriesKey { subject: string; metric: string }

/**
 * 主機趨勢圖的長範圍資料：**一條序列一個請求**（`subject`＋`metric`），24 小時逐筆約 1440 筆、7 天的 5 分鐘桶
 * 約 2016 筆，都在上限 5000 以內；整包一起查會被截斷。`range` 是 null 時不查。
 * 資料每 5 分鐘才匯入一次，所以 5 分鐘內不重抓，也不輪詢（最新值由 `useHostObservations` 負責）。
 */
export function useHostTrends(range: HostTrendRange | null, series: readonly HostSeriesKey[]) {
  return useQueries({
    queries: range == null ? [] : series.map(s => ({
      queryKey: [...OPS_KEY, 'observations', 'host-trend', range, s.subject, s.metric],
      queryFn: (): Promise<ObservationListResponse> => adminApi.listObservations({
        scope: 'host', subject: s.subject, metric: s.metric, limit: 5000,
        since: new Date(Date.now() - HOST_TREND_MS[range] + HOST_TREND_MARGIN_MS).toISOString(),
      }),
      staleTime: 5 * 60_000,
      retry: false,
    })),
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
