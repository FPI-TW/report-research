import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { adminApi } from '../../../../lib/generated/adminApi'
import { OPS_KEY } from '../useOps'

/**
 * 這台主機的 Service Catalog 允許「立即執行」的服務名。白名單在主機上（`deploy/ops/services.*.toml`），
 * 每台不同：例如正式環境的 r2-reconcile 沒有開放 run。清單經維運代理取得，和外殼導覽、服務頁共用同一份快取；
 * `enabled` 為 false（沒有 `ops.operate`）時不送請求。代理不可用或還沒讀到時回空集合——不顯示按鈕，
 * 而不是顯示一顆按了一定失敗的按鈕。
 */
export function useRunnableServices(enabled: boolean): ReadonlySet<string> {
  const q = useQuery({
    queryKey: [...OPS_KEY, 'services'],
    queryFn: () => adminApi.listOpsServices(),
    enabled,
    staleTime: 30_000,
    retry: false,
  })
  return useMemo(
    () => new Set((q.data?.items ?? []).filter(s => s.actions.includes('run')).map(s => s.name)),
    [q.data],
  )
}
