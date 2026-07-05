import { useEffect, useState } from 'react'

function fmt(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 本地牆鐘（HH:MM:SS），每秒更新；不吃任何請求。 */
export function useClock(): string {
  const [now, setNow] = useState(() => fmt(new Date()))
  useEffect(() => {
    const id = setInterval(() => setNow(fmt(new Date())), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}
