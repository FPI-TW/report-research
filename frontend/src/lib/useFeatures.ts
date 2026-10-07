import { useQuery } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import { z } from 'zod'
import { getJSON } from './api'

/**
 * 目前使用者的功能旗標實際值（`GET /api/features`，Admin v2）。
 *
 * 後端算好的是「環境變數上限 AND 管理員在功能開關頁設的覆寫（含角色／使用者作用域）」，這裡只拿 `{key: bool}`：
 * 不含上限、覆寫與名單。不在 `/api/admin` 底下，所以 zod 手寫（不在產生的 admin client 裡）。鍵與後端
 * `app/services/feature_flags.py` 的 `REGISTRY` 相同；不認得的鍵照收（後端新增旗標不必同步改前端）。
 *
 * **兩層**：`useFeatures()` 用 TanStack Query 抓（staleTime 60 秒、視窗回焦點時重抓，管理員改了開關最慢約一分鐘
 * 生效），結果寫進 module 級 store；`useFeature(key)` 只讀 store（useSyncExternalStore，不需要 QueryClient）。
 * 讀的元件（輸入框、訊息）散在各處、有些測試不包 QueryClientProvider，抓的那一次只放在頁面層（`AskPage`）。
 *
 * **還沒抓到或抓失敗＝全部當成關**：這些開關都是「多開一個功能」，讀不到時寧可少開，與後端
 * DB 讀不到時退回 registry 預設是兩回事——前端拿不到的是後端的答案，不是預設值。
 */
export const featuresResponseSchema = z.object({
  features: z.record(z.string(), z.boolean()),
})
export type Features = Record<string, boolean>

export const FEATURES_KEY = ['features'] as const

let current: Features | null = null
const listeners = new Set<() => void>()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): Features | null {
  return current
}

/** 寫入 store（`useFeatures` 抓到時呼叫；測試也用它模擬後端的答案，傳 null 還原成「還沒抓到」）。 */
export function setFeatures(next: Features | null): void {
  if (next === current) return
  current = next
  listeners.forEach((l) => l())
}

/** 單一旗標的實際值；還沒抓到、抓失敗或後端沒有這個鍵都是 false。 */
export function useFeature(key: string): boolean {
  const features = useSyncExternalStore(subscribe, getSnapshot, () => null)
  return features?.[key] ?? false
}

/** 抓 `/api/features` 並寫進 store。放在頁面層呼叫一次即可（需要 QueryClientProvider）。 */
export function useFeatures() {
  const query = useQuery<Features>({
    queryKey: FEATURES_KEY,
    queryFn: async () => (await getJSON('/api/features', featuresResponseSchema, { cache: 'no-store' })).features,
    staleTime: 60_000,
    retry: false,
  })
  useEffect(() => {
    if (query.data) setFeatures(query.data)
  }, [query.data])
  return query
}
