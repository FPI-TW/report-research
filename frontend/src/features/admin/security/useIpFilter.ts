import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useDebouncedValue } from '../../../lib/useDebouncedValue'

const KEY = 'ip'

/**
 * 登入事件的 IP 篩選，套用中的值放在 URL query（`?ip=203.0.113.9`），連結才能分享。
 *
 * 兩個入口寫同一個參數：
 * - 輸入框逐字輸入：停手 400ms 才套用（不要每個字都打一次 API），用 replace，不堆瀏覽紀錄。
 * - 可疑 IP 的「看事件」與標籤上的清除：立即套用；「看事件」多一筆瀏覽紀錄（按上一頁就回到沒篩選）。
 *
 * `draft` 是輸入框裡的字；URL 被別處改掉（看事件、清除、上一頁）時跟著改。
 */
export function useIpFilter() {
  const [params, setParams] = useSearchParams()
  const applied = params.get(KEY) ?? ''
  const [draft, setDraft] = useState(applied)
  const [synced, setSynced] = useState(applied)
  if (synced !== applied) {
    setSynced(applied)
    setDraft(applied)
  }

  const write = useCallback((ip: string, replace: boolean) => {
    setParams(prev => {
      const p = new URLSearchParams(prev)
      if (ip) p.set(KEY, ip)
      else p.delete(KEY)
      return p
    }, { replace })
  }, [setParams])

  const settled = useDebouncedValue(draft, 400)
  useEffect(() => {
    // settled !== draft：還在打字，或 URL 剛被別處改掉而 debounce 還沒追上——都不要寫回去。
    if (settled !== draft) return
    const next = settled.trim()
    if (next !== applied) write(next, true)
  }, [settled, draft, applied, write])

  const pick = useCallback((ip: string) => write(ip, false), [write])
  const clear = useCallback(() => write('', true), [write])
  return { applied, draft, setDraft, pick, clear }
}
