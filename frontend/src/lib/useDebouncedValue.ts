import { useEffect, useState } from 'react'

/** 值停止變動 `delayMs` 之後才更新；用來讓逐字輸入不要每個字都打一次 API。 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(id)
  }, [value, delayMs])
  return debounced
}
