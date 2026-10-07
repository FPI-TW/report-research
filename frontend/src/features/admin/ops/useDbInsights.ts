import { useQuery } from '@tanstack/react-query'
import {
  adminApi, type DbOverviewResponse, type DbTrendResponse, type IncidentTrendsResponse, type SlowQueryResponse,
} from '../../../lib/generated/adminApi'
import { OPS_KEY } from './useOps'

export type TrendMetric = DbTrendResponse['metric']
export type SlowQuerySort = NonNullable<SlowQueryResponse['sort']>

/** 區間（天）→ since 的 ISO 字串。在 queryFn 裡算，query key 只帶天數（每次重抓都以「現在」為終點）。 */
function sinceDays(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString()
}

/** 即時快照（系統目錄，後端以短 statement_timeout 保護）。60 秒自動重抓。 */
export function useDbOverview() {
  return useQuery<DbOverviewResponse>({
    queryKey: [...OPS_KEY, 'db', 'overview'],
    queryFn: () => adminApi.getDbOverview(),
    refetchInterval: 60_000,
    retry: false,
  })
}

/** pg_stat_statements 的前 20 筆；不可用時後端回 200＋available=false。不自動重抓。 */
export function useDbSlowQueries(sort: SlowQuerySort) {
  return useQuery<SlowQueryResponse>({
    queryKey: [...OPS_KEY, 'db', 'slow', sort],
    queryFn: () => adminApi.listDbSlowQueries({ sort, limit: 20 }),
    retry: false,
  })
}

/** 單一指標的趨勢（db_stat_snapshot；起點在 30 天內逐時、否則每日）。`table_bytes` 沒選表時不送出。 */
export function useDbTrend(metric: TrendMetric, days: number, table: string) {
  return useQuery<DbTrendResponse>({
    queryKey: [...OPS_KEY, 'db', 'trend', metric, days, metric === 'table_bytes' ? table : ''],
    queryFn: () => adminApi.getDbTrends({
      metric, since: sinceDays(days), table: metric === 'table_bytes' ? table : undefined,
    }),
    enabled: metric !== 'table_bytes' || table !== '',
    retry: false,
  })
}

/** 事件趨勢（每週件數、MTTR、常見原因）與批次 90 天失敗率。資料最多 5 分鐘更新一次：5 分鐘重抓。 */
export function useIncidentTrends(days: number) {
  return useQuery<IncidentTrendsResponse>({
    queryKey: [...OPS_KEY, 'incidents', 'trends', days],
    queryFn: () => adminApi.getIncidentTrends({ since: sinceDays(days) }),
    refetchInterval: 300_000,
    retry: false,
  })
}
