import { useEffect, useRef, useState } from 'react'
import { NULL_RATE, nextRate, type MonitorRate, type RateBase, type RateSample } from './rate'

/**
 * 開頁平均速率 hook：每次收到「新的 sample 物件參照」時，於 effect 內以 performance.now()
 * 計算速率並存入 state（base/last 以 ref 保存）。比 render 期計算延遲一個 render，對 2 秒
 * 輪詢的監控頁可忽略，且符合 React 19 純度規則。
 *
 * 注意：呼叫端必須以 useMemo 穩定 sample 物件（僅在資料變動時換參照），否則 effect 會在每次
 * render 觸發。
 */
export function useMonitorRate(sample: RateSample | null): MonitorRate {
  const baseRef = useRef<RateBase | null>(null)
  const lastRef = useRef<MonitorRate>(NULL_RATE)
  const [rate, setRate] = useState<MonitorRate>(NULL_RATE)

  useEffect(() => {
    if (sample == null) return
    const out = nextRate(baseRef.current, lastRef.current, sample, performance.now())
    baseRef.current = out.base
    lastRef.current = out.rate
    setRate(out.rate)
  }, [sample])

  return rate
}
