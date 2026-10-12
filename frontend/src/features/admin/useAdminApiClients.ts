import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  adminApi, type ApiClientEntitlements, type ApiClientItem, type UpdateApiClientRequest,
} from '../../lib/generated/adminApi'
import { AUDIT_KEY } from './useAdmin'

export type AdminApiClient = ApiClientItem
export type ApiClientScope = ApiClientItem['scopes'][number]
export type ApiClientPatch = Omit<UpdateApiClientRequest, 'scopes'> & { scopes?: ApiClientScope[] }

export const API_CLIENTS_KEY = ['admin', 'api-clients'] as const

/** API 用戶端清單（產生的 adminApi）。不含金鑰與 hash；錯誤走 requestJSON，403 的 detail 原樣顯示。 */
export function useApiClients() {
  return useQuery<AdminApiClient[]>({
    queryKey: API_CLIENTS_KEY,
    queryFn: async () => (await adminApi.listApiClients()).items,
    retry: false,
  })
}

/**
 * API 用戶端的寫入。每一種成功後都重抓清單與稽核：後端在同一筆交易裡改資料並寫稽核。
 *
 * 建立與輪替金鑰要已提升權限，由呼叫端以 `useElevationGate().guard` 包住 `mutateAsync`。
 * 這兩個的回應帶**原始金鑰**（只此一次）：`gcTime: 0` 讓 mutation 一沒有觀察者就從快取清掉，
 * 呼叫端拿到金鑰後也要 `reset()`，金鑰只活在一次性對話框的 state 裡。
 */
export function useApiClientActions() {
  const client = useQueryClient()
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: API_CLIENTS_KEY }),
      client.invalidateQueries({ queryKey: AUDIT_KEY }),
    ])
  }
  const create = useMutation({
    mutationFn: (body: {
      name: string; scopes: ApiClientScope[]; rate_limit_per_min: number; daily_quota: number
      entitlements: ApiClientEntitlements; note?: string | null
    }) => adminApi.createApiClient(body),
    onSuccess: refresh,
    gcTime: 0,
  })
  const update = useMutation({
    mutationFn: ({ id, ...patch }: { id: number } & ApiClientPatch) => adminApi.updateApiClient(String(id), patch),
    onSuccess: refresh,
  })
  const entitlements = useMutation({
    mutationFn: ({ id, ...body }: { id: number } & ApiClientEntitlements) =>
      adminApi.replaceApiClientEntitlements(String(id), body),
    onSuccess: refresh,
  })
  const rotate = useMutation({
    mutationFn: (id: number) => adminApi.rotateApiClientKey(String(id)),
    onSuccess: refresh,
    gcTime: 0,
  })
  return { create, update, entitlements, rotate }
}
