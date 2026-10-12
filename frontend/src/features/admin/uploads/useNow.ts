import { useEffect, useState } from 'react'

/** 現在時間（毫秒），每 `intervalMs` 更新一次：退回寬限期的倒數用。 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}
