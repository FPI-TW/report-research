import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { getJSON } from './api'

/**
 * 一般使用者看的粗粒度系統狀態（`GET /api/status`，任何登入使用者可讀）。
 *
 * 後端刻意只回 `{status, message}`：不含服務清單、主機或錯誤細節（那些在管理後台的維運頁）。
 * 取不到（網路錯、格式不符）一律當成 `unknown`——小燈號是資訊，不該因為它自己壞了而打擾人。
 */
export const systemStatusSchema = z.object({
  status: z.enum(['ok', 'degraded', 'unknown']),
  message: z.string(),
})
export type SystemStatus = z.infer<typeof systemStatusSchema>
export type SystemStatusLevel = SystemStatus['status']

export const STATUS_LABELS: Record<SystemStatusLevel, string> = {
  ok: '正常',
  degraded: '部分異常',
  unknown: '狀態未知',
}

const FALLBACK: SystemStatus = { status: 'unknown', message: '暫時無法取得系統狀態' }

export function useSystemStatus(): SystemStatus {
  const q = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: () => getJSON('/api/status', systemStatusSchema, { cache: 'no-store' }),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
    retry: false,
  })
  return q.data ?? FALLBACK
}
