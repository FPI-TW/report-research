import { useQuery } from '@tanstack/react-query'
import { getJSON } from './api'
import { meSchema, type Me } from './adminSchemas'

/**
 * 目前登入身分（`GET /api/me`）。
 *
 * 只拿來決定**顯示**：要不要露出「管理」入口、管理頁要不要渲染。授權一律由後端判斷
 * （`/api/admin/*`、`/api/review/*` 對一般使用者回 403）——前端被怎麼改都拿不到資料。
 * 角色可能被管理員即時改掉，所以 staleTime 不設太長；失敗時 `data` 為 undefined＝當成非管理員。
 */
export function useMe() {
  return useQuery<Me>({
    queryKey: ['me'],
    queryFn: () => getJSON('/api/me', meSchema, { cache: 'no-store' }),
    staleTime: 60_000,
    retry: false,
  })
}

export function useIsAdmin(): boolean {
  return useMe().data?.role === 'admin'
}
