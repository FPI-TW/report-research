import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

/**
 * 把一個狀態放在 URL query（`?service=web`、`?review=3f9a2c1e`、`?tab=draft`），連結才能分享、重新整理也還在。
 * 抽屜、詳情、分頁籤都用它。設成 null（或空字串）就把參數拿掉；其他參數原樣保留。
 * 打開詳情會多一筆瀏覽紀錄（按上一頁就關），關閉則取代目前這一筆。
 */
export function useQueryParam(key: string): [string | null, (value: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const value = params.get(key)
  const set = useCallback((next: string | null) => {
    setParams(prev => {
      const p = new URLSearchParams(prev)
      if (next) p.set(key, next)
      else p.delete(key)
      return p
    }, { replace: !next })
  }, [key, setParams])
  return [value, set]
}
