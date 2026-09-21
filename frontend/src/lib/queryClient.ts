import { QueryClient } from '@tanstack/react-query'

/**
 * 全站 query 預設的 staleTime。
 *
 * TanStack Query 的出廠值是 0：每次元件掛載、每次視窗重新取得焦點都會重抓。對這個站來說
 * 那是純浪費——語料每 3 小時才由 sync 批次更新一次，而會即時變動的資料各自有自己的機制
 * （監控頁 `refetchInterval`、對話串在送出／刪除後 `invalidateQueries`），都不受 staleTime 影響。
 *
 * 30 秒只是「切個分頁回來不要重抓」的下限。資料更冷的 query 自己設更長的值
 * （檢索結果、閱讀頁、簡報），這裡不代它們決定。
 */
export const DEFAULT_STALE_TIME_MS = 30_000

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: DEFAULT_STALE_TIME_MS } },
  })
}
