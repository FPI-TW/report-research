import { useQuery } from '@tanstack/react-query'
import { adminApi, type FlagListResponse } from '../../../lib/generated/adminApi'

export const FLAGS_KEY = ['admin', 'flags'] as const

/** 功能旗標清單（`GET /api/admin/flags`，ops.read）。寫入後呼叫端 invalidate 這個 key。 */
export function useFlags() {
  return useQuery<FlagListResponse>({
    queryKey: FLAGS_KEY,
    queryFn: () => adminApi.listFlags(),
    retry: false,
  })
}
