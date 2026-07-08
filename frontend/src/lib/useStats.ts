import { useQuery } from '@tanstack/react-query'
import { getJSON } from './api'
import { statsSchema, type StatsResponse } from './schemas'

export function useStats() {
  return useQuery<StatsResponse>({
    queryKey: ['stats'],
    queryFn: () => getJSON('/api/stats', statsSchema, { cache: 'no-store' }),
    staleTime: 60_000,
  })
}
