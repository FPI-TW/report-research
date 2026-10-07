import { useQuery, keepPreviousData, type UseQueryResult } from '@tanstack/react-query'
import { getJSON } from '../../../../lib/api'
import { progressSchema, type Progress } from '../../../../lib/progressSchema'

/**
 * 輪詢間隔對齊後端的 DB 快取（`web/routers/monitor.py` 的 15 秒）：舊監控頁每 5 秒一次，
 * 三次裡有兩次拿到的是同一份快取。分頁在背景時不輪詢（TanStack Query 預設
 * `refetchIntervalInBackground: false`，這裡寫明是刻意的）。
 */
export const PROGRESS_POLL_MS = 15_000

/** 輪詢 /api/progress；keepPreviousData 於重抓/失敗時保留上一筆，避免閃爍。 */
export function useProgress(): UseQueryResult<Progress> {
  return useQuery<Progress>({
    queryKey: ['progress'],
    queryFn: () => getJSON('/api/progress', progressSchema, { cache: 'no-store' }),
    refetchInterval: PROGRESS_POLL_MS,
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
    retry: false,
  })
}
