import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { getJSON } from '../../lib/api'
import { adminApi, type OpsServiceListResponse } from '../../lib/generated/adminApi'
import { progressSchema, type Progress } from '../../lib/progressSchema'
import type { ReviewKind } from '../../lib/reviewSchemas'
import { deriveSignals, type AdminSignals } from './adminSignals'
import { isAgentUnavailable } from './ops/opsLabels'
import { OPS_KEY } from './ops/useOps'
import { SECURITY_KEY } from './security/useSecurity'
import { reviewCountQuery } from './useReviewQueue'

const MINUTE = 60_000
const REVIEW_KINDS: readonly ReviewKind[] = ['faithfulness', 'feedback', 'extraction']

/** 一個來源的狀態：off＝沒有 scope 或還沒啟用，不會送請求。 */
export type SourceState = 'off' | 'pending' | 'ready' | 'error'
export type SignalSource =
  | 'services' | 'dependencies' | 'pipeline' | 'incidents' | 'dataHealth' | 'security' | 'reviews' | 'uploads'

export interface UseAdminSignals {
  signals: AdminSignals
  /** 各來源讀到了沒有。總覽用它分辨「正常」與「還不知道」——沒亮燈不代表讀到了。 */
  sources: Record<SignalSource, SourceState>
  /** 次級面板底部的環境卡：環境・主機・檢查時間（服務清單帶回來的；沒有 ops.read 或代理不通時是 null）。 */
  env: Pick<OpsServiceListResponse, 'environment' | 'host' | 'checked_at'> | null
  services: OpsServiceListResponse | undefined
}

/**
 * 外殼導覽的燈號與數字（判準在 `adminSignals.ts`）。
 *
 * 每個來源只在有對應 scope 時才查——沒有 scope 的請求只會換來 403，不該送出去。`enabled` 是總開關：
 * 還不確定是不是管理員、或被 TOTP 強制擋著時，一個請求都不送。
 *
 * 一律每分鐘輪詢一次（依賴圖兩分鐘、資料健康五分鐘），分頁在背景時不輪詢（TanStack Query 預設）。服務清單、
 * 管線、告警與所在頁面共用 query key：同一份快取，頁面自己較密的輪詢照舊。
 * 讀取失敗不重試、也不亮燈；頁面本身會顯示錯誤。
 */
export function useAdminSignals(opts: { enabled: boolean; scopes: readonly string[] }): UseAdminSignals {
  const has = (scope: string) => opts.enabled && opts.scopes.includes(scope)
  const ops = has('ops.read')
  const reviewOn = has('review.manage')

  const services = useQuery({
    queryKey: [...OPS_KEY, 'services'],
    queryFn: () => adminApi.listOpsServices(),
    enabled: ops, refetchInterval: MINUTE, retry: false,
  })
  // 依賴圖只拿來看外部依賴的健康狀態；它也要經維運代理，輪詢比服務清單疏一倍。
  const dependencies = useQuery({
    queryKey: [...OPS_KEY, 'dependencies'],
    queryFn: () => adminApi.getOpsDependencies(),
    enabled: ops, refetchInterval: 2 * MINUTE, retry: false,
  })
  const progress = useQuery<Progress>({
    queryKey: ['progress'],
    queryFn: () => getJSON('/api/progress', progressSchema, { cache: 'no-store' }),
    enabled: ops, refetchInterval: MINUTE, retry: false,
  })
  const incidents = useQuery({
    queryKey: [...OPS_KEY, 'incidents', 'firing', '', 0],
    queryFn: () => adminApi.listIncidents({ status: 'firing', limit: 50, offset: 0 }),
    enabled: ops, refetchInterval: MINUTE, retry: false,
  })
  const dataHealth = useQuery({
    queryKey: [...OPS_KEY, 'data-health'],
    queryFn: () => adminApi.getDataHealth(),
    enabled: ops, refetchInterval: 5 * MINUTE, retry: false,
  })
  const alerts = useQuery({
    queryKey: [...SECURITY_KEY, 'alerts'],
    queryFn: () => adminApi.securityAlerts(),
    enabled: has('audit.read'), refetchInterval: MINUTE, retry: false,
  })
  const reviews = useQueries({
    queries: REVIEW_KINDS.map(kind => ({ ...reviewCountQuery(kind), enabled: reviewOn, refetchInterval: MINUTE })),
  })
  const drafts = useQuery({
    queryKey: ['admin', 'uploads', 'count', 'draft'],
    queryFn: () => adminApi.listUploads({ state: 'draft', limit: 1 }),
    enabled: has('reports.manage'), refetchInterval: MINUTE, staleTime: MINUTE, retry: false,
    select: d => d.total,
  })

  const [rvFaith, rvFeedback, rvExtraction] = reviews.map(q => q.data)
  const agentUnavailable = isAgentUnavailable(services.error)
  const signals = useMemo(() => deriveSignals({
    services: services.data,
    agentUnavailable,
    dependencies: dependencies.data,
    progress: progress.data,
    firingIncidents: incidents.data,
    dataHealth: dataHealth.data,
    securityAlerts: alerts.data,
    reviewOpen: { faithfulness: rvFaith, feedback: rvFeedback, extraction: rvExtraction },
    draftUploads: drafts.data,
  }), [services.data, agentUnavailable, dependencies.data, progress.data, incidents.data, dataHealth.data, alerts.data,
    rvFaith, rvFeedback, rvExtraction, drafts.data])

  const state = (on: boolean, q: { isSuccess: boolean; isError: boolean }): SourceState =>
    (!on ? 'off' : q.isSuccess ? 'ready' : q.isError ? 'error' : 'pending')
  const sources: Record<SignalSource, SourceState> = {
    services: state(ops, services),
    dependencies: state(ops, dependencies),
    pipeline: state(ops, progress),
    incidents: state(ops, incidents),
    dataHealth: state(ops, dataHealth),
    security: state(has('audit.read'), alerts),
    reviews: state(reviewOn, {
      isSuccess: reviews.some(q => q.isSuccess), isError: reviews.every(q => q.isError),
    }),
    uploads: state(has('reports.manage'), drafts),
  }

  const s = services.data
  const env = useMemo(
    () => (s ? { environment: s.environment, host: s.host, checked_at: s.checked_at } : null),
    [s],
  )
  return { signals, sources, env, services: s }
}
