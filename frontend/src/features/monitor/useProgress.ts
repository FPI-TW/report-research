import { useQuery, keepPreviousData, type UseQueryResult } from '@tanstack/react-query'
import { getJSON } from '../../lib/api'
import { progressSchema, type Progress } from './progressSchema'

/** 每 5 秒輪詢 /api/progress；keepPreviousData 於重抓/失敗時保留上一筆，避免閃爍。 */
export function useProgress(): UseQueryResult<Progress> {
  return useQuery<Progress>({
    queryKey: ['progress'],
    queryFn: () => getJSON('/api/progress', progressSchema, { cache: 'no-store' }),
    refetchInterval: 5000,
    placeholderData: keepPreviousData,
    retry: false,
  })
}
